"""Assemblage de la transcription : ordre, doublons, confiance, reprise."""

from __future__ import annotations

import pytest

from backend.app.config import Settings
from backend.app.models import Segment, Transcript
from backend.app.pipeline import transcribe
from backend.app.providers.asr import RawSegment, TranscriptionError, build_asr_provider
from backend.app.providers.asr.base import AsrCapabilities, AsrProvider, TranscriptionResult


def test_segments_ordonnes_et_numerotes():
    brut = [
        RawSegment(10.0, 14.0, "troisième"),
        RawSegment(0.0, 4.0, "premier"),
        RawSegment(5.0, 9.0, "deuxième"),
    ]
    segments = transcribe._finalise(brut)
    assert [s.id for s in segments] == ["s0001", "s0002", "s0003"]
    assert [s.text for s in segments] == ["premier", "deuxième", "troisième"]


def test_doublons_de_recouvrement_ecartes():
    brut = [
        RawSegment(0.0, 4.0, "bonjour à tous"),
        RawSegment(0.0, 4.0, "bonjour à tous"),
        RawSegment(4.0, 8.0, "bonjour à tous"),  # même texte, autre instant : conservé
    ]
    segments = transcribe._finalise(brut)
    assert len(segments) == 2


def test_passages_vides_ignores():
    brut = [RawSegment(0.0, 2.0, "   "), RawSegment(2.0, 4.0, "du texte")]
    assert len(transcribe._finalise(brut)) == 1


def test_avertissement_si_audio_majoritairement_incertain():
    transcript = Transcript(
        language="fr", provider="x", model="x", duration=600,
        segments=[
            Segment(id=f"s{i:04d}", start=i, end=i + 1, text="mot", low_confidence=i % 2 == 0)
            for i in range(10)
        ],
    )
    transcribe._add_quality_warnings(transcript)
    assert any("confiance faible" in w for w in transcript.warnings)


def test_pas_davertissement_si_audio_propre():
    transcript = Transcript(
        language="fr", provider="x", model="x", duration=10,
        segments=[Segment(id="s0001", start=0, end=9, text="tout va bien")],
    )
    transcribe._add_quality_warnings(transcript)
    assert transcript.warnings == []


# --- découpage selon les capacités du moteur --------------------------------


class MoteurSansLimite(AsrProvider):
    name = "sans-limite"
    capabilities = AsrCapabilities(diarization=True, max_seconds=None)

    def transcribe(self, audio, *, language, vocabulary, diarization):
        return TranscriptionResult([], "fr", self.name)


class MoteurLimite(AsrProvider):
    name = "limite"
    capabilities = AsrCapabilities(diarization=False, max_seconds=600)

    def transcribe(self, audio, *, language, vocabulary, diarization):
        return TranscriptionResult([], "fr", self.name)


def test_moteur_sans_limite_recoit_le_fichier_entier(tmp_path):
    from backend.app.storage import MeetingPaths

    chemins = MeetingPaths(tmp_path)
    chemins.ensure()
    morceaux = transcribe._plan(chemins, MoteurSansLimite(), 7200, Settings(), [])
    assert len(morceaux) == 1, (
        "un moteur capable d'avaler plusieurs heures ne doit pas être découpé : "
        "le découpage casserait la continuité des locuteurs"
    )


def test_moteur_limite_declenche_le_decoupage(monkeypatch, tmp_path):
    from backend.app.pipeline import audio as audio_module
    from backend.app.storage import MeetingPaths

    appels = {}

    def faux_prepare(work, dossier, duree, cible):
        appels["cible"] = cible
        return [audio_module.Chunk(0, work, 0.0, duree)]

    monkeypatch.setattr(transcribe.audio_module, "prepare_chunks", faux_prepare)
    chemins = MeetingPaths(tmp_path)
    chemins.ensure()
    transcribe._plan(chemins, MoteurLimite(), 7200, Settings(chunk_target_seconds=900), [])
    assert appels["cible"] == 600, "la cible doit être ramenée à la limite du moteur"


# --- reprise ----------------------------------------------------------------


class MoteurCapricieux(AsrProvider):
    """Échoue aux premiers essais, réussit ensuite."""

    name = "capricieux"
    capabilities = AsrCapabilities()

    def __init__(self, echecs: int, retryable: bool = True):
        self.restants = echecs
        self.retryable = retryable
        self.appels = 0

    def transcribe(self, audio, *, language, vocabulary, diarization):
        self.appels += 1
        if self.restants > 0:
            self.restants -= 1
            raise TranscriptionError("panne passagère", retryable=self.retryable)
        return TranscriptionResult([RawSegment(0, 1, "ok")], "fr", self.name)


def test_erreur_passagere_est_reessayee(monkeypatch, tmp_path):
    monkeypatch.setattr(transcribe, "BACKOFF_SECONDS", (0.0, 0.0))
    moteur = MoteurCapricieux(echecs=2)
    resultat = transcribe._transcribe_with_retry(moteur, tmp_path / "x.wav", Settings())
    assert moteur.appels == 3
    assert resultat.segments[0].text == "ok"


def test_erreur_definitive_nest_pas_reessayee(tmp_path):
    moteur = MoteurCapricieux(echecs=1, retryable=False)
    with pytest.raises(TranscriptionError):
        transcribe._transcribe_with_retry(moteur, tmp_path / "x.wav", Settings())
    assert moteur.appels == 1, "une clé invalide ne doit pas être retentée"


def test_cache_par_segment_permet_la_reprise(tmp_path):
    """Un segment déjà transcrit n'est pas renvoyé au moteur : on ne repaie pas
    deux fois la même minute d'audio."""
    fichier = tmp_path / "chunk.json"
    resultat = TranscriptionResult(
        [RawSegment(0, 3, "déjà transcrit", speaker="Locuteur 1", confidence=0.9)],
        "fr", "moteur",
    )
    transcribe._save_cache(fichier, resultat)
    relu = transcribe._load_cache(fichier)
    assert relu.segments[0].text == "déjà transcrit"
    assert relu.segments[0].speaker == "Locuteur 1"


# --- sélection du moteur ----------------------------------------------------


@pytest.mark.parametrize(
    "fournisseur, diarisation, limite",
    [("deepgram", True, None), ("assemblyai", True, None),
     ("openai", False, 700.0), ("local-whisper", False, None)],
)
def test_capacites_declarees(fournisseur, diarisation, limite):
    settings = Settings(
        asr_provider=fournisseur, deepgram_api_key="k", assemblyai_api_key="k", openai_api_key="k"
    )
    moteur = build_asr_provider(settings)
    assert moteur.capabilities.diarization is diarisation
    assert moteur.capabilities.max_seconds == limite


def test_cle_manquante_detectee_avant_tout_traitement():
    assert "DEEPGRAM_API_KEY" in Settings(asr_provider="deepgram").asr_problem()
    assert Settings(asr_provider="deepgram", deepgram_api_key="k").asr_problem() is None
