#!/usr/bin/env python3
"""Génère des documents d'exemple, sans aucune clé API.

Utile pour juger la mise en forme et le niveau de détail des livrables avant
de souscrire aux services d'IA. Les documents produits portent la mention
« MODE TEST » : ils sont construits à partir du jeu de données de test, pas
d'un enregistrement réel.

    python scripts/generer_exemples.py [destination]
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))


def main(argv: list[str]) -> int:
    destination = Path(argv[1]).resolve() if len(argv) > 1 else RACINE / "exemples"
    destination.mkdir(parents=True, exist_ok=True)

    fixtures = RACINE / "tests" / "fixtures"
    if not (fixtures / "transcript_reunion.json").exists():
        print("Jeu de données de test introuvable.", file=sys.stderr)
        return 1

    os.environ.update(
        DATA_DIR=tempfile.mkdtemp(prefix="exemples-"),
        ASR_PROVIDER="test-replay",
        LLM_PROVIDER="test-replay",
        REPLAY_TRANSCRIPT_PATH=str(fixtures / "transcript_reunion.json"),
        REPLAY_LLM_DIR=str(fixtures / "llm"),
    )

    from backend.app import storage
    from backend.app.config import get_settings, reset_settings_cache
    from backend.app.models import Meeting
    from backend.app.pipeline import analyze, orchestrator, transcribe

    reset_settings_cache()
    settings = get_settings()

    chemins = storage.meeting_paths("exemple")
    chemins.ensure()
    reunion = Meeting(
        id="exemple",
        title="Point hebdomadaire infrastructure",
        meeting_date="2026-10-15",
        context="Point d'avancement hebdomadaire de l'équipe plateforme.",
    )

    print("Transcription (rejeu)…")
    transcription = transcribe.run_transcription(chemins, settings, duration=349.0)

    print("Analyse et vérification…")
    modele, _ = analyze.run_analysis(reunion, transcription, chemins, settings)
    qualite = modele.quality
    print(
        f"  {qualite.items_verified}/{qualite.items_total} informations vérifiées, "
        f"{qualite.items_partial} signalées, {qualite.dropped_items} écartée(s)"
    )

    print("Génération des documents…")
    noms = orchestrator.generate_documents(reunion, modele, transcription, chemins)
    for nom in noms:
        cible = destination / nom
        cible.write_bytes((chemins.documents_dir / nom).read_bytes())
        print(f"  {cible}")

    print(f"\n{len(noms)} documents écrits dans {destination}")
    print(
        "Rappel : ces documents proviennent du jeu de test, pas d'un "
        "enregistrement réel."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
