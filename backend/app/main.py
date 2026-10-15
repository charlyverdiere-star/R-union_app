"""Application FastAPI : API + service de l'interface.

Un seul processus sert l'API et l'interface compilée. L'interface est donc
accessible sur une origine « sécurisée » (http://127.0.0.1), condition posée
par les navigateurs pour autoriser l'accès au microphone.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import PROJECT_ROOT, get_settings
from .api.routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("assistant")

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db.init_db()

    logger.info("Données stockées dans %s", settings.data_dir)
    logger.info(
        "Transcription : %s%s", settings.asr_provider,
        "" if settings.asr_problem() is None else f" — INDISPONIBLE ({settings.asr_problem()})",
    )
    logger.info(
        "Analyse : %s (%s)%s", settings.llm_provider, settings.llm_model,
        "" if settings.llm_problem() is None else f" — INDISPONIBLE ({settings.llm_problem()})",
    )
    if settings.asr_provider == "test-replay" or settings.llm_provider == "test-replay":
        logger.warning(
            "MODE TEST actif : les documents produits ne reflètent pas l'audio réel."
        )
    if not FRONTEND_DIST.exists():
        logger.warning(
            "Interface non compilée (%s absent). Lancez « npm install && npm run build » "
            "dans le dossier frontend/.", FRONTEND_DIST,
        )
    yield


app = FastAPI(
    title="Assistant de réunions",
    description="Enregistrement, transcription et compte rendu automatique de réunions.",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """Message lisible plutôt que le détail technique de la validation."""
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", [])[1:]) or "requête"
    return JSONResponse(
        status_code=422,
        content={"detail": f"Champ « {field} » invalide : {first.get('msg', 'valeur refusée')}"},
    )


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "asr_ready": settings.asr_problem() is None,
        "llm_ready": settings.llm_problem() is None,
    }


if FRONTEND_DIST.exists():
    app.mount(
        "/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets"
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str):
        """Sert les fichiers de l'interface, avec repli sur index.html pour que
        la navigation interne fonctionne au rechargement de la page."""
        candidate = (FRONTEND_DIST / full_path).resolve()
        if (
            full_path
            and candidate.is_file()
            and candidate.is_relative_to(FRONTEND_DIST.resolve())
        ):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
