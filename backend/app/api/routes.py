"""Routes HTTP de l'application."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse

from .. import db, storage
from ..config import Settings, get_settings
from ..models import Meeting, MeetingStatus, Segment, Stage, Transcript
from ..pipeline import analyze, audio as audio_module, orchestrator, transcribe
from .schemas import (
    CompleteRecording,
    DeliverableOut,
    MeetingCreate,
    MeetingOut,
    MeetingUpdate,
    ProcessRequest,
    ReportSummaryOut,
    TranscriptOut,
    TranscriptUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

CHUNK_SIZE = 1024 * 1024

MIME_EXTENSIONS = {
    "audio/webm": ".webm", "video/webm": ".webm",
    "audio/ogg": ".ogg", "audio/mp4": ".m4a", "video/mp4": ".m4a",
    "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
    "audio/flac": ".flac", "audio/x-m4a": ".m4a", "audio/aac": ".aac",
}

DOCUMENT_MEDIA = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
}

AUDIO_MEDIA = {
    ".ogg": "audio/ogg", ".webm": "audio/webm", ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
    ".mka": "audio/x-matroska", ".aac": "audio/aac",
}


# ---------------------------------------------------------------------------
# Sécurité
# ---------------------------------------------------------------------------


def require_auth(x_auth_token: str | None = Header(default=None)) -> None:
    """Jeton partagé, actif uniquement si AUTH_TOKEN est renseigné."""
    expected = get_settings().auth_token
    if expected and x_auth_token != expected:
        raise HTTPException(status_code=401, detail="Jeton d'accès invalide ou absent.")


Secured = [Depends(require_auth)]


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------


def _meeting_or_404(meeting_id: str) -> Meeting:
    meeting = db.get_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Réunion introuvable.")
    return meeting


def _deliverables(meeting: Meeting, paths: storage.MeetingPaths) -> list[DeliverableOut]:
    """État des trois livrables : disponible ou non, et sous quels formats."""
    results: list[DeliverableOut] = []

    archive = paths.find_archive()
    results.append(
        DeliverableOut(
            kind="audio",
            label="Enregistrement audio brut",
            filename=meeting.audio_filename
            or storage.deliverable_name(
                meeting.meeting_date, meeting.title, "audio", archive.suffix if archive else ".ogg"
            ),
            size_bytes=archive.stat().st_size if archive else 0,
            available=archive is not None,
            formats=[archive.suffix.lstrip(".")] if archive else [],
        )
    )

    for kind, label in (("report", "Rapport complet"), ("summary", "Synthèse essentielle")):
        formats = []
        size = 0
        name = storage.deliverable_name(meeting.meeting_date, meeting.title, kind, ".docx")
        for extension in (".docx", ".pdf"):
            candidate = paths.documents_dir / storage.deliverable_name(
                meeting.meeting_date, meeting.title, kind, extension
            )
            if candidate.exists():
                formats.append(extension.lstrip("."))
                size += candidate.stat().st_size
        results.append(
            DeliverableOut(
                kind=kind, label=label, filename=name, size_bytes=size,
                available=bool(formats), formats=formats,
            )
        )
    return results


def _to_out(meeting: Meeting) -> MeetingOut:
    paths = storage.meeting_paths(meeting.id)
    return MeetingOut.build(
        meeting,
        _deliverables(meeting, paths),
        has_transcript=paths.transcript.exists(),
        is_running=orchestrator.is_running(meeting.id),
        duration_label=audio_module.human_duration(meeting.duration_seconds),
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@router.get("/config")
def read_config(settings: Settings = Depends(get_settings)) -> dict:
    """État de la configuration, sans aucun secret (voir `Settings.public_state`)."""
    return settings.public_state()


# ---------------------------------------------------------------------------
# Réunions
# ---------------------------------------------------------------------------


@router.get("/meetings", dependencies=Secured)
def list_meetings(
    query: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
) -> list[MeetingOut]:
    meetings = db.list_meetings(query=query, status=status, date_from=date_from, date_to=date_to)
    return [_to_out(m) for m in meetings]


@router.post("/meetings", status_code=201, dependencies=Secured)
def create_meeting(payload: MeetingCreate) -> MeetingOut:
    meeting = Meeting(
        id=uuid.uuid4().hex,
        title=payload.title.strip(),
        meeting_date=payload.meeting_date,
        context=payload.context,
        notes=payload.notes,
        source=payload.source,
        status=MeetingStatus.CREATED,
    )
    storage.meeting_paths(meeting.id).ensure()
    db.save_meeting(meeting)
    db.index_meeting(meeting.id, meeting.title, meeting.context, "")
    return _to_out(meeting)


@router.get("/meetings/{meeting_id}", dependencies=Secured)
def read_meeting(meeting_id: str) -> MeetingOut:
    return _to_out(_meeting_or_404(meeting_id))


@router.patch("/meetings/{meeting_id}", dependencies=Secured)
def update_meeting(meeting_id: str, payload: MeetingUpdate) -> MeetingOut:
    meeting = _meeting_or_404(meeting_id)
    renamed = payload.title is not None and payload.title.strip() != meeting.title
    redated = payload.meeting_date is not None and payload.meeting_date != meeting.meeting_date

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(meeting, field, value.strip() if isinstance(value, str) else value)

    paths = storage.meeting_paths(meeting_id)
    if (renamed or redated) and paths.report_model.exists():
        # Les noms de fichiers des livrables dérivent du titre et de la date :
        # on les régénère pour qu'ils restent cohérents.
        model = analyze.load_report_model(paths.report_model)
        model.meta.title = meeting.title
        model.meta.date = meeting.meeting_date
        analyze.save_report_model(model, paths.report_model)
        orchestrator.generate_documents(
            meeting, model, orchestrator.load_transcript(paths), paths
        )
        meeting.documents_generated_at = datetime.now(timezone.utc)

    if renamed:
        archive = paths.find_archive()
        if archive:
            meeting.audio_filename = storage.deliverable_name(
                meeting.meeting_date, meeting.title, "audio", archive.suffix
            )

    db.save_meeting(meeting)
    transcript = orchestrator.load_transcript(paths)
    db.index_meeting(
        meeting.id, meeting.title, meeting.context,
        transcript.plain_text(False) if transcript else "",
    )
    return _to_out(meeting)


@router.delete("/meetings/{meeting_id}", status_code=204, dependencies=Secured)
def delete_meeting(meeting_id: str) -> None:
    _meeting_or_404(meeting_id)
    if orchestrator.is_running(meeting_id):
        raise HTTPException(
            status_code=409,
            detail="Un traitement est en cours sur cette réunion. Attendez qu'il se termine.",
        )
    storage.delete_meeting_files(meeting_id)
    db.delete_meeting(meeting_id)


# ---------------------------------------------------------------------------
# Enregistrement
# ---------------------------------------------------------------------------


@router.post("/meetings/{meeting_id}/recording/chunk", dependencies=Secured)
async def upload_chunk(
    meeting_id: str,
    request: Request,
    seq: int = Query(ge=0),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Reçoit un morceau de l'enregistrement en cours et l'ajoute au fichier.

    L'écriture est immédiate : si le navigateur ou la machine s'arrête, tout
    ce qui a été transmis est déjà sur le disque.
    """
    meeting = _meeting_or_404(meeting_id)
    paths = storage.meeting_paths(meeting_id)
    paths.ensure()

    expected = _next_sequence(paths)
    if seq != expected:
        # Le client renvoie à partir du morceau attendu : aucun trou, aucun doublon.
        raise HTTPException(
            status_code=409,
            detail=f"Morceau {seq} inattendu, le serveur attend le morceau {expected}.",
            headers={"X-Expected-Seq": str(expected)},
        )

    limit = settings.max_upload_mb * 1024 * 1024
    written = 0
    with paths.incoming.open("ab") as handle:
        async for block in request.stream():
            written += len(block)
            if paths.incoming.stat().st_size + written > limit:
                raise HTTPException(
                    status_code=413,
                    detail=f"L'enregistrement dépasse la limite de {settings.max_upload_mb} Mo.",
                )
            handle.write(block)

    _set_sequence(paths, seq + 1)
    if meeting.status == MeetingStatus.CREATED:
        meeting.status = MeetingStatus.RECORDING
        db.save_meeting(meeting)
    return {"received": seq, "next": seq + 1, "bytes": paths.incoming.stat().st_size}


def _meta(paths: storage.MeetingPaths) -> dict:
    if paths.incoming_meta.exists():
        return json.loads(paths.incoming_meta.read_text(encoding="utf-8"))
    return {}


def _next_sequence(paths: storage.MeetingPaths) -> int:
    return int(_meta(paths).get("next_seq", 0))


def _set_sequence(paths: storage.MeetingPaths, value: int, **extra) -> None:
    meta = _meta(paths)
    meta["next_seq"] = value
    meta.update(extra)
    paths.incoming_meta.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


@router.get("/meetings/{meeting_id}/recording/state", dependencies=Secured)
def recording_state(meeting_id: str) -> dict:
    """Permet au navigateur de reprendre un envoi interrompu au bon endroit."""
    _meeting_or_404(meeting_id)
    paths = storage.meeting_paths(meeting_id)
    return {
        "next_seq": _next_sequence(paths),
        "bytes": paths.incoming.stat().st_size if paths.incoming.exists() else 0,
    }


@router.post("/meetings/{meeting_id}/recording/complete", dependencies=Secured)
def complete_recording(meeting_id: str, payload: CompleteRecording) -> MeetingOut:
    meeting = _meeting_or_404(meeting_id)
    paths = storage.meeting_paths(meeting_id)

    if not paths.incoming.exists() or paths.incoming.stat().st_size == 0:
        raise HTTPException(
            status_code=400,
            detail="Aucune donnée audio n'a été reçue pour cette réunion.",
        )

    extension = MIME_EXTENSIONS.get(payload.mime_type.split(";")[0].strip(), ".webm")
    _set_sequence(paths, _next_sequence(paths), extension=extension, mime_type=payload.mime_type)

    meeting.status = MeetingStatus.READY
    if payload.duration_seconds:
        meeting.duration_seconds = payload.duration_seconds
    db.save_meeting(meeting)

    if payload.start_processing:
        orchestrator.start(meeting_id)
    return _to_out(_meeting_or_404(meeting_id))


@router.post("/meetings/{meeting_id}/import", dependencies=Secured)
async def import_audio(
    meeting_id: str,
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
) -> MeetingOut:
    """Importe un fichier audio existant à la place d'un enregistrement."""
    meeting = _meeting_or_404(meeting_id)
    paths = storage.meeting_paths(meeting_id)
    paths.ensure()

    extension = Path(file.filename or "").suffix.lower() or MIME_EXTENSIONS.get(
        (file.content_type or "").split(";")[0], ".bin"
    )
    limit = settings.max_upload_mb * 1024 * 1024
    total = 0
    with paths.incoming.open("wb") as handle:
        while block := await file.read(CHUNK_SIZE):
            total += len(block)
            if total > limit:
                paths.incoming.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"Le fichier dépasse la limite de {settings.max_upload_mb} Mo.",
                )
            handle.write(block)

    if total == 0:
        raise HTTPException(status_code=400, detail="Le fichier importé est vide.")

    _set_sequence(paths, 0, extension=extension, mime_type=file.content_type or "")
    meeting.source = "import"
    meeting.status = MeetingStatus.READY
    db.save_meeting(meeting)
    return _to_out(meeting)


# ---------------------------------------------------------------------------
# Traitement
# ---------------------------------------------------------------------------


@router.post("/meetings/{meeting_id}/process", dependencies=Secured)
def process(meeting_id: str, payload: ProcessRequest | None = None) -> MeetingOut:
    meeting = _meeting_or_404(meeting_id)
    paths = storage.meeting_paths(meeting_id)

    if paths.find_archive() is None and not paths.incoming.exists():
        raise HTTPException(
            status_code=400,
            detail="Aucun enregistrement n'est disponible pour cette réunion.",
        )

    from_stage = Stage(payload.from_stage) if payload and payload.from_stage else None
    if not orchestrator.start(meeting_id, from_stage=from_stage):
        raise HTTPException(
            status_code=409, detail="Un traitement est déjà en cours sur cette réunion."
        )
    return _to_out(_meeting_or_404(meeting_id))


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------


@router.get("/meetings/{meeting_id}/transcript", dependencies=Secured)
def read_transcript(meeting_id: str) -> TranscriptOut:
    _meeting_or_404(meeting_id)
    paths = storage.meeting_paths(meeting_id)
    transcript = orchestrator.load_transcript(paths)
    if transcript is None:
        raise HTTPException(
            status_code=404, detail="Cette réunion n'a pas encore été transcrite."
        )
    return TranscriptOut(
        **transcript.model_dump(),
        speakers=transcript.speakers,
        low_confidence_ratio=transcript.low_confidence_ratio,
    )


@router.put("/meetings/{meeting_id}/transcript", dependencies=Secured)
def update_transcript(meeting_id: str, payload: TranscriptUpdate) -> TranscriptOut:
    """Enregistre une transcription corrigée à la main.

    Les analyses mises en cache deviennent caduques : elles sont effacées, et
    les étapes d'analyse et de génération repassent à « à faire » pour que
    l'utilisateur puisse régénérer les documents sans retranscrire l'audio.
    """
    meeting = _meeting_or_404(meeting_id)
    paths = storage.meeting_paths(meeting_id)
    transcript = orchestrator.load_transcript(paths)
    if transcript is None:
        raise HTTPException(
            status_code=404, detail="Cette réunion n'a pas encore été transcrite."
        )
    if orchestrator.is_running(meeting_id):
        raise HTTPException(
            status_code=409,
            detail="Un traitement est en cours : attendez la fin avant de corriger.",
        )

    previous = {s.id: s for s in transcript.segments}
    segments: list[Segment] = []
    for item in payload.segments:
        text = item.text.strip()
        if not text:
            continue  # un passage vidé est supprimé de la transcription
        original = previous.get(item.id)
        segments.append(
            Segment(
                id=item.id,
                start=item.start,
                end=max(item.end, item.start),
                text=text,
                speaker=item.speaker,
                confidence=item.confidence,
                # Un passage corrigé n'est plus « incertain » : c'est
                # l'utilisateur qui fait foi.
                low_confidence=item.low_confidence and (original is None or original.text == text),
                edited=item.edited or (original is not None and original.text != text),
            )
        )

    transcript.segments = sorted(segments, key=lambda s: s.start)
    transcribe.renumber(transcript)
    transcript.edited_at = datetime.now(timezone.utc)
    orchestrator.save_transcript(paths, transcript)
    orchestrator.invalidate_analysis(paths)

    for stage in (Stage.ANALYSIS, Stage.DOCUMENTS):
        state = meeting.stage(stage)
        state.status = "pending"
        state.progress = 0.0
        state.message = "À régénérer : la transcription a été corrigée."
        state.error = None
    if meeting.status == MeetingStatus.COMPLETED:
        meeting.status = MeetingStatus.READY
    db.save_meeting(meeting)
    db.index_meeting(meeting.id, meeting.title, meeting.context, transcript.plain_text(False))

    return TranscriptOut(
        **transcript.model_dump(),
        speakers=transcript.speakers,
        low_confidence_ratio=transcript.low_confidence_ratio,
    )


# ---------------------------------------------------------------------------
# Résultats et fichiers
# ---------------------------------------------------------------------------


@router.get("/meetings/{meeting_id}/report", dependencies=Secured)
def read_report_summary(meeting_id: str) -> ReportSummaryOut:
    _meeting_or_404(meeting_id)
    paths = storage.meeting_paths(meeting_id)
    if not paths.report_model.exists():
        raise HTTPException(status_code=404, detail="Cette réunion n'a pas encore été analysée.")
    model = analyze.load_report_model(paths.report_model)
    quality = model.quality
    return ReportSummaryOut(
        generated_at=model.generated_at,
        llm_model=model.llm_model,
        topics=len(model.topics),
        decisions=len(model.decisions),
        actions=len(model.actions),
        open_questions=len(model.open_questions),
        risks=len(model.risks),
        items_total=quality.items_total,
        items_verified=quality.items_verified,
        items_partial=quality.items_partial,
        dropped_items=quality.dropped_items,
        low_confidence_ratio=quality.low_confidence_ratio,
        notes=[*quality.audio_notes, *quality.transcription_notes],
    )


@router.get("/meetings/{meeting_id}/audio", dependencies=Secured)
def stream_audio(meeting_id: str):
    """Sert l'enregistrement original. Starlette gère les requêtes « Range »,
    ce qui permet de se déplacer dans le lecteur audio."""
    meeting = _meeting_or_404(meeting_id)
    paths = storage.meeting_paths(meeting_id)
    archive = paths.find_archive()
    if archive is None:
        raise HTTPException(status_code=404, detail="Aucun enregistrement disponible.")
    filename = meeting.audio_filename or storage.deliverable_name(
        meeting.meeting_date, meeting.title, "audio", archive.suffix
    )
    return FileResponse(
        archive,
        media_type=AUDIO_MEDIA.get(archive.suffix, "application/octet-stream"),
        filename=filename,
        content_disposition_type="inline",
    )


@router.get("/meetings/{meeting_id}/download/{kind}/{extension}", dependencies=Secured)
def download(meeting_id: str, kind: str, extension: str):
    meeting = _meeting_or_404(meeting_id)
    paths = storage.meeting_paths(meeting_id)

    if kind == "audio":
        archive = paths.find_archive()
        if archive is None:
            raise HTTPException(status_code=404, detail="Aucun enregistrement disponible.")
        return FileResponse(
            archive,
            media_type=AUDIO_MEDIA.get(archive.suffix, "application/octet-stream"),
            filename=meeting.audio_filename
            or storage.deliverable_name(meeting.meeting_date, meeting.title, "audio", archive.suffix),
        )

    if kind not in ("report", "summary") or f".{extension}" not in DOCUMENT_MEDIA:
        raise HTTPException(status_code=404, detail="Document inconnu.")

    name = storage.deliverable_name(meeting.meeting_date, meeting.title, kind, f".{extension}")
    path = paths.documents_dir / name
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Ce document n'a pas encore été généré pour cette réunion.",
        )
    return FileResponse(path, media_type=DOCUMENT_MEDIA[f".{extension}"], filename=name)


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------


@router.post("/maintenance/retention", dependencies=Secured)
def apply_retention(settings: Settings = Depends(get_settings)) -> dict:
    """Supprime les réunions au-delà de la durée de conservation configurée."""
    if settings.retention_days <= 0:
        return {"deleted": 0, "detail": "Aucune politique de conservation n'est active."}

    limit = (date.today() - timedelta(days=settings.retention_days)).isoformat()
    deleted = []
    for meeting_id in db.meetings_older_than(limit):
        if orchestrator.is_running(meeting_id):
            continue
        storage.delete_meeting_files(meeting_id)
        db.delete_meeting(meeting_id)
        deleted.append(meeting_id)
    return {"deleted": len(deleted), "before": limit}
