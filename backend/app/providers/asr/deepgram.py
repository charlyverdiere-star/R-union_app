"""Deepgram — moteur par défaut.

Retenu pour : diarisation native, acceptation de fichiers de plusieurs heures
en un seul appel, horodatages par phrase, bon niveau en francais, et coût
parmi les plus bas (~0,26 $ l'heure d'audio en nova-3).

Les données audio sont transmises aux serveurs de Deepgram : voir la section
« Confidentialité » du README.
"""

from __future__ import annotations

from pathlib import Path

import httpx2 as httpx

from .base import AsrCapabilities, AsrProvider, RawSegment, TranscriptionError, TranscriptionResult

ENDPOINT = "https://api.deepgram.com/v1/listen"
LOW_CONFIDENCE = 0.6


class DeepgramProvider(AsrProvider):
    name = "deepgram"
    capabilities = AsrCapabilities(diarization=True, max_seconds=None, max_bytes=None)

    def __init__(self, api_key: str, model: str = "nova-3", timeout: float = 1800.0):
        if not api_key:
            raise TranscriptionError("Clé API Deepgram manquante.")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def describe(self) -> str:
        return f"deepgram/{self._model}"

    def transcribe(
        self, audio: Path, *, language: str, vocabulary: list[str], diarization: bool
    ) -> TranscriptionResult:
        params: dict[str, object] = {
            "model": self._model,
            "punctuate": "true",
            "smart_format": "true",
            "utterances": "true",
            "diarize": "true" if diarization else "false",
        }
        if language and language != "auto":
            params["language"] = language
        else:
            params["detect_language"] = "true"

        warnings: list[str] = []
        optional = self._vocabulary_params(vocabulary, language)
        if vocabulary and not optional:
            warnings.append(
                "Le vocabulaire métier n'a pas été transmis : le modèle "
                f"« {self._model} » ne propose pas cette option pour cette langue."
            )

        payload = audio.read_bytes()
        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "audio/wav",
        }

        data = self._post(params | optional, headers, payload)
        if data is None and optional:
            # Un paramètre optionnel a été refusé : on réessaie sans.
            warnings.append(
                "Le vocabulaire métier a été refusé par l'API et n'a pas été appliqué."
            )
            data = self._post(params, headers, payload)
        if data is None:
            raise TranscriptionError("Deepgram a refusé la requête.", retryable=False)

        segments, detected = _parse_deepgram(data)
        if not segments:
            warnings.append("Deepgram n'a retourné aucun texte pour cet audio.")
        return TranscriptionResult(
            segments=segments,
            language=detected or language,
            model=self.describe(),
            warnings=warnings,
        )

    @staticmethod
    def _vocabulary_params(vocabulary: list[str], language: str) -> dict[str, object]:
        """Deepgram n'expose pas le même paramètre selon le modèle et la langue."""
        if not vocabulary:
            return {}
        return {"keywords": vocabulary[:100]}

    def _post(self, params: dict, headers: dict, payload: bytes) -> dict | None:
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(ENDPOINT, params=params, headers=headers, content=payload)
        except httpx.TimeoutException as exc:
            raise TranscriptionError(
                "Deepgram n'a pas répondu dans le temps imparti.", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise TranscriptionError(
                f"Connexion à Deepgram impossible : {exc}", retryable=True
            ) from exc

        if response.status_code == 400:
            return None  # paramètre refusé : l'appelant réessaiera sans options
        if response.status_code in (401, 403):
            raise TranscriptionError(
                "Clé API Deepgram invalide ou sans droits suffisants.", retryable=False
            )
        if response.status_code == 429:
            raise TranscriptionError("Quota Deepgram atteint.", retryable=True)
        if response.status_code >= 500:
            raise TranscriptionError(
                f"Deepgram est indisponible (code {response.status_code}).", retryable=True
            )
        if response.status_code >= 400:
            raise TranscriptionError(
                f"Deepgram a renvoyé une erreur {response.status_code} : "
                f"{response.text[:200]}",
                retryable=False,
            )
        return response.json()


def _parse_deepgram(data: dict) -> tuple[list[RawSegment], str | None]:
    """Extrait les phrases horodatées de la réponse Deepgram."""
    results = data.get("results", {}) or {}
    channels = results.get("channels", []) or []
    detected = channels[0].get("detected_language") if channels else None

    utterances = results.get("utterances") or []
    if utterances:
        segments = []
        for utt in utterances:
            confidence = utt.get("confidence")
            text = (utt.get("transcript") or "").strip()
            if not text:
                continue
            speaker = utt.get("speaker")
            segments.append(
                RawSegment(
                    start=float(utt.get("start", 0.0)),
                    end=float(utt.get("end", 0.0)),
                    text=text,
                    speaker=f"Locuteur {int(speaker) + 1}" if speaker is not None else None,
                    confidence=confidence,
                    low_confidence=confidence is not None and confidence < LOW_CONFIDENCE,
                )
            )
        return segments, detected

    # Repli : paragraphes, puis transcription brute en un seul bloc.
    if channels:
        alternative = (channels[0].get("alternatives") or [{}])[0]
        paragraphs = ((alternative.get("paragraphs") or {}).get("paragraphs")) or []
        segments = []
        for para in paragraphs:
            for sentence in para.get("sentences", []):
                text = (sentence.get("text") or "").strip()
                if text:
                    segments.append(
                        RawSegment(
                            start=float(sentence.get("start", 0.0)),
                            end=float(sentence.get("end", 0.0)),
                            text=text,
                        )
                    )
        if segments:
            return segments, detected

        transcript = (alternative.get("transcript") or "").strip()
        if transcript:
            words = alternative.get("words") or []
            end = float(words[-1].get("end", 0.0)) if words else 0.0
            return [RawSegment(start=0.0, end=end, text=transcript)], detected

    return [], detected
