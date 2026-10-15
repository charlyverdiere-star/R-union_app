"""Fixtures communes aux tests.

Chaque test travaille dans un répertoire de données isolé et utilise les
fournisseurs de rejeu : aucun appel réseau, aucun coût, résultat déterministe.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def environnement_isole(tmp_path, monkeypatch):
    """Isole les données et force les fournisseurs de test."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ASR_PROVIDER", "test-replay")
    monkeypatch.setenv("LLM_PROVIDER", "test-replay")
    monkeypatch.setenv("REPLAY_TRANSCRIPT_PATH", str(FIXTURES / "transcript_reunion.json"))
    monkeypatch.setenv("REPLAY_LLM_DIR", str(FIXTURES / "llm"))
    monkeypatch.setenv("AUTH_TOKEN", "")
    monkeypatch.setenv("RETENTION_DAYS", "0")

    from backend.app.config import reset_settings_cache

    reset_settings_cache()
    from backend.app import db

    db.init_db()
    yield
    reset_settings_cache()


@pytest.fixture
def settings():
    from backend.app.config import get_settings

    return get_settings()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from backend.app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def audio_court(tmp_path_factory) -> Path:
    """Enregistrement WebM/Opus de 70 s, avec deux silences exploitables."""
    cible = tmp_path_factory.mktemp("audio") / "reunion.webm"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "sine=frequency=330:duration=18",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono:d=4",
            "-f", "lavfi", "-i", "sine=frequency=550:duration=25",
            "-filter_complex", "[0][1][2][3][4]concat=n=5:v=0:a=1[a]",
            "-map", "[a]", "-c:a", "libopus", "-b:a", "32k", str(cible),
        ],
        check=True, capture_output=True,
    )
    return cible


@pytest.fixture
def audio_tronque(audio_court, tmp_path) -> Path:
    """Même enregistrement, amputé de sa fin : simule une coupure brutale."""
    cible = tmp_path / "reunion-tronquee.webm"
    donnees = audio_court.read_bytes()
    cible.write_bytes(donnees[: int(len(donnees) * 0.85)])
    return cible


@pytest.fixture
def transcription():
    """Transcription complète issue du jeu de test."""
    import json
    from backend.app.models import Segment, Transcript

    brut = json.loads((FIXTURES / "transcript_reunion.json").read_text(encoding="utf-8"))
    segments = [
        Segment(
            id=f"s{index:04d}",
            start=item["start"], end=item["end"], text=item["text"],
            speaker=item.get("speaker"), confidence=item.get("confidence"),
            low_confidence=bool(item.get("low_confidence", False)),
        )
        for index, item in enumerate(brut["segments"], start=1)
    ]
    return Transcript(
        language="fr", provider="test-replay", model="test-replay",
        duration=segments[-1].end, segments=segments, diarization=True,
    )


@pytest.fixture
def reunion_analysee(transcription, settings):
    """Réunion complète : transcription + modèle de rapport vérifié."""
    from backend.app import storage
    from backend.app.models import Meeting
    from backend.app.pipeline import analyze

    chemins = storage.meeting_paths("test")
    chemins.ensure()
    reunion = Meeting(id="test", title="Point hebdomadaire infrastructure", meeting_date="2026-10-15")
    modele, _ = analyze.run_analysis(reunion, transcription, chemins, settings)
    return reunion, transcription, modele, chemins
