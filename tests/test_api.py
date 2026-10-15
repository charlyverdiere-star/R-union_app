"""API HTTP : cycle de vie d'une réunion, erreurs, reprise, sécurité."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from backend.app import db, storage
from backend.app.models import MeetingStatus, Stage


def creer(client, titre="Point hebdomadaire", **extra) -> str:
    reponse = client.post("/api/meetings", json={"title": titre, **extra})
    assert reponse.status_code == 201, reponse.text
    return reponse.json()["id"]


def televerser(client, meeting_id: str, fichier: Path, morceaux: int = 4) -> None:
    donnees = fichier.read_bytes()
    taille = len(donnees) // morceaux + 1
    for index in range(morceaux):
        bloc = donnees[index * taille : (index + 1) * taille]
        if not bloc:
            break
        reponse = client.post(
            f"/api/meetings/{meeting_id}/recording/chunk?seq={index}", content=bloc
        )
        assert reponse.status_code == 200, reponse.text


def attendre_fin(client, meeting_id: str, limite: float = 90.0) -> dict:
    debut = time.monotonic()
    while time.monotonic() - debut < limite:
        meeting = client.get(f"/api/meetings/{meeting_id}").json()
        if meeting["status"] in ("completed", "failed"):
            return meeting
        time.sleep(0.2)
    pytest.fail(f"traitement non terminé en {limite} s")


def lancer_et_attendre(client, meeting_id: str, **payload) -> dict:
    """Lance un traitement puis attend sa fin.

    Le traitement démarre dans un fil d'exécution : sans attendre que l'état
    passe à « processing », on risque de relire l'état précédent et de croire
    le nouveau traitement déjà terminé.
    """
    reponse = client.post(f"/api/meetings/{meeting_id}/process", json=payload)
    assert reponse.status_code == 200, reponse.text
    debut = time.monotonic()
    while time.monotonic() - debut < 10:
        meeting = client.get(f"/api/meetings/{meeting_id}").json()
        if meeting["status"] == "processing" or meeting["is_running"]:
            break
        time.sleep(0.05)
    return attendre_fin(client, meeting_id)


# --- cycle de vie ------------------------------------------------------------


def test_creation_initialise_les_quatre_etapes(client):
    meeting = client.get(f"/api/meetings/{creer(client)}").json()
    assert meeting["status"] == "created"
    assert [e["stage"] for e in meeting["stages"]] == [
        "audio", "transcription", "analysis", "documents"
    ]
    assert all(e["status"] == "pending" for e in meeting["stages"])
    assert [d["kind"] for d in meeting["deliverables"]] == ["audio", "report", "summary"]
    assert not any(d["available"] for d in meeting["deliverables"])


def test_date_par_defaut_est_aujourd_hui(client):
    from datetime import date

    assert client.get(f"/api/meetings/{creer(client)}").json()["meeting_date"] == date.today().isoformat()


def test_date_invalide_refusee_avec_message_clair(client):
    reponse = client.post("/api/meetings", json={"title": "T", "meeting_date": "15/10/2026"})
    assert reponse.status_code == 422
    assert "AAAA-MM-JJ" in reponse.json()["detail"]


def test_titre_vide_refuse(client):
    assert client.post("/api/meetings", json={"title": ""}).status_code == 422


def test_reunion_inconnue_renvoie_404(client):
    assert client.get("/api/meetings/inexistante").status_code == 404
    assert "introuvable" in client.get("/api/meetings/inexistante").json()["detail"]


# --- enregistrement ----------------------------------------------------------


def test_morceaux_recus_dans_l_ordre(client, audio_court):
    meeting_id = creer(client)
    televerser(client, meeting_id, audio_court)
    etat = client.get(f"/api/meetings/{meeting_id}/recording/state").json()
    assert etat["next_seq"] == 4
    assert etat["bytes"] == audio_court.stat().st_size


def test_morceau_hors_sequence_refuse_sans_perdre_les_donnees(client, audio_court):
    meeting_id = creer(client)
    televerser(client, meeting_id, audio_court, morceaux=2)
    avant = client.get(f"/api/meetings/{meeting_id}/recording/state").json()

    reponse = client.post(f"/api/meetings/{meeting_id}/recording/chunk?seq=99", content=b"x")
    assert reponse.status_code == 409
    assert reponse.headers["X-Expected-Seq"] == str(avant["next_seq"])
    assert client.get(f"/api/meetings/{meeting_id}/recording/state").json() == avant, (
        "un morceau refusé ne doit rien écrire"
    )


def test_finalisation_sans_audio_refusee(client):
    meeting_id = creer(client)
    reponse = client.post(
        f"/api/meetings/{meeting_id}/recording/complete",
        json={"mime_type": "audio/webm", "start_processing": False},
    )
    assert reponse.status_code == 400
    assert "Aucune donnée audio" in reponse.json()["detail"]


def test_traitement_sans_enregistrement_refuse(client):
    reponse = client.post(f"/api/meetings/{creer(client)}/process", json={})
    assert reponse.status_code == 400
    assert "Aucun enregistrement" in reponse.json()["detail"]


# --- import ------------------------------------------------------------------


def test_import_de_fichier_audio(client, audio_court):
    meeting_id = creer(client, "Réunion importée")
    with audio_court.open("rb") as handle:
        reponse = client.post(
            f"/api/meetings/{meeting_id}/import",
            files={"file": ("reunion.webm", handle, "audio/webm")},
        )
    assert reponse.status_code == 200
    assert reponse.json()["source"] == "import"
    assert reponse.json()["status"] == "ready"


def test_import_vide_refuse(client):
    reponse = client.post(
        f"/api/meetings/{creer(client)}/import",
        files={"file": ("vide.webm", b"", "audio/webm")},
    )
    assert reponse.status_code == 400


def test_import_trop_volumineux_refuse(client, monkeypatch, audio_court):
    from backend.app.config import get_settings, reset_settings_cache

    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    reset_settings_cache()
    gros = b"0" * (2 * 1024 * 1024)
    reponse = client.post(
        f"/api/meetings/{creer(client)}/import",
        files={"file": ("gros.wav", gros, "audio/wav")},
    )
    assert reponse.status_code == 413
    assert "limite" in reponse.json()["detail"]


# --- traitement complet -------------------------------------------------------


@pytest.fixture
def reunion_traitee(client, audio_court):
    meeting_id = creer(client, "Point hebdomadaire infrastructure")
    televerser(client, meeting_id, audio_court)
    client.post(
        f"/api/meetings/{meeting_id}/recording/complete",
        json={"mime_type": "audio/webm", "start_processing": True},
    )
    return meeting_id, attendre_fin(client, meeting_id)


def test_traitement_complet_produit_les_trois_livrables(reunion_traitee):
    _, meeting = reunion_traitee
    assert meeting["status"] == "completed", meeting.get("error")
    assert all(e["status"] == "done" for e in meeting["stages"])
    assert meeting["overall_progress"] == 1.0
    disponibles = {d["kind"]: d for d in meeting["deliverables"] if d["available"]}
    assert set(disponibles) == {"audio", "report", "summary"}
    assert disponibles["report"]["formats"] == ["docx", "pdf"]
    assert disponibles["summary"]["formats"] == ["docx", "pdf"]


def test_telechargement_de_chaque_livrable(client, reunion_traitee):
    meeting_id, _ = reunion_traitee
    for genre, extension, debut in [
        ("audio", "ogg", b"OggS"),
        ("report", "docx", b"PK"),
        ("report", "pdf", b"%PDF"),
        ("summary", "docx", b"PK"),
        ("summary", "pdf", b"%PDF"),
    ]:
        reponse = client.get(f"/api/meetings/{meeting_id}/download/{genre}/{extension}")
        assert reponse.status_code == 200, f"{genre}/{extension}"
        assert reponse.content.startswith(debut), f"{genre}/{extension} : format inattendu"
        assert "attachment" in reponse.headers.get("content-disposition", "")


def test_lecture_audio_accepte_les_requetes_partielles(client, reunion_traitee):
    """Nécessaire pour se déplacer dans le lecteur audio."""
    meeting_id, _ = reunion_traitee
    reponse = client.get(f"/api/meetings/{meeting_id}/audio", headers={"Range": "bytes=0-1023"})
    assert reponse.status_code == 206
    assert len(reponse.content) == 1024
    assert reponse.headers["content-range"].startswith("bytes 0-1023/")


def test_document_inconnu_renvoie_404(client, reunion_traitee):
    meeting_id, _ = reunion_traitee
    assert client.get(f"/api/meetings/{meeting_id}/download/report/txt").status_code == 404
    assert client.get(f"/api/meetings/{meeting_id}/download/inconnu/pdf").status_code == 404


def test_synthese_du_rapport_exposee(client, reunion_traitee):
    meeting_id, _ = reunion_traitee
    rapport = client.get(f"/api/meetings/{meeting_id}/report").json()
    assert rapport["topics"] == 3
    assert rapport["items_total"] > 30
    assert rapport["items_verified"] + rapport["items_partial"] <= rapport["items_total"]
    assert rapport["dropped_items"] >= 1


# --- correction et régénération ------------------------------------------------


def test_correction_invalide_les_analyses_en_cache(client, reunion_traitee):
    meeting_id, _ = reunion_traitee
    chemins = storage.meeting_paths(meeting_id)
    assert chemins.window_file(0).exists()

    transcription = client.get(f"/api/meetings/{meeting_id}/transcript").json()
    segments = transcription["segments"]
    segments[0]["text"] = "Texte corrigé manuellement."
    reponse = client.put(f"/api/meetings/{meeting_id}/transcript", json={"segments": segments})
    assert reponse.status_code == 200
    assert reponse.json()["segments"][0]["edited"] is True
    assert reponse.json()["edited_at"] is not None

    assert not chemins.window_file(0).exists(), (
        "les extractions en cache ne correspondent plus au texte corrigé"
    )
    meeting = client.get(f"/api/meetings/{meeting_id}").json()
    etats = {e["stage"]: e["status"] for e in meeting["stages"]}
    assert etats["transcription"] == "done", "la transcription ne doit pas être refaite"
    assert etats["analysis"] == "pending" and etats["documents"] == "pending"


def test_regeneration_sans_retranscrire(client, reunion_traitee):
    meeting_id, _ = reunion_traitee
    chemins = storage.meeting_paths(meeting_id)
    date_transcription = chemins.transcript.stat().st_mtime

    meeting = lancer_et_attendre(client, meeting_id, from_stage="analysis")

    assert meeting["status"] == "completed"
    assert chemins.transcript.stat().st_mtime == date_transcription, (
        "l'audio ne doit pas être retranscrit"
    )


def test_segment_vide_supprime_le_passage(client, reunion_traitee):
    meeting_id, _ = reunion_traitee
    transcription = client.get(f"/api/meetings/{meeting_id}/transcript").json()
    avant = len(transcription["segments"])
    transcription["segments"][0]["text"] = "   "
    apres = client.put(
        f"/api/meetings/{meeting_id}/transcript", json={"segments": transcription["segments"]}
    ).json()
    assert len(apres["segments"]) == avant - 1
    assert [s["id"] for s in apres["segments"]][:2] == ["s0001", "s0002"], "renumérotation attendue"


def test_correction_impossible_sans_transcription(client):
    assert client.get(f"/api/meetings/{creer(client)}/transcript").status_code == 404


# --- reprise après échec --------------------------------------------------------


def test_reprise_apres_echec_conserve_les_etapes_reussies(client, audio_court, monkeypatch):
    """Une panne d'API ne doit pas faire repayer la transcription."""
    meeting_id = creer(client)
    televerser(client, meeting_id, audio_court)
    client.post(
        f"/api/meetings/{meeting_id}/recording/complete",
        json={"mime_type": "audio/webm", "start_processing": False},
    )

    from backend.app.pipeline import orchestrator
    from backend.app.providers.llm.base import LlmError

    original = orchestrator.analyze.run_analysis

    def analyse_en_panne(*args, **kwargs):
        raise LlmError("Service momentanément indisponible.", retryable=True)

    monkeypatch.setattr(orchestrator.analyze, "run_analysis", analyse_en_panne)
    meeting = lancer_et_attendre(client, meeting_id)

    assert meeting["status"] == "failed", (
        f"l'analyse aurait dû échouer ; états = "
        f"{ {e['stage']: e['status'] for e in meeting['stages']} }, "
        f"erreur = {meeting['error']!r}, "
        f"fonction patchée = {orchestrator.analyze.run_analysis.__name__}"
    )
    etats = {e["stage"]: e["status"] for e in meeting["stages"]}
    assert etats["audio"] == "done" and etats["transcription"] == "done"
    assert etats["analysis"] == "failed"
    assert "indisponible" in meeting["error"]

    chemins = storage.meeting_paths(meeting_id)
    date_transcription = chemins.transcript.stat().st_mtime

    monkeypatch.setattr(orchestrator.analyze, "run_analysis", original)
    repris = lancer_et_attendre(client, meeting_id)

    assert repris["status"] == "completed", (
        f"la reprise aurait dû aboutir ; états = "
        f"{ {e['stage']: (e['status'], e['error']) for e in repris['stages']} }, "
        f"erreur = {repris['error']!r}"
    )
    assert chemins.transcript.stat().st_mtime == date_transcription, (
        "la transcription déjà obtenue doit être réutilisée"
    )


def test_traitement_simultane_refuse(client, audio_court, monkeypatch):
    meeting_id = creer(client)
    televerser(client, meeting_id, audio_court)
    client.post(
        f"/api/meetings/{meeting_id}/recording/complete",
        json={"mime_type": "audio/webm", "start_processing": False},
    )
    from backend.app.pipeline import orchestrator

    monkeypatch.setattr(orchestrator, "is_running", lambda _: True)
    monkeypatch.setattr(orchestrator, "start", lambda *a, **k: False)
    reponse = client.post(f"/api/meetings/{meeting_id}/process", json={})
    assert reponse.status_code == 409


# --- recherche, modification, suppression ----------------------------------------


def test_recherche_porte_sur_le_contenu_transcrit(client, reunion_traitee):
    meeting_id, _ = reunion_traitee
    assert [m["id"] for m in client.get("/api/meetings", params={"query": "kubernetes"}).json()] == [meeting_id]
    assert [m["id"] for m in client.get("/api/meetings", params={"query": "postgresql"}).json()] == [meeting_id]
    assert client.get("/api/meetings", params={"query": "zzzintrouvable"}).json() == []


def test_recherche_insensible_aux_accents(client, reunion_traitee):
    meeting_id, _ = reunion_traitee
    assert [m["id"] for m in client.get("/api/meetings", params={"query": "hebdomadaire"}).json()] == [meeting_id]


def test_filtres_par_etat_et_par_date(client, reunion_traitee):
    meeting_id, meeting = reunion_traitee
    assert client.get("/api/meetings", params={"status": "completed"}).json()[0]["id"] == meeting_id
    assert client.get("/api/meetings", params={"status": "failed"}).json() == []
    assert client.get("/api/meetings", params={"date_from": "2000-01-01"}).json()
    assert client.get("/api/meetings", params={"date_from": "2099-01-01"}).json() == []


def test_renommage_regenere_les_documents(client, reunion_traitee):
    meeting_id, _ = reunion_traitee
    reponse = client.patch(f"/api/meetings/{meeting_id}", json={"title": "Comité de pilotage"})
    assert reponse.status_code == 200

    noms = {d["filename"] for d in reponse.json()["deliverables"]}
    assert any("Comite-de-pilotage_rapport-complet" in n for n in noms)
    assert client.get(f"/api/meetings/{meeting_id}/download/report/docx").status_code == 200

    chemins = storage.meeting_paths(meeting_id)
    restants = list(chemins.documents_dir.glob("*rapport-complet.docx"))
    assert len(restants) == 1, "l'ancien document ne doit pas subsister"


def test_suppression_efface_les_fichiers(client, reunion_traitee):
    meeting_id, _ = reunion_traitee
    chemins = storage.meeting_paths(meeting_id)
    assert chemins.root.exists()

    assert client.delete(f"/api/meetings/{meeting_id}").status_code == 204
    assert not chemins.root.exists(), "les fichiers doivent être effacés du disque"
    assert client.get(f"/api/meetings/{meeting_id}").status_code == 404
    assert client.get("/api/meetings", params={"query": "kubernetes"}).json() == [], (
        "l'index de recherche doit être purgé"
    )


# --- configuration et sécurité -----------------------------------------------------


def test_configuration_ne_divulgue_aucun_secret(client, monkeypatch):
    from backend.app.config import reset_settings_cache

    monkeypatch.setenv("ASR_PROVIDER", "deepgram")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "secret-a-ne-pas-divulguer")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "autre-secret")
    reset_settings_cache()

    corps = client.get("/api/config").text
    assert "secret-a-ne-pas-divulguer" not in corps
    assert "autre-secret" not in corps
    assert client.get("/api/config").json()["asr_ready"] is True


def test_configuration_signale_une_cle_manquante(client, monkeypatch):
    from backend.app.config import reset_settings_cache

    monkeypatch.setenv("ASR_PROVIDER", "deepgram")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "")
    reset_settings_cache()
    config = client.get("/api/config").json()
    assert config["asr_ready"] is False
    assert "DEEPGRAM_API_KEY" in config["asr_problem"]


def test_jeton_exige_quand_configure(client, monkeypatch):
    from backend.app.config import reset_settings_cache

    monkeypatch.setenv("AUTH_TOKEN", "mon-jeton")
    reset_settings_cache()

    assert client.get("/api/meetings").status_code == 401
    assert client.get("/api/meetings", headers={"X-Auth-Token": "faux"}).status_code == 401
    assert client.get("/api/meetings", headers={"X-Auth-Token": "mon-jeton"}).status_code == 200
    assert client.get("/api/config").status_code == 200, (
        "l'état de configuration reste lisible pour afficher la demande de jeton"
    )


def test_etat_de_sante(client):
    assert client.get("/healthz").json()["status"] == "ok"


# --- conservation des données --------------------------------------------------------


def test_politique_de_conservation_inactive_par_defaut(client):
    assert client.post("/api/maintenance/retention").json()["deleted"] == 0


def test_politique_de_conservation_supprime_les_anciennes(client, monkeypatch):
    from backend.app.config import reset_settings_cache

    ancienne = creer(client, "Vieille réunion", meeting_date="2020-01-01")
    recente = creer(client, "Réunion récente")

    monkeypatch.setenv("RETENTION_DAYS", "30")
    reset_settings_cache()
    resultat = client.post("/api/maintenance/retention").json()

    assert resultat["deleted"] == 1
    assert client.get(f"/api/meetings/{ancienne}").status_code == 404
    assert client.get(f"/api/meetings/{recente}").status_code == 200
    assert not storage.meeting_paths(ancienne).root.exists()
