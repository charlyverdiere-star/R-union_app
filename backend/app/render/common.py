"""Éléments partagés par les deux générateurs de documents.

Le rapport complet et la synthèse sont produits à partir du même
`ReportModel` et des mêmes fonctions de mise en forme : ils ne peuvent donc
pas se contredire sur une décision, un chiffre ou une action.
"""

from __future__ import annotations

from ..models import Grounded, ReportModel, Segment, Transcript

#: Marque apposée aux informations dont une partie n'a pas pu être retrouvée
#: textuellement dans le passage cité.
TO_VERIFY = "[à vérifier]"

DISCLAIMER = (
    "Document généré automatiquement à partir de l'enregistrement de la réunion. "
    "Les horodatages entre crochets renvoient au passage correspondant de "
    "l'audio et de la transcription. Les mentions « à vérifier » signalent une "
    "information dont une partie n'a pas pu être retrouvée mot pour mot dans le "
    "passage cité : relisez-la avant diffusion."
)

# Libellés affichés. Les clés sont les codes internes du modèle de données,
# volontairement en ASCII ; seules les valeurs sont destinées à la lecture.
COMMITMENT_LABELS = {"ferme": "engagement ferme", "propose": "action proposée"}
EXPLICITNESS_LABELS = {"explicite": "décision explicite", "implicite": "décision implicite"}
SOLUTION_LABELS = {
    "proposee": "proposée",
    "etudiee": "étudiée",
    "retenue": "retenue",
    "ecartee": "écartée",
}

QUESTION_LABELS = {
    "sans reponse": "sans réponse",
    "a verifier": "à vérifier",
    "a reprendre": "à reprendre",
}

RISK_LABELS = {
    "risque": "risque",
    "probleme": "problème",
    "dependance": "dépendance",
    "difficulte": "difficulté",
}

KEYPOINT_LABELS = {
    "fait": "fait",
    "chiffre": "chiffre",
    "contrainte": "contrainte",
    "reference": "référence",
    "technique": "donnée technique",
}


def timecode_of(item: Grounded, segments_by_id: dict[str, Segment]) -> str | None:
    """Horodatage du premier passage source, pour retrouver l'extrait audio."""
    for segment_id in item.evidence.segment_ids:
        segment = segments_by_id.get(segment_id)
        if segment:
            return segment.timecode
    return None


def reference(item: Grounded, segments_by_id: dict[str, Segment]) -> str:
    """Suffixe « [00:12:34] » et, le cas échéant, « [à vérifier] »."""
    parts = []
    code = timecode_of(item, segments_by_id)
    if code:
        parts.append(f"[{code}]")
    if item.verification.status == "partiel":
        parts.append(TO_VERIFY)
    return " " + " ".join(parts) if parts else ""


def segments_index(transcript: Transcript | None) -> dict[str, Segment]:
    return transcript.by_id() if transcript else {}


def has_content(model: ReportModel) -> bool:
    return bool(
        model.topics
        or model.decisions
        or model.actions
        or model.open_questions
        or model.risks
    )


def action_row(action, segments_by_id: dict[str, Segment]) -> list[str]:
    """Ligne du tableau des actions. Les champs absents restent vides :
    aucun responsable ni aucune échéance n'est supposée."""
    return [
        action.task + (f" {TO_VERIFY}" if action.verification.status == "partiel" else ""),
        action.owner or "non précisé",
        action.due or "non précisée",
        COMMITMENT_LABELS.get(action.commitment, action.commitment),
        timecode_of(action, segments_by_id) or "",
    ]


ACTION_HEADERS = ["Action", "Responsable", "Échéance", "Nature", "Repère"]


def decision_lines(decision, segments_by_id: dict[str, Segment]) -> list[tuple[str, str]]:
    """Détails d'une décision sous forme (intitulé, valeur)."""
    lines: list[tuple[str, str]] = []
    if decision.rationale:
        lines.append(("Contexte et justification", decision.rationale))
    if decision.stakeholders:
        lines.append(("Personnes concernees", ", ".join(decision.stakeholders)))
    if decision.conditions:
        lines.append(("Conditions et réserves", decision.conditions))
    lines.append(("Nature", EXPLICITNESS_LABELS.get(decision.explicitness, decision.explicitness)))
    return lines


def quality_lines(model: ReportModel) -> list[str]:
    """Phrases décrivant les limites du traitement, en tête des documents."""
    quality = model.quality
    lines: list[str] = []
    if quality.items_total:
        lines.append(
            f"{quality.items_verified} information(s) sur {quality.items_total} ont été "
            f"retrouvées mot pour mot dans le passage cité ; {quality.items_partial} "
            "sont signalées « à vérifier »."
        )
    if quality.dropped_items:
        lines.append(
            f"{quality.dropped_items} information(s) proposée(s) par l'analyse ont été "
            "écartées faute de passage justificatif."
        )
    if quality.low_confidence_ratio > 0.05:
        lines.append(
            f"{quality.low_confidence_ratio:.0%} des passages transcrits ont une "
            "confiance faible."
        )
    lines.extend(quality.audio_notes)
    lines.extend(quality.transcription_notes)
    return lines


def summary_sections(model: ReportModel, segments_by_id: dict[str, Segment]) -> list[tuple[str, list[str]]]:
    """Contenu de la synthèse, sous forme (titre, lignes).

    Construit à partir du même `ReportModel` que le rapport complet : c'est ce
    qui garantit l'absence de contradiction entre les deux documents.
    """
    sections: list[tuple[str, list[str]]] = []

    essentials: list[str] = []
    if model.executive_summary.objectives:
        essentials.append("Objectif : " + " ".join(model.executive_summary.objectives))
    if model.executive_summary.key_outcomes:
        essentials.extend(model.executive_summary.key_outcomes)
    if essentials:
        sections.append(("L'essentiel", essentials))

    key_points = [
        kp for kp in model.all_key_points()
        if kp.kind in ("chiffre", "technique", "contrainte", "reference")
    ] or model.all_key_points()
    if key_points:
        sections.append((
            "Informations clés",
            [kp.statement + reference(kp, segments_by_id) for kp in key_points[:12]],
        ))

    if model.decisions:
        sections.append((
            "Décisions importantes",
            [
                f"{d.statement}"
                f"{' — ' + EXPLICITNESS_LABELS[d.explicitness] if d.explicitness == 'implicite' else ''}"
                f"{reference(d, segments_by_id)}"
                for d in model.decisions
            ],
        ))

    if model.risks:
        sections.append((
            "Points de vigilance",
            [
                f"{r.statement} ({RISK_LABELS.get(r.kind, r.kind)})"
                f"{reference(r, segments_by_id)}"
                for r in model.risks
            ],
        ))

    if model.open_questions:
        sections.append((
            "Questions en suspens",
            [
                f"{q.question} ({QUESTION_LABELS.get(q.kind, q.kind)})"
                f"{reference(q, segments_by_id)}"
                for q in model.open_questions
            ],
        ))

    if model.conclusion.next_steps:
        sections.append(("Prochaines étapes", list(model.conclusion.next_steps)))

    return sections
