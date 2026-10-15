"""Schémas des réponses attendues du modèle de langage.

Ces modèles servent de contrat de sortie structurée : le modèle ne répond pas
en texte libre, il remplit ces champs. Tous les champs sont obligatoires (les
valeurs facultatives sont explicitement `None`), ce qui oblige le modèle à se
prononcer sur chaque rubrique plutôt qu'a en omettre discretement.

Règle commune à toutes les extractions : `segment_ids` reference les segments
de la transcription fournis dans la fenêtre analysée, et `quote` reprend mot
pour mot un extrait de ces segments. C'est ce qui rend la vérification
mécanique possible (voir `grounding.py`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ExtractedTopic(BaseModel):
    title: str = Field(description="Intitulé court du thème aborde")
    summary: str = Field(description="Résumé factuel du thème en une à trois phrases")
    segment_ids: list[str]


class ExtractedKeyPoint(BaseModel):
    statement: str
    kind: Literal["fait", "chiffre", "contrainte", "reference", "technique"]
    segment_ids: list[str]
    quote: str


class ExtractedProblem(BaseModel):
    statement: str
    causes: list[str] = Field(description="Causes évoquées par les participants, sinon liste vide")
    segment_ids: list[str]
    quote: str


class ExtractedSolution(BaseModel):
    statement: str
    status: Literal["proposee", "etudiee", "retenue", "ecartee"]
    segment_ids: list[str]
    quote: str


class ExtractedDecision(BaseModel):
    statement: str
    rationale: str | None
    stakeholders: list[str] = Field(description="Uniquement les personnes nommées explicitement")
    conditions: str | None
    explicitness: Literal["explicite", "implicite"]
    segment_ids: list[str]
    quote: str


class ExtractedAction(BaseModel):
    task: str
    owner: str | None = Field(description="Uniquement si un responsable est nommé explicitement")
    due: str | None = Field(description="Uniquement si une échéance est énoncée")
    priority: Literal["haute", "moyenne", "basse"] | None
    dependencies: list[str]
    blockers: list[str]
    commitment: Literal["ferme", "propose"]
    segment_ids: list[str]
    quote: str


class ExtractedQuestion(BaseModel):
    question: str
    kind: Literal["sans reponse", "a verifier", "a reprendre"]
    segment_ids: list[str]
    quote: str


class ExtractedRisk(BaseModel):
    statement: str
    kind: Literal["risque", "probleme", "dependance", "difficulte"]
    segment_ids: list[str]
    quote: str


class ExtractedDisagreement(BaseModel):
    statement: str
    segment_ids: list[str]
    quote: str


class ExtractedParticipant(BaseModel):
    label: str = Field(description="Étiquette telle qu'elle apparaît, ex. « Locuteur 1 »")
    name: str | None = Field(description="Nom réel uniquement s'il est prononcé")
    role: str | None
    segment_ids: list[str]


class ExtractedUnclear(BaseModel):
    description: str = Field(description="Ce qui n'a pas pu être compris ou reste ambigu")
    segment_ids: list[str]


class WindowExtraction(BaseModel):
    """Résultat de l'analyse d'une fenêtre de transcription."""

    topics: list[ExtractedTopic]
    key_points: list[ExtractedKeyPoint]
    problems: list[ExtractedProblem]
    solutions: list[ExtractedSolution]
    decisions: list[ExtractedDecision]
    actions: list[ExtractedAction]
    open_questions: list[ExtractedQuestion]
    risks: list[ExtractedRisk]
    disagreements: list[ExtractedDisagreement]
    participants: list[ExtractedParticipant]
    unclear_passages: list[ExtractedUnclear]


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------


class MergeGroup(BaseModel):
    """Regroupement d'éléments extraits désignant la même réalité.

    `member_ids` référence les identifiants attribués aux éléments extraits.
    Les champs structures (responsable, échéance...) ne sont PAS redemandes au
    modèle à cette étape : ils sont repris des éléments d'origine, ce qui
    interdit l'apparition d'un responsable ou d'une date qui n'existait pas.
    """

    canonical: str = Field(description="Formulation retenue, fidèle aux éléments regroupés")
    member_ids: list[str]


class TopicGroup(BaseModel):
    title: str
    summary: str
    member_ids: list[str]
    related_titles: list[str] = Field(description="Titres d'autres thèmes liés, sinon liste vide")


class ExecutiveSummaryOut(BaseModel):
    objectives: list[str]
    main_topics: list[str]
    key_outcomes: list[str]


class ConclusionOut(BaseModel):
    assessment: str
    next_steps: list[str]
    attention_points: list[str]


class ReduceResult(BaseModel):
    """Consolidation de l'ensemble des fenêtres."""

    subject: str | None
    missing_information: list[str]
    topics: list[TopicGroup]
    key_points: list[MergeGroup]
    problems: list[MergeGroup]
    solutions: list[MergeGroup]
    decisions: list[MergeGroup]
    actions: list[MergeGroup]
    open_questions: list[MergeGroup]
    risks: list[MergeGroup]
    disagreements: list[MergeGroup]
    participants: list[MergeGroup]
    executive_summary: ExecutiveSummaryOut
    conclusion: ConclusionOut


class TopicNarrative(BaseModel):
    """Rédaction du compte rendu détaillé pour un thème."""

    narrative: str = Field(
        description="Plusieurs paragraphes en français professionnel, fidèles aux échanges"
    )
