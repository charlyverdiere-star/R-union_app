"""Machine à états du traitement d'une réunion.

Les quatre étapes — préparation de l'audio, transcription, analyse,
génération des documents — s'exécutent en séquence, et **chacune enregistre
son résultat sur le disque avant de passer à la suivante**. Si le traitement
échoue ou s'interrompt, il reprend à la dernière étape réussie : une
transcription déjà payée n'est jamais refaite pour régénérer un document.

Le traitement tourne dans un fil d'exécution dédié ; l'interface interroge
l'état d'avancement via l'API.
"""

from __future__ import annotations

import logging
import threading
import traceback
from datetime import datetime, timezone
from typing import Callable

from .. import db, storage
from ..config import Settings, get_settings
from ..models import (
    Meeting,
    MeetingStatus,
    ReportModel,
    STAGE_ORDER,
    Stage,
    Transcript,
)
from ..render import docx_renderer, pdf_renderer
from ..storage import MeetingPaths, deliverable_name
from . import analyze, audio as audio_module, transcribe

logger = logging.getLogger(__name__)

#: Travaux en cours, pour éviter qu'une réunion soit traitée deux fois.
_running: dict[str, threading.Thread] = {}
_lock = threading.Lock()


class ProcessingError(RuntimeError):
    """Erreur destinée à être affichée telle quelle à l'utilisateur."""


def is_running(meeting_id: str) -> bool:
    with _lock:
        thread = _running.get(meeting_id)
        return thread is not None and thread.is_alive()


def start(meeting_id: str, *, from_stage: Stage | None = None) -> bool:
    """Lance le traitement en arrière-plan. Retourne False s'il tourne déjà."""
    with _lock:
        existing = _running.get(meeting_id)
        if existing is not None and existing.is_alive():
            return False
        thread = threading.Thread(
            target=_run_safely,
            args=(meeting_id, from_stage),
            name=f"traitement-{meeting_id[:8]}",
            daemon=True,
        )
        _running[meeting_id] = thread
        thread.start()
        return True


def _run_safely(meeting_id: str, from_stage: Stage | None) -> None:
    try:
        run(meeting_id, from_stage=from_stage)
    except Exception:  # pragma: no cover - filet de sécurité du fil d'exécution
        logger.exception("Traitement interrompu pour la réunion %s", meeting_id)


# ---------------------------------------------------------------------------
# Déroulé
# ---------------------------------------------------------------------------


def run(meeting_id: str, *, from_stage: Stage | None = None, settings: Settings | None = None) -> Meeting:
    """Exécute les étapes manquantes. Synchrone : utilisé par `start` et les tests."""
    settings = settings or get_settings()
    meeting = db.get_meeting(meeting_id)
    if meeting is None:
        raise ProcessingError("Réunion introuvable.")

    paths = storage.meeting_paths(meeting_id)
    paths.ensure()

    if from_stage is not None:
        _reset_from(meeting, from_stage)
        clear_stage_outputs(paths, from_stage)

    meeting.status = MeetingStatus.PROCESSING
    meeting.error = None
    db.save_meeting(meeting)

    steps: list[tuple[Stage, Callable[[Meeting, MeetingPaths, Settings], None]]] = [
        (Stage.AUDIO, _stage_audio),
        (Stage.TRANSCRIPTION, _stage_transcription),
        (Stage.ANALYSIS, _stage_analysis),
        (Stage.DOCUMENTS, _stage_documents),
    ]

    for stage, handler in steps:
        state = meeting.stage(stage)
        if state.status == "done":
            continue
        state.status = "running"
        state.error = None
        state.progress = 0.0
        state.started_at = datetime.now(timezone.utc)
        state.message = "En cours…"
        db.save_meeting(meeting)

        try:
            handler(meeting, paths, settings)
        except Exception as exc:
            state.status = "failed"
            state.error = _user_message(exc)
            state.finished_at = datetime.now(timezone.utc)
            meeting.status = MeetingStatus.FAILED
            meeting.error = state.error
            db.save_meeting(meeting)
            logger.warning("Étape %s en échec : %s", stage, exc, exc_info=True)
            return meeting

        state.status = "done"
        state.progress = 1.0
        state.finished_at = datetime.now(timezone.utc)
        db.save_meeting(meeting)

    meeting.status = MeetingStatus.COMPLETED
    meeting.error = None
    db.save_meeting(meeting)
    return meeting


def _reset_from(meeting: Meeting, stage: Stage) -> None:
    """Replace à « à faire » l'étape visée et toutes les suivantes."""
    start_index = STAGE_ORDER.index(stage)
    for later in STAGE_ORDER[start_index:]:
        state = meeting.stage(later)
        state.status = "pending"
        state.progress = 0.0
        state.message = ""
        state.error = None
        state.started_at = None
        state.finished_at = None


def clear_stage_outputs(paths: MeetingPaths, stage: Stage) -> None:
    """Supprime les résultats intermédiaires d'une étape que l'on veut refaire.

    Sans cela, relancer explicitement une étape rejouerait simplement son
    cache. La distinction est importante : une **reprise après échec**
    conserve le cache (on ne repaie pas une transcription déjà obtenue),
    tandis qu'une **relance demandée** le supprime.
    """
    index = STAGE_ORDER.index(stage)

    if index <= STAGE_ORDER.index(Stage.TRANSCRIPTION):
        paths.transcript.unlink(missing_ok=True)
        if paths.chunks_dir.exists():
            for cached in paths.chunks_dir.glob("*.transcript.json"):
                cached.unlink()
    if index <= STAGE_ORDER.index(Stage.ANALYSIS):
        paths.merged_analysis.unlink(missing_ok=True)
        paths.report_model.unlink(missing_ok=True)
        if paths.windows_dir.exists():
            for cached in paths.windows_dir.glob("*.json"):
                cached.unlink()


def invalidate_analysis(paths: MeetingPaths) -> None:
    """À appeler après une correction manuelle de la transcription : les
    extractions mises en cache ne correspondent plus au texte source."""
    clear_stage_outputs(paths, Stage.ANALYSIS)


def _user_message(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        message = f"{type(exc).__name__} — {traceback.format_exc(limit=1).strip()}"
    return message[:600]


def _progress(meeting: Meeting, stage: Stage) -> Callable[[float, str], None]:
    """Remonte l'avancement d'une étape, sans saturer la base de données."""
    state = meeting.stage(stage)
    last = {"value": -1.0}

    def report(fraction: float, message: str) -> None:
        state.progress = max(0.0, min(1.0, fraction))
        state.message = message
        if state.progress - last["value"] >= 0.02 or state.progress >= 1.0:
            last["value"] = state.progress
            db.save_meeting(meeting)

    return report


# ---------------------------------------------------------------------------
# Étape A — audio
# ---------------------------------------------------------------------------


def _stage_audio(meeting: Meeting, paths: MeetingPaths, settings: Settings) -> None:
    report = _progress(meeting, Stage.AUDIO)
    archive = paths.find_archive()

    if archive is None:
        if not paths.incoming.exists() or paths.incoming.stat().st_size == 0:
            raise ProcessingError(
                "Aucun enregistrement n'est disponible pour cette réunion. "
                "Enregistrez une réunion ou importez un fichier audio."
            )
        report(0.1, "Finalisation de l'enregistrement")
        archive, info = audio_module.build_archive(
            paths.incoming,
            paths,
            archive_format=settings.archive_format,
            original_extension=_incoming_extension(paths),
        )
    else:
        report(0.3, "Enregistrement déjà préparé")
        info = audio_module.probe(archive)

    meeting.duration_seconds = info.duration
    meeting.audio_filename = deliverable_name(
        meeting.meeting_date, meeting.title, "audio", archive.suffix
    )
    meeting.audio_bytes = info.size_bytes

    if info.duration < 1.0:
        raise ProcessingError(
            "L'enregistrement dure moins d'une seconde : il n'y a rien à transcrire."
        )

    report(0.55, "Préparation de l'audio pour la transcription")
    if not paths.work_audio.exists():
        audio_module.make_work_audio(archive, paths.work_audio)

    notes: list[str] = []
    if info.sample_rate and info.sample_rate < 16000:
        notes.append(
            f"L'audio est échantillonné à {info.sample_rate} Hz, en dessous des "
            "16 kHz recommandés : la transcription peut être moins fidèle."
        )
    if info.bit_rate and 0 < info.bit_rate < 16000:
        notes.append(
            "Le débit audio est très faible : certains passages risquent d'être "
            "mal transcrits."
        )
    meeting.stage(Stage.AUDIO).message = (
        f"Audio prêt — {audio_module.human_duration(info.duration)}, {info.codec}"
    )
    _store_audio_notes(paths, notes)
    report(1.0, meeting.stage(Stage.AUDIO).message)


def _incoming_extension(paths: MeetingPaths) -> str | None:
    import json

    if paths.incoming_meta.exists():
        meta = json.loads(paths.incoming_meta.read_text(encoding="utf-8"))
        return meta.get("extension")
    return None


def _store_audio_notes(paths: MeetingPaths, notes: list[str]) -> None:
    import json

    (paths.analysis_dir / "audio_notes.json").write_text(
        json.dumps(notes, ensure_ascii=False), encoding="utf-8"
    )


def _load_audio_notes(paths: MeetingPaths) -> list[str]:
    import json

    path = paths.analysis_dir / "audio_notes.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


# ---------------------------------------------------------------------------
# Étape B — transcription
# ---------------------------------------------------------------------------


def _stage_transcription(meeting: Meeting, paths: MeetingPaths, settings: Settings) -> None:
    problem = settings.asr_problem()
    if problem:
        raise ProcessingError(f"Transcription impossible : {problem}")

    report = _progress(meeting, Stage.TRANSCRIPTION)
    transcript = transcribe.run_transcription(
        paths, settings, duration=meeting.duration_seconds, progress=report
    )
    save_transcript(paths, transcript)

    meeting.language = transcript.language
    meeting.transcribed_at = datetime.now(timezone.utc)
    db.index_meeting(meeting.id, meeting.title, meeting.context, transcript.plain_text(False))
    meeting.stage(Stage.TRANSCRIPTION).message = (
        f"{len(transcript.segments)} passages transcrits "
        f"({transcript.model})"
    )
    report(1.0, meeting.stage(Stage.TRANSCRIPTION).message)


def save_transcript(paths: MeetingPaths, transcript: Transcript) -> None:
    paths.transcript.parent.mkdir(parents=True, exist_ok=True)
    paths.transcript.write_text(transcript.model_dump_json(indent=1), encoding="utf-8")


def load_transcript(paths: MeetingPaths) -> Transcript | None:
    if not paths.transcript.exists():
        return None
    return Transcript.model_validate_json(paths.transcript.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Étape C — analyse
# ---------------------------------------------------------------------------


def _stage_analysis(meeting: Meeting, paths: MeetingPaths, settings: Settings) -> None:
    problem = settings.llm_problem()
    if problem:
        raise ProcessingError(f"Analyse impossible : {problem}")

    transcript = load_transcript(paths)
    if transcript is None:
        raise ProcessingError(
            "La transcription est absente : relancez l'étape de transcription."
        )

    report = _progress(meeting, Stage.ANALYSIS)
    model, usage = analyze.run_analysis(meeting, transcript, paths, settings, progress=report)
    model.quality.audio_notes = _load_audio_notes(paths)
    analyze.save_report_model(model, paths.report_model)

    quality = model.quality
    meeting.stage(Stage.ANALYSIS).message = (
        f"{len(model.topics)} thèmes, {len(model.decisions)} décisions, "
        f"{len(model.actions)} actions — {quality.items_verified}/{quality.items_total} "
        "informations vérifiées"
    )
    logger.info(
        "Analyse terminée (%s) : %s jetons en entrée, %s en sortie, %s lus en cache",
        meeting.id, usage.input_tokens, usage.output_tokens, usage.cache_read_tokens,
    )
    report(1.0, meeting.stage(Stage.ANALYSIS).message)


# ---------------------------------------------------------------------------
# Étape D — documents
# ---------------------------------------------------------------------------


def _stage_documents(meeting: Meeting, paths: MeetingPaths, settings: Settings) -> None:
    if not paths.report_model.exists():
        raise ProcessingError("L'analyse est absente : relancez l'étape d'analyse.")

    model = analyze.load_report_model(paths.report_model)
    transcript = load_transcript(paths)
    report = _progress(meeting, Stage.DOCUMENTS)

    generated = generate_documents(meeting, model, transcript, paths, progress=report)
    meeting.documents_generated_at = datetime.now(timezone.utc)
    meeting.stage(Stage.DOCUMENTS).message = f"{len(generated)} documents générés"
    report(1.0, meeting.stage(Stage.DOCUMENTS).message)


def generate_documents(
    meeting: Meeting,
    model: ReportModel,
    transcript: Transcript | None,
    paths: MeetingPaths,
    *,
    progress: Callable[[float, str], None] | None = None,
) -> list[str]:
    """Produit les quatre fichiers de diffusion des livrables 2 et 3."""
    report = progress or (lambda *_: None)
    # On repart d'un dossier propre : un ancien document ne doit pas survivre
    # à une régénération sous un titre différent.
    if paths.documents_dir.exists():
        for old in paths.documents_dir.iterdir():
            if old.is_file():
                old.unlink()
    paths.documents_dir.mkdir(parents=True, exist_ok=True)

    jobs = [
        ("report", ".docx", docx_renderer.render_report, "Rapport complet (Word)"),
        ("report", ".pdf", pdf_renderer.render_report, "Rapport complet (PDF)"),
        ("summary", ".docx", docx_renderer.render_summary, "Synthèse (Word)"),
        ("summary", ".pdf", pdf_renderer.render_summary, "Synthèse (PDF)"),
    ]
    produced: list[str] = []
    for index, (kind, extension, renderer, label) in enumerate(jobs):
        report(index / len(jobs), f"Génération : {label}")
        name = deliverable_name(meeting.meeting_date, meeting.title, kind, extension)
        renderer(model, transcript, paths.documents_dir / name)
        produced.append(name)
    return produced
