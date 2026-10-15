"""Préparation de l'audio : archivage sans perte, réparation d'un
enregistrement interrompu, découpage des longues réunions."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from backend.app import storage
from backend.app.pipeline import audio as A


def pcm(path: Path) -> bytes:
    """Flux décodé, pour comparer deux fichiers au-delà de leur conteneur."""
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", "48000", "-"],
        capture_output=True, check=True,
    ).stdout


# --- sonde ------------------------------------------------------------------


def test_sonde_lit_les_caracteristiques(audio_court):
    info = A.probe(audio_court)
    assert info.codec == "opus"
    assert info.duration == pytest.approx(70, abs=1.5)
    assert info.size_bytes > 0


def test_fichier_absent_donne_un_message_clair(tmp_path):
    with pytest.raises(A.AudioError, match="vide ou absent"):
        A.probe(tmp_path / "rien.webm")


def test_fichier_sans_piste_audio_est_refuse(tmp_path):
    faux = tmp_path / "faux.webm"
    faux.write_bytes(b"ceci n'est pas un fichier audio" * 50)
    with pytest.raises(A.AudioError):
        A.probe(faux)


# --- archive (livrable 1) ---------------------------------------------------


def test_archive_conserve_laudio_sans_reencodage(audio_court):
    """L'archive doit être l'enregistrement original : on vérifie que le flux
    décodé est identique à la source, au délai de codec près."""
    chemins = storage.meeting_paths("archive")
    archive, info = A.build_archive(audio_court, chemins, archive_format="original")

    assert archive.suffix == ".ogg"           # conteneur lisible et navigable
    assert info.codec == "opus"               # codec inchangé
    assert info.duration == pytest.approx(A.probe(audio_court).duration, abs=0.2)
    # Même quantité d'audio décodé : aucune perte, aucun rééchantillonnage.
    assert len(pcm(archive)) == pytest.approx(len(pcm(audio_court)), rel=0.01)


def test_archive_en_flac_si_demandee(audio_court):
    chemins = storage.meeting_paths("flac")
    archive, info = A.build_archive(audio_court, chemins, archive_format="flac")
    assert archive.suffix == ".flac"
    assert info.codec == "flac"


def test_enregistrement_interrompu_est_recupere(audio_tronque, audio_court):
    """Coupure brutale : on doit récupérer tout ce qui a été écrit."""
    chemins = storage.meeting_paths("tronque")
    archive, info = A.build_archive(audio_tronque, chemins, archive_format="original")
    complet = A.probe(audio_court).duration
    assert info.duration > complet * 0.75, "trop d'audio perdu"
    assert info.duration < complet, "la troncature devrait réduire la durée"


# --- fichier de travail -----------------------------------------------------


def test_fichier_de_travail_normalise(audio_court):
    chemins = storage.meeting_paths("travail")
    archive, _ = A.build_archive(audio_court, chemins)
    travail = A.make_work_audio(archive, chemins.work_audio)
    info = A.probe(travail)
    assert (info.sample_rate, info.channels, info.codec) == (16000, 1, "pcm_s16le")


# --- découpage --------------------------------------------------------------


def test_reunion_courte_non_decoupee():
    assert A.plan_chunks(300, [], 600) == [(0.0, 300)]


def test_decoupage_couvre_tout_sans_recouvrement():
    bornes = A.plan_chunks(3600, [], 600)
    assert bornes[0][0] == 0.0 and bornes[-1][1] == 3600
    for precedent, suivant in zip(bornes, bornes[1:]):
        assert precedent[1] == suivant[0], "les segments doivent être jointifs"
    assert sum(fin - debut for debut, fin in bornes) == 3600


def test_coupes_alignees_sur_les_silences():
    """Une coupe doit tomber dans un silence proche, pas au milieu d'une phrase."""
    silences = [(592.0, 600.0), (1195.0, 1200.0)]
    bornes = A.plan_chunks(1500, silences, 600)
    coupes = [fin for _, fin in bornes[:-1]]
    assert coupes[0] == pytest.approx(596.0, abs=0.1)
    assert coupes[1] == pytest.approx(1197.5, abs=0.1)


def test_silence_trop_eloigne_ignore():
    """Un silence hors de la fenêtre de tolérance ne doit pas déplacer la coupe."""
    bornes = A.plan_chunks(1500, [(100.0, 110.0)], 600)
    assert bornes[0][1] == 600.0


def test_decoupage_reel_produit_des_fichiers_lisibles(audio_court):
    chemins = storage.meeting_paths("decoupe")
    archive, _ = A.build_archive(audio_court, chemins)
    travail = A.make_work_audio(archive, chemins.work_audio)
    duree = A.probe(travail).duration

    morceaux = A.prepare_chunks(travail, chemins.chunks_dir, duree, target_seconds=25)
    assert len(morceaux) >= 2
    assert morceaux[0].start == 0.0
    assert morceaux[-1].end == pytest.approx(duree, abs=0.2)
    total = sum(A.probe(m.path).duration for m in morceaux)
    assert total == pytest.approx(duree, rel=0.02), "des segments ont été perdus"


def test_detection_de_silence(audio_court):
    silences = A.detect_silences(audio_court)
    assert len(silences) >= 2
    assert any(abs(debut - 18) < 2 for debut, _ in silences)


# --- affichage --------------------------------------------------------------


@pytest.mark.parametrize(
    "secondes, attendu",
    [(7, "7 s"), (95, "1 min 35 s"), (3725, "1 h 02 min"), (0, "0 s")],
)
def test_duree_lisible(secondes, attendu):
    assert A.human_duration(secondes) == attendu
