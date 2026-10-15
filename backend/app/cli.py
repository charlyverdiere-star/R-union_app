"""Point d'entrée en ligne de commande."""

from __future__ import annotations

import argparse
import sys
import webbrowser

from .config import get_settings


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="meeting-assistant",
        description="Assistant d'enregistrement et de compte rendu de réunions.",
    )
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--reload", action="store_true", help="rechargement automatique (développement)")
    parser.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur")
    args = parser.parse_args(argv)

    import uvicorn

    url = f"http://{args.host}:{args.port}"
    print(f"\n  Assistant de réunions — {url}")
    print(f"  Données : {settings.data_dir}")
    if settings.asr_problem():
        print(f"  ⚠ Transcription indisponible : {settings.asr_problem()}")
    if settings.llm_problem():
        print(f"  ⚠ Analyse indisponible : {settings.llm_problem()}")
    print()

    if not args.no_browser and args.host in ("127.0.0.1", "localhost"):
        try:
            webbrowser.open(url)
        except Exception:
            pass

    uvicorn.run(
        "backend.app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
