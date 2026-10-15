"""Modèles de données du domaine.

Principe directeur : **toute information restituée dans un document doit
pouvoir être rattachée à un passage de la transcription**. C'est pourquoi la
quasi-totalite des objets d'analyse herite de `Grounded`, qui porte les
identifiants des segments sources et le résultat de la vérification
programmatique effectuee après l'analyse (voir `pipeline/grounding.py`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------


class Segment(BaseModel):
    """Un passage horodate de la transcription."""

    id: str
    start: float = Field(description="Debut en secondes depuis le debut de l'audio")
    end: float
    text: str
    speaker: str | None = None
    confidence: float | None = None
    low_confidence: bool = False
    edited: bool = False

    @property
    def timecode(self) -> str:
        total = int(self.start)
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


class Transcript(BaseModel):
    language: str
    provider: str
    model: str
    duration: float
    segments: list[Segment] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    diarization: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    edited_at: datetime | None = None

    @property
    def speakers(self) -> list[str]:
        seen: list[str] = []
        for seg in self.segments:
            if seg.speaker and seg.speaker not in seen:
                seen.append(seg.speaker)
        return seen

    @property
    def low_confidence_ratio(self) -> float:
        if not self.segments:
            return 0.0
        return sum(1 for s in self.segments if s.low_confidence) / len(self.segments)

    def plain_text(self, with_timecodes: bool = True) -> str:
        lines = []
        for seg in self.segments:
            prefix = f"[{seg.timecode}]" if with_timecodes else ""
            who = f" {seg.speaker} :" if seg.speaker else ""
            lines.append(f"{prefix}{who} {seg.text}".strip())
        return "\n".join(lines)

    def by_id(self) -> dict[str, Segment]:
        return {s.id: s for s in self.segments}


# ---------------------------------------------------------------------------
# Éléments d'analyse rattachés à la transcription
# ---------------------------------------------------------------------------


class Evidence(BaseModel):
    """Rattachement d'une information à la transcription."""

    segment_ids: list[str] = Field(default_factory=list)
    quote: str | None = Field(
        default=None, description="Extrait verbatim justifiant l'information"
    )


class Verification(BaseModel):
    """Résultat du contrôle programmatique, calculé après l'analyse.

    `status` vaut :
      - « vérifié »   : les segments cités existent et les chiffres, dates et
                        noms propres de l'énoncé se retrouvent dans ces segments ;
      - « partiel »   : les segments existent mais certains éléments factuels
                        n'y figurent pas textuellement ;
      - « non vérifié »: aucun segment valide n'est cité.
    """

    status: Literal["verifie", "partiel", "non verifie"] = "non verifie"
    unmatched: list[str] = Field(default_factory=list)
    note: str | None = None

    @property
    def is_reliable(self) -> bool:
        return self.status == "verifie"


class Grounded(BaseModel):
    id: str
    evidence: Evidence = Field(default_factory=Evidence)
    verification: Verification = Field(default_factory=Verification)


class Participant(Grounded):
    label: str
    name: str | None = None
    role: str | None = None


class KeyPoint(Grounded):
    statement: str
    kind: Literal["fait", "chiffre", "contrainte", "reference", "technique"] = "fait"


class Problem(Grounded):
    statement: str
    causes: list[str] = Field(default_factory=list)


class Solution(Grounded):
    statement: str
    status: Literal["proposee", "etudiee", "retenue", "ecartee"] = "proposee"


class Disagreement(Grounded):
    statement: str


class Decision(Grounded):
    statement: str
    rationale: str | None = None
    stakeholders: list[str] = Field(default_factory=list)
    conditions: str | None = None
    explicitness: Literal["explicite", "implicite"] = "explicite"


class ActionItem(Grounded):
    task: str
    owner: str | None = None
    due: str | None = None
    priority: Literal["haute", "moyenne", "basse"] | None = None
    dependencies: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    commitment: Literal["ferme", "propose"] = "propose"


class OpenQuestion(Grounded):
    question: str
    kind: Literal["sans reponse", "a verifier", "a reprendre"] = "sans reponse"


class RiskItem(Grounded):
    statement: str
    kind: Literal["risque", "probleme", "dependance", "difficulte"] = "risque"


class TopicSection(BaseModel):
    """Une rubrique du compte rendu détaillé."""

    id: str
    title: str
    narrative: str = ""
    key_points: list[KeyPoint] = Field(default_factory=list)
    problems: list[Problem] = Field(default_factory=list)
    solutions: list[Solution] = Field(default_factory=list)
    disagreements: list[Disagreement] = Field(default_factory=list)
    related_topics: list[str] = Field(default_factory=list)
    segment_ids: list[str] = Field(default_factory=list)

    @property
    def start(self) -> str | None:
        return self.segment_ids[0] if self.segment_ids else None


class ExecutiveSummary(BaseModel):
    objectives: list[str] = Field(default_factory=list)
    main_topics: list[str] = Field(default_factory=list)
    key_outcomes: list[str] = Field(default_factory=list)


class Conclusion(BaseModel):
    assessment: str = ""
    next_steps: list[str] = Field(default_factory=list)
    attention_points: list[str] = Field(default_factory=list)


class MeetingMeta(BaseModel):
    title: str
    date: str
    duration_label: str
    duration_seconds: float
    context: str | None = None
    subject: str | None = None
    participants: list[Participant] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


class QualityReport(BaseModel):
    """Limites connues du traitement, affichées en tête des documents."""

    audio_notes: list[str] = Field(default_factory=list)
    transcription_notes: list[str] = Field(default_factory=list)
    low_confidence_ratio: float = 0.0
    items_total: int = 0
    items_verified: int = 0
    items_partial: int = 0
    items_unverified: int = 0
    dropped_items: int = 0

    @property
    def verified_ratio(self) -> float:
        return self.items_verified / self.items_total if self.items_total else 0.0


class ReportModel(BaseModel):
    """Source unique du rapport complet ET de la synthèse.

    Les deux documents sont rendus à partir de cet objet : ils ne peuvent donc
    pas se contredire sur les décisions, les actions ou les chiffres.
    """

    meta: MeetingMeta
    executive_summary: ExecutiveSummary = Field(default_factory=ExecutiveSummary)
    topics: list[TopicSection] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    actions: list[ActionItem] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    risks: list[RiskItem] = Field(default_factory=list)
    conclusion: Conclusion = Field(default_factory=Conclusion)
    quality: QualityReport = Field(default_factory=QualityReport)
    generated_at: datetime = Field(default_factory=utcnow)
    llm_model: str = ""

    def all_key_points(self) -> list[KeyPoint]:
        return [kp for topic in self.topics for kp in topic.key_points]


# ---------------------------------------------------------------------------
# Réunion et états de traitement
# ---------------------------------------------------------------------------


class MeetingStatus(StrEnum):
    CREATED = "created"          # créée, pas encore d'audio exploitable
    RECORDING = "recording"      # enregistrement en cours
    READY = "ready"              # audio disponible, traitement non lance
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Stage(StrEnum):
    AUDIO = "audio"
    TRANSCRIPTION = "transcription"
    ANALYSIS = "analysis"
    DOCUMENTS = "documents"


STAGE_LABELS: dict[Stage, str] = {
    Stage.AUDIO: "Préparation de l'audio",
    Stage.TRANSCRIPTION: "Transcription",
    Stage.ANALYSIS: "Analyse et structuration",
    Stage.DOCUMENTS: "Génération des documents",
}

STAGE_ORDER: list[Stage] = [Stage.AUDIO, Stage.TRANSCRIPTION, Stage.ANALYSIS, Stage.DOCUMENTS]


class StageState(BaseModel):
    stage: Stage
    status: Literal["pending", "running", "done", "failed"] = "pending"
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def label(self) -> str:
        return STAGE_LABELS[self.stage]


class Meeting(BaseModel):
    id: str
    title: str
    meeting_date: str
    context: str | None = None
    notes: str | None = None
    status: MeetingStatus = MeetingStatus.CREATED
    source: Literal["recording", "import"] = "recording"
    duration_seconds: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    documents_generated_at: datetime | None = None
    transcribed_at: datetime | None = None
    error: str | None = None
    stages: list[StageState] = Field(default_factory=list)
    audio_filename: str | None = None
    audio_bytes: int = 0
    language: str | None = None

    def stage(self, stage: Stage) -> StageState:
        for st in self.stages:
            if st.stage == stage:
                return st
        new = StageState(stage=stage)
        self.stages.append(new)
        return new

    @property
    def overall_progress(self) -> float:
        if not self.stages:
            return 0.0
        return sum(s.progress for s in self.stages) / len(STAGE_ORDER)
