"""Whisper hébergé par OpenAI.

Pas de diarisation, et une limite de 25 Mo par requête : le pipeline découpe
donc l'audio en segments d'environ dix minutes. L'audio est transmis aux
serveurs d'OpenAI.
"""

from __future__ import annotations

from pathlib import Path

import httpx2 as httpx

from .base import AsrCapabilities, AsrProvider, RawSegment, TranscriptionError, TranscriptionResult

ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
MAX_BYTES = 25 * 1024 * 1024
#: WAV 16 kHz mono 16 bits = 32 ko/s. On garde une marge sous les 25 Mo.
MAX_SECONDS = 700.0


class OpenAIWhisperProvider(AsrProvider):
    name = "openai"
    capabilities = AsrCapabilities(
        diarization=False, max_seconds=MAX_SECONDS, max_bytes=MAX_BYTES
    )

    def __init__(self, api_key: str, model: str = "whisper-1", timeout: float = 1800.0):
        if not api_key:
            raise TranscriptionError("Clé API OpenAI manquante.")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def describe(self) -> str:
        return f"openai/{self._model}"

    def transcribe(
        self, audio: Path, *, language: str, vocabulary: list[str], diarization: bool
    ) -> TranscriptionResult:
        size = audio.stat().st_size
        if size > MAX_BYTES:
            raise TranscriptionError(
                f"Segment de {size // (1024 * 1024)} Mo : la limite OpenAI est de 25 Mo.",
                retryable=False,
            )

        data: dict[str, str] = {
            "model": self._model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "segment",
        }
        if language and language != "auto":
            data["language"] = language
        if vocabulary:
            # Le « prompt » oriente l'orthographe des noms propres et acronymes.
            data["prompt"] = ", ".join(vocabulary[:200])

        try:
            with httpx.Client(timeout=self._timeout) as client:
                with audio.open("rb") as handle:
                    response = client.post(
                        ENDPOINT,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        data=data,
                        files={"file": (audio.name, handle, "audio/wav")},
                    )
        except httpx.TimeoutException as exc:
            raise TranscriptionError(
                "OpenAI n'a pas répondu dans le temps imparti.", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise TranscriptionError(
                f"Connexion à OpenAI impossible : {exc}", retryable=True
            ) from exc

        if response.status_code in (401, 403):
            raise TranscriptionError("Clé API OpenAI invalide.", retryable=False)
        if response.status_code == 429:
            raise TranscriptionError("Quota OpenAI atteint.", retryable=True)
        if response.status_code >= 500:
            raise TranscriptionError(
                f"OpenAI est indisponible (code {response.status_code}).", retryable=True
            )
        if response.status_code >= 400:
            raise TranscriptionError(
                f"OpenAI a renvoyé une erreur {response.status_code} : {response.text[:200]}",
                retryable=False,
            )

        payload = response.json()
        segments = _parse_whisper_segments(payload.get("segments") or [])
        if not segments and (payload.get("text") or "").strip():
            segments = [RawSegment(start=0.0, end=0.0, text=payload["text"].strip())]
        return TranscriptionResult(
            segments=segments,
            language=payload.get("language") or language,
            model=self.describe(),
        )


def _parse_whisper_segments(raw: list[dict]) -> list[RawSegment]:
    """Whisper ne fournit pas de score de confiance : on le dérive de
    `avg_logprob` et `no_speech_prob`, les deux indicateurs qu'il expose."""
    segments: list[RawSegment] = []
    for item in raw:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        avg_logprob = item.get("avg_logprob")
        no_speech = item.get("no_speech_prob")
        segments.append(
            RawSegment(
                start=float(item.get("start", 0.0)),
                end=float(item.get("end", 0.0)),
                text=text,
                confidence=_logprob_to_confidence(avg_logprob),
                low_confidence=is_low_confidence(avg_logprob, no_speech),
            )
        )
    return segments


def is_low_confidence(avg_logprob: float | None, no_speech_prob: float | None) -> bool:
    if avg_logprob is not None and avg_logprob < -0.9:
        return True
    if no_speech_prob is not None and no_speech_prob > 0.5:
        return True
    return False


def _logprob_to_confidence(avg_logprob: float | None) -> float | None:
    """Projection indicative de la log-probabilité moyenne sur [0, 1]."""
    if avg_logprob is None:
        return None
    import math

    return round(min(1.0, max(0.0, math.exp(avg_logprob))), 3)
