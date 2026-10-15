"""Objets échangés avec l'interface."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..models import Meeting, Segment


class MeetingCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    # `validate_default` fait passer la valeur par défaut par le validateur,
    # qui la remplace alors par la date du jour.
    meeting_date: str | None = Field(default=None, validate_default=True)
    context: str | None = Field(default=None, max_length=4000)
    notes: str | None = Field(default=None, max_length=4000)
    source: Literal["recording", "import"] = "recording"

    @field_validator("meeting_date")
    @classmethod
    def _valid_date(cls, value: str | None) -> str:
        if not value:
            return date.today().isoformat()
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError("La date doit être au format AAAA-MM-JJ.") from exc


class MeetingUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    meeting_date: str | None = None
    context: str | None = Field(default=None, max_length=4000)
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("meeting_date")
    @classmethod
    def _valid_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError("La date doit être au format AAAA-MM-JJ.") from exc


class StageOut(BaseModel):
    stage: str
    label: str
    status: str
    progress: float
    message: str
    error: str | None


class DeliverableOut(BaseModel):
    kind: Literal["audio", "report", "summary"]
    label: str
    filename: str
    size_bytes: int
    available: bool
    formats: list[str] = Field(default_factory=list)


class MeetingOut(BaseModel):
    id: str
    title: str
    meeting_date: str
    context: str | None
    notes: str | None
    status: str
    source: str
    duration_seconds: float
    duration_label: str
    created_at: datetime
    updated_at: datetime
    documents_generated_at: datetime | None
    transcribed_at: datetime | None
    error: str | None
    language: str | None
    audio_bytes: int
    stages: list[StageOut]
    overall_progress: float
    deliverables: list[DeliverableOut]
    has_transcript: bool
    is_running: bool

    @classmethod
    def build(
        cls,
        meeting: Meeting,
        deliverables: list[DeliverableOut],
        *,
        has_transcript: bool,
        is_running: bool,
        duration_label: str,
    ) -> "MeetingOut":
        return cls(
            **meeting.model_dump(exclude={"stages"}),
            duration_label=duration_label,
            stages=[
                StageOut(
                    stage=str(s.stage), label=s.label, status=s.status,
                    progress=s.progress, message=s.message, error=s.error,
                )
                for s in meeting.stages
            ],
            overall_progress=meeting.overall_progress,
            deliverables=deliverables,
            has_transcript=has_transcript,
            is_running=is_running,
        )


class SegmentIn(BaseModel):
    id: str
    start: float
    end: float
    text: str
    speaker: str | None = None
    confidence: float | None = None
    low_confidence: bool = False
    edited: bool = False


class TranscriptUpdate(BaseModel):
    segments: list[SegmentIn]


class TranscriptOut(BaseModel):
    language: str
    provider: str
    model: str
    duration: float
    diarization: bool
    warnings: list[str]
    created_at: datetime
    edited_at: datetime | None
    segments: list[Segment]
    speakers: list[str]
    low_confidence_ratio: float


class ProcessRequest(BaseModel):
    from_stage: Literal["audio", "transcription", "analysis", "documents"] | None = None


class CompleteRecording(BaseModel):
    mime_type: str = "audio/webm"
    duration_seconds: float | None = None
    start_processing: bool = True


class ReportSummaryOut(BaseModel):
    """Aperçu du rapport affiché dans l'écran de résultats."""

    generated_at: datetime | None
    llm_model: str
    topics: int
    decisions: int
    actions: int
    open_questions: int
    risks: int
    items_total: int
    items_verified: int
    items_partial: int
    dropped_items: int
    low_confidence_ratio: float
    notes: list[str]
