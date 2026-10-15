"""Parcours complet dans un vrai navigateur, du clic « Nouvelle réunion »
jusqu'aux trois livrables téléchargés.

C'est le seul test qui exerce réellement le microphone, `MediaRecorder`,
l'envoi progressif et le rendu de l'interface. Chromium reçoit un fichier
audio en guise de microphone (« --use-file-for-fake-audio-capture »), ce qui
donne un signal réaliste et un indicateur de niveau exploitable.

Lancer uniquement ce test :      pytest -m navigateur
L'exclure (suite rapide) :       pytest -m "not navigateur"
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.navigateur

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
CHROMIUM_CANDIDATES = [
    Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome"),
]


def chromium_path() -> str | None:
    for candidate in CHROMIUM_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    return None  # Playwright utilisera son navigateur installé


@pytest.fixture(scope="module")
def microphone_simule() -> Path:
    """Fichier audio injecté à la place du microphone."""
    cible = Path(tempfile.mkdtemp()) / "voix.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=220:duration=90,volume=0.35",
         "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(cible)],
        check=True, capture_output=True,
    )
    return cible


@pytest.fixture(scope="module")
def serveur():
    """Démarre l'application avec les fournisseurs de rejeu."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    donnees = tempfile.mkdtemp(prefix="e2e-")
    journal = Path(donnees) / "serveur.log"
    env = {
        **os.environ,
        "DATA_DIR": donnees,
        "ASR_PROVIDER": "test-replay",
        "LLM_PROVIDER": "test-replay",
        "REPLAY_TRANSCRIPT_PATH": str(FIXTURES / "transcript_reunion.json"),
        "REPLAY_LLM_DIR": str(FIXTURES / "llm"),
        "AUTH_TOKEN": "",
    }
    with journal.open("w") as sortie:
        processus = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.app.main:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=ROOT, env=env, stdout=sortie, stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        for _ in range(80):
            try:
                urllib.request.urlopen(base + "/healthz", timeout=1)
                break
            except Exception:
                time.sleep(0.25)
        else:
            processus.kill()
            pytest.fail(f"Le serveur n'a pas démarré :\n{journal.read_text()[-2000:]}")

        yield base, journal
        processus.send_signal(signal.SIGINT)
        processus.wait(timeout=15)


@pytest.fixture(scope="module")
def page(serveur, microphone_simule):
    playwright = pytest.importorskip("playwright.sync_api")
    if not (ROOT / "frontend" / "dist" / "index.html").exists():
        pytest.skip("Interface non compilée : lancez « npm run build » dans frontend/.")

    with playwright.sync_playwright() as pw:
        navigateur = pw.chromium.launch(
            executable_path=chromium_path(),
            args=[
                "--no-sandbox",
                "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={microphone_simule}%noloop",
                "--use-fake-ui-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        contexte = navigateur.new_context(
            permissions=["microphone"], viewport={"width": 1280, "height": 900}
        )
        onglet = contexte.new_page()
        erreurs: list[str] = []
        onglet.on("console", lambda m: erreurs.append(m.text) if m.type == "error" else None)
        onglet.on("pageerror", lambda e: erreurs.append(str(e)))
        onglet.erreurs_console = erreurs  # type: ignore[attr-defined]
        yield onglet
        navigateur.close()


# ---------------------------------------------------------------------------


def test_parcours_complet(page, serveur):
    base, journal = serveur
    TITRE = "Point hebdomadaire infrastructure"

    # --- 1. Tableau de bord vide --------------------------------------
    page.goto(base, wait_until="networkidle")
    assert page.locator("h1").first.inner_text() == "Mes réunions"
    assert "pas encore de réunion" in page.inner_text("body")
    assert "Mode test activé" in page.inner_text("body"), (
        "le mode test doit être signalé sans ambiguïté à l'utilisateur"
    )

    # --- 2. Création et enregistrement --------------------------------
    page.get_by_role("link", name="Nouvelle réunion").click()
    page.fill("input[type=text]", TITRE)
    page.fill("textarea >> nth=0", "Équipe plateforme, point d'avancement.")
    page.get_by_role("button", name="Démarrer l'enregistrement").click()
    page.wait_for_selector(".chrono", timeout=20000)

    niveaux = []
    for _ in range(25):
        style = page.get_attribute(".niveau-barre", "style") or ""
        try:
            niveaux.append(float(style.split("width:")[1].split("%")[0]))
        except (IndexError, ValueError):
            niveaux.append(0.0)
        time.sleep(0.2)

    assert page.locator(".pastille-active").count() == 1, "le micro doit être signalé actif"
    assert max(niveaux) > 20, (
        f"l'indicateur de niveau doit réagir au son (max relevé : {max(niveaux):.0f} %)"
    )
    assert page.inner_text(".chrono") != "00:00", "le chronomètre doit avancer"
    assert "déjà transmis" in page.inner_text(".enregistreur"), (
        "la sauvegarde progressive doit être visible"
    )

    # --- 3. Pause et reprise ------------------------------------------
    page.get_by_role("button", name="Mettre en pause").click()
    time.sleep(0.5)
    assert page.locator(".pastille-pause").count() == 1
    fige = page.inner_text(".chrono")
    time.sleep(2.0)
    assert page.inner_text(".chrono") == fige, "le chronomètre doit se figer en pause"

    page.get_by_role("button", name="Reprendre").click()
    time.sleep(2.5)
    assert page.locator(".pastille-active").count() == 1
    assert page.inner_text(".chrono") != fige, "le chronomètre doit repartir"

    # --- 4. Arrêt, traitement, résultats -------------------------------
    page.get_by_role("button", name="Arrêter").click()
    page.wait_for_selector("text=Enregistrement terminé", timeout=40000)
    page.get_by_role("button", name="Lancer le traitement").click()
    page.wait_for_selector("text=Livrables", timeout=30000)
    page.wait_for_selector("text=Fiabilité du compte rendu", timeout=180000)

    corps = page.inner_text("body")
    assert "Terminée" in corps
    assert corps.count("Disponible") >= 3, "les trois livrables doivent être disponibles"
    assert page.locator("audio").count() >= 1, "l'audio doit être réécoutable"

    # --- 5. Téléchargement des documents --------------------------------
    for libelle, extension in (("Télécharger DOCX", ".docx"), ("Télécharger PDF", ".pdf")):
        with page.expect_download(timeout=30000) as info:
            page.get_by_role("button", name=libelle).first.click()
        telecharge = info.value
        cible = Path(tempfile.mkdtemp()) / telecharge.suggested_filename
        telecharge.save_as(str(cible))
        assert cible.suffix == extension
        assert cible.stat().st_size > 5000, f"{cible.name} semble vide"

    # --- 6. Correction de la transcription ------------------------------
    page.get_by_role("button", name="Voir et corriger la transcription").click()
    page.wait_for_selector(".segment", timeout=30000)
    assert page.locator(".segment").count() == 32
    assert page.locator(".segment-incertain").count() == 1, (
        "le passage à faible confiance doit être mis en évidence"
    )
    page.locator(".segment-texte").first.fill("Bon, on démarre. Correction manuelle de test.")
    page.get_by_role("button", name="Enregistrer les corrections").click()
    page.wait_for_selector("text=Transcription enregistrée", timeout=30000)

    # --- 7. Régénération sans retranscrire ------------------------------
    page.get_by_role("button", name="Relancer l'analyse (sans retranscrire)").click()
    page.wait_for_timeout(1500)
    page.wait_for_selector("text=Fiabilité du compte rendu", timeout=180000)
    assert "Terminée" in page.inner_text("body")

    # --- 8. Recherche et suppression ------------------------------------
    page.goto(base, wait_until="networkidle")
    page.fill("input[type=search]", "kubernetes")
    page.wait_for_timeout(1000)
    assert TITRE in page.inner_text("body"), "la recherche doit porter sur le texte transcrit"

    page.fill("input[type=search]", "zzzintrouvable")
    page.wait_for_timeout(1000)
    assert "Aucune réunion ne correspond" in page.inner_text("body")

    page.fill("input[type=search]", "")
    page.wait_for_timeout(800)
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Supprimer").first.click()
    page.wait_for_timeout(2000)
    assert "pas encore de réunion" in page.inner_text("body")

    graves = [e for e in page.erreurs_console if "favicon" not in e.lower()]
    assert not graves, f"erreurs JavaScript : {graves[:3]}"
