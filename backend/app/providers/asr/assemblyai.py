"""AssemblyAI — alternative avec diarisation native.

Fonctionnement en trois temps : téléversement du fichier, création d'un travail
de transcription, puis attente du résultat. L'audio est transmis aux serveurs
d'AssemblyAI.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx2 as httpx

from .base import AsrCapabilities, AsrProvider, RawSegment, TranscriptionError, TranscriptionResult

BASE_URL = "https://api.assemblyai.com/v2"
LOW_CONFIDENCE = 0.6
POLL_INTERVAL = 5.0


class AssemblyAIProvider(AsrProvider):
    name = "assemblyai"
    capabilities = AsrCapabilities(diarization=True, max_seconds=None, max_bytes=None)

    def __init__(self, api_key: str, timeout: float = 1800.0, max_wait: float = 7200.0):
        if not api_key:
            raise TranscriptionError("Clé API AssemblyAI manquante.")
        self._headers = {"authorization": api_key}
        self._timeout = timeout
        self._max_wait = max_wait

    def transcribe(
        self, audio: Path, *, language: str, vocabulary: list[str], diarization: bool
    ) -> TranscriptionResult:
        with httpx.Client(timeout=self._timeout, headers=self._headers) as client:
            upload_url = self._upload(client, audio)
            request: dict[str, object] = {
                "audio_url": upload_url,
                "speaker_labels": bool(diarization),
                "punctuate": True,
                "format_text": True,
            }
            if language and language != "auto":
                request["language_code"] = language
            else:
                request["language_detection"] = True
            if vocabulary:
                request["word_boost"] = vocabulary[:1000]

            job = self._request(client, "POST", "/transcript", json=request)
            data = self._wait(client, job["id"])

        segments = _parse_assemblyai(data)
        warnings: list[str] = []
        if not segments:
            warnings.append("AssemblyAI n'a retourné aucun texte pour cet audio.")
        return TranscriptionResult(
            segments=segments,
            language=data.get("language_code") or language,
            model=self.name,
            warnings=warnings,
        )

    def _upload(self, client: httpx.Client, audio: Path) -> str:
        try:
            response = client.post(f"{BASE_URL}/upload", content=audio.read_bytes())
        except httpx.HTTPError as exc:
            raise TranscriptionError(
                f"Téléversement vers AssemblyAI impossible : {exc}", retryable=True
            ) from exc
        _raise_for_status(response, "AssemblyAI")
        return response.json()["upload_url"]

    def _request(self, client: httpx.Client, method: str, path: str, **kwargs) -> dict:
        try:
            response = client.request(method, f"{BASE_URL}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise TranscriptionError(
                f"Connexion à AssemblyAI impossible : {exc}", retryable=True
            ) from exc
        _raise_for_status(response, "AssemblyAI")
        return response.json()

    def _wait(self, client: httpx.Client, job_id: str) -> dict:
        deadline = time.monotonic() + self._max_wait
        while time.monotonic() < deadline:
            data = self._request(client, "GET", f"/transcript/{job_id}")
            status = data.get("status")
            if status == "completed":
                return data
            if status == "error":
                raise TranscriptionError(
                    f"AssemblyAI a signalé une erreur : {data.get('error')}", retryable=False
                )
            time.sleep(POLL_INTERVAL)
        raise TranscriptionError(
            "AssemblyAI n'a pas terminé la transcription dans le temps imparti.",
            retryable=True,
        )


def _parse_assemblyai(data: dict) -> list[RawSegment]:
    utterances = data.get("utterances") or []
    segments: list[RawSegment] = []
    if utterances:
        for utt in utterances:
            text = (utt.get("text") or "").strip()
            if not text:
                continue
            confidence = utt.get("confidence")
            speaker = utt.get("speaker")
            segments.append(
                RawSegment(
                    start=float(utt.get("start", 0)) / 1000.0,
                    end=float(utt.get("end", 0)) / 1000.0,
                    text=text,
                    speaker=f"Locuteur {speaker}" if speaker else None,
                    confidence=confidence,
                    low_confidence=confidence is not None and confidence < LOW_CONFIDENCE,
                )
            )
        return segments

    text = (data.get("text") or "").strip()
    if text:
        words = data.get("words") or []
        end = float(words[-1].get("end", 0)) / 1000.0 if words else 0.0
        return [RawSegment(start=0.0, end=end, text=text)]
    return []


def _raise_for_status(response: httpx.Response, service: str) -> None:
    if response.status_code in (401, 403):
        raise TranscriptionError(f"Clé API {service} invalide.", retryable=False)
    if response.status_code == 429:
        raise TranscriptionError(f"Quota {service} atteint.", retryable=True)
    if response.status_code >= 500:
        raise TranscriptionError(
            f"{service} est indisponible (code {response.status_code}).", retryable=True
        )
    if response.status_code >= 400:
        raise TranscriptionError(
            f"{service} a renvoyé une erreur {response.status_code} : {response.text[:200]}",
            retryable=False,
        )
