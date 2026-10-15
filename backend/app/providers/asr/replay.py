"""Moteur de rejeu — RÉSERVÉ AUX TESTS AUTOMATISÉS.

Il ne transcrit rien : il relit une transcription déposée sur le disque. Il
permet de valider tout le reste de la chaîne (découpage, orchestration,
analyse, génération des documents, reprise après erreur) sans dependre d'un
service externe.

Il n'est actif que si ASR_PROVIDER vaut explicitement « test-replay », et
l'interface affiché alors un avertissement : aucun document produit dans ce
mode ne doit être pris pour un compte rendu réel.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import AsrCapabilities, AsrProvider, RawSegment, TranscriptionError, TranscriptionResult


class ReplayProvider(AsrProvider):
    name = "test-replay"
    capabilities = AsrCapabilities(diarization=True, max_seconds=None, max_bytes=None, local=True)

    def __init__(self, transcript_path: Path | None):
        if not transcript_path:
            raise TranscriptionError("REPLAY_TRANSCRIPT_PATH n'est pas renseigné.")
        self._path = Path(transcript_path)

    def transcribe(
        self, audio: Path, *, language: str, vocabulary: list[str], diarization: bool
    ) -> TranscriptionResult:
        if not self._path.exists():
            raise TranscriptionError(
                f"Transcription de rejeu introuvable : {self._path}", retryable=False
            )
        data = json.loads(self._path.read_text(encoding="utf-8"))
        segments = [
            RawSegment(
                start=float(item["start"]),
                end=float(item["end"]),
                text=item["text"],
                speaker=item.get("speaker"),
                confidence=item.get("confidence"),
                low_confidence=bool(item.get("low_confidence", False)),
            )
            for item in data.get("segments", [])
        ]
        return TranscriptionResult(
            segments=segments,
            language=data.get("language", language),
            model="test-replay",
            warnings=[
                "MODE TEST : cette transcription provient d'un fichier de rejeu, "
                "elle n'a pas été produite à partir de l'audio."
            ],
        )
