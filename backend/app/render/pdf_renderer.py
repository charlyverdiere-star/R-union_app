"""Génération des versions PDF des livrables 2 et 3.

Les PDF sont produits à partir du même `ReportModel` que les DOCX : il ne
s'agit pas d'une conversion du fichier Word (qui exigerait LibreOffice) mais
d'un rendu parallele, ce qui evite une dependance lourde et garantit un
résultat identique sur Windows, macOS et Linux.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..models import ReportModel, Transcript
from . import common

ACCENT = colors.HexColor("#1F3A5F")
MUTED = colors.HexColor("#5A626B")
WARNING = colors.HexColor("#8A5200")
LIGHT = colors.HexColor("#EEF2F7")

NOTHING_FOUND = "Aucun élément de ce type n'a été identifié dans cette réunion."


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]
    body = ParagraphStyle(
        "Corps", parent=base, fontName="Helvetica", fontSize=10,
        leading=14.5, spaceAfter=5, alignment=TA_JUSTIFY,
    )
    return {
        "title": ParagraphStyle(
            "Titre", parent=body, fontName="Helvetica-Bold", fontSize=20,
            leading=24, textColor=ACCENT, spaceAfter=2, alignment=0,
        ),
        "subtitle": ParagraphStyle(
            "SousTitre", parent=body, fontSize=10.5, textColor=MUTED,
            spaceAfter=10, alignment=0,
        ),
        "section": ParagraphStyle(
            "Section", parent=body, fontName="Helvetica-Bold", fontSize=13.5,
            leading=17, textColor=ACCENT, spaceBefore=14, spaceAfter=5, alignment=0,
        ),
        "subsection": ParagraphStyle(
            "SousSection", parent=body, fontName="Helvetica-Bold", fontSize=11.5,
            leading=15, spaceBefore=9, spaceAfter=3, alignment=0,
        ),
        "label": ParagraphStyle(
            "Intitulé", parent=body, fontName="Helvetica-Oblique", fontSize=9.5,
            textColor=MUTED, spaceBefore=6, spaceAfter=2, alignment=0,
        ),
        "body": body,
        "bullet": ParagraphStyle("Puce", parent=body, spaceAfter=2, alignment=0),
        "note": ParagraphStyle(
            "Note", parent=body, fontName="Helvetica-Oblique", fontSize=8.8,
            leading=12, textColor=MUTED, alignment=0,
        ),
        "warning": ParagraphStyle(
            "Avertissement", parent=body, fontName="Helvetica-Oblique", fontSize=8.8,
            leading=12, textColor=WARNING, alignment=0,
        ),
        "cell": ParagraphStyle("Cellule", parent=body, fontSize=9, leading=12, spaceAfter=0, alignment=0),
        "cellhead": ParagraphStyle(
            "CelluleEntete", parent=body, fontName="Helvetica-Bold", fontSize=9,
            leading=12, spaceAfter=0, textColor=colors.white, alignment=0,
        ),
        "footer": ParagraphStyle(
            "PiedDePage", parent=body, fontName="Helvetica-Oblique", fontSize=8,
            textColor=MUTED, alignment=TA_RIGHT, spaceBefore=16,
        ),
    }


def _escape(text: str) -> str:
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _bullets(lines: list[str], style: ParagraphStyle) -> list:
    items = [
        ListItem(Paragraph(_escape(line.strip()), style), leftIndent=10)
        for line in lines
        if line and line.strip()
    ]
    if not items:
        return []
    return [ListFlowable(items, bulletType="bullet", bulletFontSize=7, leftIndent=12, spaceAfter=4)]


def _info_table(rows: list[tuple[str, str]], styles: dict) -> Table:
    data = [
        [Paragraph(f"<b>{_escape(name)}</b>", styles["cell"]), Paragraph(_escape(value), styles["cell"])]
        for name, value in rows
    ]
    table = Table(data, colWidths=[38 * mm, 128 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), LIGHT),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D5DCE4")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])
    )
    return table


def _grid_table(headers: list[str], rows: list[list[str]], widths: list[float], styles: dict) -> Table:
    data = [[Paragraph(_escape(h), styles["cellhead"]) for h in headers]]
    data += [[Paragraph(_escape(value), styles["cell"]) for value in row] for row in rows]
    table = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1)
    table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D5DCE4")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FC")]),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])
    )
    return table


def _document(target: Path, title: str) -> SimpleDocTemplate:
    target.parent.mkdir(parents=True, exist_ok=True)
    return SimpleDocTemplate(
        str(target), pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title=title, author="Assistant de réunions",
    )


# ---------------------------------------------------------------------------
# Livrable 2 — rapport complet
# ---------------------------------------------------------------------------


def render_report(model: ReportModel, transcript: Transcript | None, target: Path) -> Path:
    segments = common.segments_index(transcript)
    styles = _styles()
    story: list = []

    story.append(Paragraph(_escape(model.meta.title), styles["title"]))
    story.append(Paragraph(
        f"Compte rendu de réunion — {_escape(model.meta.date)} — durée {_escape(model.meta.duration_label)}",
        styles["subtitle"],
    ))
    story.append(HRFlowable(width="100%", color=ACCENT, thickness=1.1, spaceAfter=8))
    story.append(Paragraph(_escape(common.DISCLAIMER), styles["note"]))
    story.append(Spacer(1, 6))

    # 1. Informations generales
    story.append(Paragraph("1. Informations générales", styles["section"]))
    rows = [("Titre", model.meta.title), ("Date", model.meta.date), ("Durée", model.meta.duration_label)]
    if model.meta.subject:
        rows.append(("Objet", model.meta.subject))
    if model.meta.context:
        rows.append(("Contexte", model.meta.context))
    story.append(_info_table(rows, styles))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Participants identifiés", styles["label"]))
    if model.meta.participants:
        story += _bullets(
            [
                " — ".join(
                    part for part in (p.name or p.label, p.role, f"({p.label})" if p.name else None) if part
                )
                for p in model.meta.participants
            ],
            styles["bullet"],
        )
    else:
        story.append(Paragraph(
            "Aucun participant n'a pu être identifié à partir de l'enregistrement.", styles["body"]
        ))

    if model.meta.missing_information:
        story.append(Paragraph("Informations manquantes ou non etablies", styles["label"]))
        story += _bullets(model.meta.missing_information, styles["bullet"])

    quality = common.quality_lines(model)
    if quality:
        story.append(Paragraph("Limites de ce compte rendu", styles["label"]))
        story += [Paragraph(_escape(line), styles["warning"]) for line in quality]

    # 2. Résumé exécutif
    story.append(Paragraph("2. Résumé exécutif", styles["section"]))
    summary = model.executive_summary
    wrote = False
    for label, lines in (
        ("Objectifs", summary.objectives),
        ("Principaux sujets abordes", summary.main_topics),
        ("Résultats essentiels", summary.key_outcomes),
    ):
        if lines:
            story.append(Paragraph(label, styles["label"]))
            story += _bullets(lines, styles["bullet"])
            wrote = True
    if not wrote:
        story.append(Paragraph(NOTHING_FOUND, styles["body"]))

    # 3. Compte rendu détaillé
    story.append(Paragraph("3. Compte rendu détaillé", styles["section"]))
    if not model.topics:
        story.append(Paragraph(NOTHING_FOUND, styles["body"]))
    for index, topic in enumerate(model.topics, start=1):
        story.append(Paragraph(f"3.{index} {_escape(topic.title)}", styles["subsection"]))
        for paragraph in topic.narrative.split("\n"):
            if paragraph.strip():
                story.append(Paragraph(_escape(paragraph.strip()), styles["body"]))
        if topic.key_points:
            story.append(Paragraph("Informations et données citées", styles["label"]))
            story += _bullets(
                [kp.statement + common.reference(kp, segments) for kp in topic.key_points],
                styles["bullet"],
            )
        if topic.problems:
            story.append(Paragraph("Problèmes et causes évoquées", styles["label"]))
            lines = []
            for problem in topic.problems:
                lines.append(problem.statement + common.reference(problem, segments))
                lines += [f"Cause évoquée : {c}" for c in problem.causes]
            story += _bullets(lines, styles["bullet"])
        if topic.solutions:
            story.append(Paragraph("Solutions proposées et pistes étudiées", styles["label"]))
            story += _bullets(
                [
                    f"{s.statement} ({common.SOLUTION_LABELS.get(s.status, s.status)})"
                    + common.reference(s, segments)
                    for s in topic.solutions
                ],
                styles["bullet"],
            )
        if topic.disagreements:
            story.append(Paragraph("Désaccords, réserves et questions soulevees", styles["label"]))
            story += _bullets(
                [d.statement + common.reference(d, segments) for d in topic.disagreements],
                styles["bullet"],
            )
        if topic.related_topics:
            story.append(Paragraph("En lien avec : " + _escape(", ".join(topic.related_topics)), styles["note"]))

    # 4. Décisions
    story.append(Paragraph("4. Décisions prises", styles["section"]))
    if not model.decisions:
        story.append(Paragraph(NOTHING_FOUND, styles["body"]))
    for index, decision in enumerate(model.decisions, start=1):
        block = [
            Paragraph(
                f"4.{index} {_escape(decision.statement)}{_escape(common.reference(decision, segments))}",
                styles["subsection"],
            )
        ]
        block += [
            Paragraph(f"<b>{_escape(name)} :</b> {_escape(value)}", styles["body"])
            for name, value in common.decision_lines(decision, segments)
        ]
        story.append(KeepTogether(block))

    # 5. Plan d'action
    story.append(Paragraph("5. Plan d'action", styles["section"]))
    if model.actions:
        story.append(
            _grid_table(
                common.ACTION_HEADERS,
                [common.action_row(a, segments) for a in model.actions],
                [62 * mm, 30 * mm, 28 * mm, 28 * mm, 18 * mm],
                styles,
            )
        )
        extras = [
            f"{a.task} — dépendances : {', '.join(a.dependencies + a.blockers)}"
            for a in model.actions
            if a.dependencies or a.blockers
        ]
        if extras:
            story.append(Paragraph("Dépendances et points bloquants", styles["label"]))
            story += _bullets(extras, styles["bullet"])
    else:
        story.append(Paragraph(NOTHING_FOUND, styles["body"]))

    # 6. Questions ouvertes
    story.append(Paragraph("6. Questions ouvertes et points à clarifier", styles["section"]))
    if model.open_questions:
        story += _bullets(
            [f"{q.question} ({common.QUESTION_LABELS.get(q.kind, q.kind)})" + common.reference(q, segments) for q in model.open_questions],
            styles["bullet"],
        )
    else:
        story.append(Paragraph(NOTHING_FOUND, styles["body"]))
    if model.risks:
        story.append(Paragraph("Risques et points de vigilance", styles["label"]))
        story += _bullets(
            [f"{r.statement} ({common.RISK_LABELS.get(r.kind, r.kind)})" + common.reference(r, segments) for r in model.risks],
            styles["bullet"],
        )

    # 7. Conclusion
    story.append(Paragraph("7. Conclusion", styles["section"]))
    if model.conclusion.assessment:
        story.append(Paragraph(_escape(model.conclusion.assessment), styles["body"]))
    if model.conclusion.next_steps:
        story.append(Paragraph("Prochaines étapes", styles["label"]))
        story += _bullets(model.conclusion.next_steps, styles["bullet"])
    if model.conclusion.attention_points:
        story.append(Paragraph("Éléments nécessitant une attention particulière", styles["label"]))
        story += _bullets(model.conclusion.attention_points, styles["bullet"])
    if not (model.conclusion.assessment or model.conclusion.next_steps or model.conclusion.attention_points):
        story.append(Paragraph(NOTHING_FOUND, styles["body"]))

    story.append(_footer(model, styles))
    _document(target, model.meta.title).build(story)
    return target


# ---------------------------------------------------------------------------
# Livrable 3 — synthèse
# ---------------------------------------------------------------------------


def render_summary(model: ReportModel, transcript: Transcript | None, target: Path) -> Path:
    segments = common.segments_index(transcript)
    styles = _styles()
    story: list = [
        Paragraph(f"Synthèse — {_escape(model.meta.title)}", styles["title"]),
        Paragraph(
            f"{_escape(model.meta.date)} — durée {_escape(model.meta.duration_label)} — lecture en 2 minutes",
            styles["subtitle"],
        ),
        HRFlowable(width="100%", color=ACCENT, thickness=1.1, spaceAfter=8),
    ]

    for title, lines in common.summary_sections(model, segments):
        story.append(Paragraph(_escape(title), styles["section"]))
        story += _bullets(lines, styles["bullet"])

    story.append(Paragraph("Actions à retenir", styles["section"]))
    if model.actions:
        story.append(
            _grid_table(
                common.ACTION_HEADERS[:4],
                [common.action_row(a, segments)[:4] for a in model.actions],
                [78 * mm, 32 * mm, 30 * mm, 26 * mm],
                styles,
            )
        )
    else:
        story.append(Paragraph("Aucune action n'a été identifiée.", styles["body"]))

    quality = common.quality_lines(model)
    if quality:
        story.append(Paragraph("Fiabilité", styles["section"]))
        story += [Paragraph(_escape(line), styles["warning"]) for line in quality]

    story.append(_footer(model, styles))
    _document(target, f"Synthèse — {model.meta.title}").build(story)
    return target


def _footer(model: ReportModel, styles: dict) -> Paragraph:
    return Paragraph(
        f"Généré le {model.generated_at.strftime('%d/%m/%Y à %H:%M')} "
        f"— analyse : {_escape(model.llm_model or 'non précisée')}",
        styles["footer"],
    )
