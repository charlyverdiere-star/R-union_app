"""Organisation des fichiers sur le disque.

Séparation voulue par le cahier des charges :
  audio/      l'enregistrement original (livrable 1) et les fichiers de travail
  transcript.json / transcript.edited.json
  analysis/   données d'analyse intermediaires (fichiers de travail internes)
  documents/  les documents diffusables (livrables 2 et 3)
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .config import get_settings

MAX_SLUG_LENGTH = 60


def slugify(value: str, fallback: str = "reunion") -> str:
    """Transforme un titre en fragment de nom de fichier lisible et portable."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", ascii_only).strip("-")
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    return (cleaned[:MAX_SLUG_LENGTH].strip("-") or fallback)


@dataclass(frozen=True)
class MeetingPaths:
    """Tous les chemins d'une réunion, dérivés de son identifiant."""

    root: Path

    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    @property
    def chunks_dir(self) -> Path:
        return self.audio_dir / "chunks"

    @property
    def analysis_dir(self) -> Path:
        return self.root / "analysis"

    @property
    def windows_dir(self) -> Path:
        return self.analysis_dir / "windows"

    @property
    def documents_dir(self) -> Path:
        return self.root / "documents"

    # --- audio -------------------------------------------------------
    @property
    def incoming(self) -> Path:
        """Flux brut reçu du navigateur, écrit au fil de l'enregistrement."""
        return self.audio_dir / "incoming.bin"

    @property
    def incoming_meta(self) -> Path:
        return self.audio_dir / "incoming.json"

    @property
    def work_audio(self) -> Path:
        """WAV 16 kHz mono dérivé de l'archive, utilisé par la transcription."""
        return self.audio_dir / "work-16k.wav"

    def archive(self, extension: str) -> Path:
        return self.audio_dir / f"archive{extension}"

    def find_archive(self) -> Path | None:
        if not self.audio_dir.exists():
            return None
        for path in sorted(self.audio_dir.glob("archive.*")):
            if path.is_file():
                return path
        return None

    # --- transcription et analyse ------------------------------------
    @property
    def transcript(self) -> Path:
        return self.root / "transcript.json"

    @property
    def report_model(self) -> Path:
        return self.analysis_dir / "report_model.json"

    @property
    def merged_analysis(self) -> Path:
        return self.analysis_dir / "merged.json"

    def window_file(self, index: int) -> Path:
        return self.windows_dir / f"window-{index:03d}.json"

    def chunk_transcript(self, index: int) -> Path:
        return self.chunks_dir / f"chunk-{index:03d}.transcript.json"

    # --- utilitaires --------------------------------------------------
    def ensure(self) -> None:
        for directory in (
            self.audio_dir,
            self.chunks_dir,
            self.analysis_dir,
            self.windows_dir,
            self.documents_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def total_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())


def meeting_paths(meeting_id: str) -> MeetingPaths:
    settings = get_settings()
    return MeetingPaths(settings.data_dir / "meetings" / meeting_id)


def delete_meeting_files(meeting_id: str) -> None:
    paths = meeting_paths(meeting_id)
    if paths.root.exists():
        shutil.rmtree(paths.root)


# ---------------------------------------------------------------------------
# Noms des livrables
# ---------------------------------------------------------------------------

#: Suffixes des noms de fichiers. Volontairement sans accent : un nom de
#: fichier traverse des systèmes de fichiers, des messageries et des archives
#: dont le traitement de l'Unicode n'est pas uniforme.
DELIVERABLE_SUFFIXES = {
    "audio": "audio",
    "report": "rapport-complet",
    "summary": "synthese",
}


def deliverable_name(meeting_date: str, title: str, kind: str, extension: str) -> str:
    """« AAAA-MM-JJ_Nom-de-la-reunion_rapport-complet.docx »."""
    if kind not in DELIVERABLE_SUFFIXES:
        raise ValueError(f"Type de livrable inconnu : {kind}")
    ext = extension if extension.startswith(".") else f".{extension}"
    return f"{meeting_date}_{slugify(title)}_{DELIVERABLE_SUFFIXES[kind]}{ext}"
