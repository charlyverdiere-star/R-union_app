"""Étapes C et D — structuration de la transcription puis rédaction.

Le traitement se fait en trois passes :

  1. **extraction** fenêtre par fenêtre : le modèle ne voit jamais plus que
     quelques milliers de mots à la fois, ce qui evite qu'il résumé au lieu
     d'extraire, et chaque élément produit cité ses segments d'origine ;
  2. **consolidation** : les éléments des différentes fenêtres sont regroupes.
     Le modèle ne peut que regrouper et reformuler ; les champs structures
     (responsable, échéance, priorite) sont repris **par le code** depuis les
     éléments d'origine, pour qu'aucun ne puisse apparaitre à cette étape ;
  3. **rédaction** : un appel par thème, limite aux passages concernes.

Une vérification mécanique (`grounding.py`) s'appliqué ensuite à tout :
les éléments dont aucun passage source n'est identifiable sont écartés, ceux
dont une partie des faits ne se retrouvé pas dans le passage cité sont
conservés mais signalés.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable, Sequence, TypeVar

from ..config import Settings
from ..models import (
    ActionItem,
    Conclusion,
    Decision,
    Disagreement,
    Evidence,
    ExecutiveSummary,
    Grounded,
    KeyPoint,
    Meeting,
    MeetingMeta,
    OpenQuestion,
    Participant,
    Problem,
    QualityReport,
    ReportModel,
    RiskItem,
    Segment,
    Solution,
    TopicSection,
    Transcript,
)
from ..prompts import (
    EXTRACTION_INSTRUCTIONS,
    NARRATIVE_INSTRUCTIONS,
    REDUCE_INSTRUCTIONS,
    extraction_request,
    narrative_request,
    reduce_request,
)
from ..providers.llm import LlmProvider, Usage, build_llm_provider
from ..storage import MeetingPaths
from . import grounding
from .audio import human_duration
from .schemas import (
    MergeGroup,
    ReduceResult,
    TopicNarrative,
    WindowExtraction,
)

ProgressCallback = Callable[[float, str], None]

#: Taille d'une fenêtre d'extraction, en caractères de transcription.
WINDOW_CHARS = 12000
#: Contexte anterieur fourni en lecture seule, en caractères.
CONTEXT_CHARS = 1500
#: Au-delà, l'extrait transmis pour la rédaction d'un thème est tronqué.
NARRATIVE_EXCERPT_CHARS = 24000

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Découpage de la transcription en fenêtres
# ---------------------------------------------------------------------------


def build_windows(segments: Sequence[Segment], window_chars: int = WINDOW_CHARS) -> list[list[Segment]]:
    """Regroupe les segments en fenêtres de taille homogène, sans en perdre."""
    if not segments:
        return []
    windows: list[list[Segment]] = []
    current: list[Segment] = []
    size = 0
    for segment in segments:
        length = len(segment.text) + 40  # marge pour l'horodatage et l'etiquette
        if current and size + length > window_chars:
            windows.append(current)
            current, size = [], 0
        current.append(segment)
        size += length
    if current:
        windows.append(current)
    return windows


def render_segments(segments: Iterable[Segment], *, with_ids: bool = True) -> str:
    lines = []
    for segment in segments:
        identifier = f"[{segment.id}] " if with_ids else ""
        speaker = f"{segment.speaker} : " if segment.speaker else ""
        flag = " (passage incertain)" if segment.low_confidence else ""
        lines.append(f"{identifier}[{segment.timecode}] {speaker}{segment.text}{flag}")
    return "\n".join(lines)


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else "..." + text[-limit:]


# ---------------------------------------------------------------------------
# Inventaire : identifiants stables pour la consolidation
# ---------------------------------------------------------------------------

CATEGORY_PREFIXES = {
    "topics": "t",
    "key_points": "k",
    "problems": "p",
    "solutions": "s",
    "decisions": "d",
    "actions": "a",
    "open_questions": "q",
    "risks": "r",
    "disagreements": "g",
    "participants": "m",
}

CATEGORY_LABELS = {
    "topics": "Thèmes ABORDES",
    "key_points": "INFORMATIONS Clés",
    "problems": "Problèmes",
    "solutions": "SOLUTIONS",
    "decisions": "Décisions",
    "actions": "ACTIONS ET ENGAGEMENTS",
    "open_questions": "QUESTIONS OUVERTES",
    "risks": "RISQUES ET POINTS DE VIGILANCE",
    "disagreements": "Désaccords ET Réserves",
    "participants": "PARTICIPANTS Repérés",
}


class Inventory:
    """Table des éléments extraits, indexés par un identifiant court."""

    def __init__(self) -> None:
        self.items: dict[str, object] = {}
        self.by_category: dict[str, list[str]] = defaultdict(list)
        self._counters: dict[str, int] = defaultdict(int)

    def add(self, category: str, item: object) -> str:
        self._counters[category] += 1
        identifier = f"{CATEGORY_PREFIXES[category]}{self._counters[category]}"
        self.items[identifier] = item
        self.by_category[category].append(identifier)
        return identifier

    def get(self, identifier: str) -> object | None:
        return self.items.get(identifier)

    def resolve(self, category: str, member_ids: Sequence[str]) -> list[object]:
        """Ne conserve que les identifiants réels et appartenant à la catégorie."""
        prefix = CATEGORY_PREFIXES[category]
        valid = [
            i for i in member_ids
            if i in self.items and i in set(self.by_category[category]) and i.startswith(prefix)
        ]
        return [self.items[i] for i in valid]

    def render(self) -> str:
        blocks: list[str] = []
        for category, identifiers in self.by_category.items():
            if not identifiers:
                continue
            lines = [CATEGORY_LABELS[category]]
            for identifier in identifiers:
                lines.append(f"  {identifier} | {_summarize(self.items[identifier])}")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)


def _summarize(item: object) -> str:
    """Résumé d'une ligne pour l'inventaire soumis à la consolidation."""
    for attribute in ("statement", "task", "question", "title", "label"):
        value = getattr(item, attribute, None)
        if value:
            base = str(value)
            break
    else:  # pragma: no cover - toutes les catégories ont l'un de ces champs
        base = str(item)

    extras = []
    for attribute, label in (
        ("owner", "responsable"),
        ("due", "échéance"),
        ("commitment", "engagement"),
        ("explicitness", "nature"),
        ("status", "état"),
        ("kind", "type"),
        ("name", "nom"),
    ):
        value = getattr(item, attribute, None)
        if value:
            extras.append(f"{label}: {value}")
    segment_ids = getattr(item, "segment_ids", []) or []
    if segment_ids:
        extras.append(f"segments: {','.join(segment_ids[:4])}")
    return base + (f"  [{' ; '.join(extras)}]" if extras else "")


# ---------------------------------------------------------------------------
# Fusion des champs structures (sans intervention du modèle)
# ---------------------------------------------------------------------------


def _first_non_null(members: Sequence[object], attribute: str):
    for member in members:
        value = getattr(member, attribute, None)
        if value:
            return value
    return None


def _union(members: Sequence[object], attribute: str) -> list[str]:
    result: list[str] = []
    for member in members:
        for value in getattr(member, attribute, []) or []:
            if value not in result:
                result.append(value)
    return result


def _merged_evidence(members: Sequence[object]) -> Evidence:
    segment_ids = _union(members, "segment_ids")
    quote = _first_non_null(members, "quote")
    return Evidence(segment_ids=segment_ids, quote=quote)


def _all_equal(members: Sequence[object], attribute: str, expected: str) -> bool:
    values = [getattr(m, attribute, None) for m in members]
    return bool(values) and all(v == expected for v in values)


def _latest(members: Sequence[object], order: dict[str, int]) -> object:
    """Élément dont le dernier segment cité arrive le plus tard dans la réunion."""
    def rank(member: object) -> int:
        ids = getattr(member, "segment_ids", []) or []
        return max((order.get(i, -1) for i in ids), default=-1)

    return max(members, key=rank)


# ---------------------------------------------------------------------------
# Passe principale
# ---------------------------------------------------------------------------


def run_analysis(
    meeting: Meeting,
    transcript: Transcript,
    paths: MeetingPaths,
    settings: Settings,
    *,
    progress: ProgressCallback | None = None,
    provider: LlmProvider | None = None,
) -> tuple[ReportModel, Usage]:
    provider = provider or build_llm_provider(settings)
    report = progress or (lambda *_: None)
    usage = Usage()

    if not transcript.segments:
        raise ValueError(
            "La transcription est vide : il n'y a rien à analyser. "
            "Vérifiez l'enregistrement audio."
        )

    # --- 1. Extraction ---------------------------------------------------
    windows = build_windows(transcript.segments)
    extractions: list[WindowExtraction] = []
    for index, window in enumerate(windows):
        cache = paths.window_file(index)
        if cache.exists():
            extractions.append(WindowExtraction.model_validate_json(cache.read_text("utf-8")))
            report(0.05 + 0.45 * (index + 1) / len(windows), f"Fenêtre {index + 1}/{len(windows)} déjà analysée")
            continue

        report(0.05 + 0.45 * index / len(windows), f"Analyse de la fenêtre {index + 1}/{len(windows)}")
        previous = windows[index - 1] if index else []
        result = provider.structured(
            instructions=EXTRACTION_INSTRUCTIONS,
            request=extraction_request(
                meeting_title=meeting.title,
                meeting_date=meeting.meeting_date,
                context=meeting.context,
                context_segments=_tail(render_segments(previous, with_ids=False), CONTEXT_CHARS),
                window_segments=render_segments(window),
                window_index=index,
                window_total=len(windows),
            ),
            schema=WindowExtraction,
        )
        usage.add(result.usage)
        extraction: WindowExtraction = result.parsed  # type: ignore[assignment]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(extraction.model_dump_json(indent=1), encoding="utf-8")
        extractions.append(extraction)

    # --- 2. Consolidation -------------------------------------------------
    report(0.52, "Consolidation des informations")
    inventory = Inventory()
    for extraction in extractions:
        for category in CATEGORY_PREFIXES:
            for item in getattr(extraction, category, []) or []:
                inventory.add(category, item)

    reduce_cache = paths.merged_analysis
    if reduce_cache.exists():
        reduced = ReduceResult.model_validate_json(reduce_cache.read_text("utf-8"))
    else:
        result = provider.structured(
            instructions=REDUCE_INSTRUCTIONS,
            request=reduce_request(
                meeting_title=meeting.title,
                meeting_date=meeting.meeting_date,
                context=meeting.context,
                inventory=inventory.render(),
            ),
            schema=ReduceResult,
        )
        usage.add(result.usage)
        reduced = result.parsed  # type: ignore[assignment]
        reduce_cache.parent.mkdir(parents=True, exist_ok=True)
        reduce_cache.write_text(reduced.model_dump_json(indent=1), encoding="utf-8")

    # --- 3. Assemblage ----------------------------------------------------
    report(0.6, "Structuration du compte rendu")
    order = {segment.id: index for index, segment in enumerate(transcript.segments)}
    model = _assemble(meeting, transcript, reduced, inventory, order, extractions)

    # --- 4. Rédaction par thème ------------------------------------------
    segments_by_id = transcript.by_id()
    for index, topic in enumerate(model.topics):
        report(
            0.62 + 0.3 * index / max(1, len(model.topics)),
            f"Rédaction : {topic.title}",
        )
        excerpt = render_segments(
            [segments_by_id[i] for i in topic.segment_ids if i in segments_by_id]
        )
        result = provider.structured(
            instructions=NARRATIVE_INSTRUCTIONS,
            request=narrative_request(
                topic_title=topic.title,
                topic_summary=topic.narrative,
                elements=_topic_elements(topic),
                excerpt=excerpt[:NARRATIVE_EXCERPT_CHARS],
            ),
            schema=TopicNarrative,
            cached_context=(
                "Transcription complète de la réunion, pour situer le thème "
                "dans son contexte :\n" + transcript.plain_text()
            ),
        )
        usage.add(result.usage)
        narrative: TopicNarrative = result.parsed  # type: ignore[assignment]
        topic.narrative = narrative.narrative.strip()

    # --- 5. Vérification --------------------------------------------------
    report(0.95, "Vérification des informations")
    verify_report(model, transcript)
    model.llm_model = provider.describe()
    return model, usage


def _topic_elements(topic: TopicSection) -> str:
    lines: list[str] = []
    for label, items, attribute in (
        ("Informations clés", topic.key_points, "statement"),
        ("Problèmes", topic.problems, "statement"),
        ("Solutions", topic.solutions, "statement"),
        ("Désaccords", topic.disagreements, "statement"),
    ):
        if items:
            lines.append(label + " :")
            lines.extend(f"  - {getattr(i, attribute)}" for i in items)
    return "\n".join(lines) or "(aucun élément structure pour ce thème)"


# ---------------------------------------------------------------------------
# Construction du ReportModel
# ---------------------------------------------------------------------------


def _assemble(
    meeting: Meeting,
    transcript: Transcript,
    reduced: ReduceResult,
    inventory: Inventory,
    order: dict[str, int],
    extractions: Sequence[WindowExtraction],
) -> ReportModel:
    decisions = _build_decisions(reduced.decisions, inventory)
    actions = _build_actions(reduced.actions, inventory)
    questions = _build_questions(reduced.open_questions, inventory)
    risks = _build_risks(reduced.risks, inventory)
    key_points = _build_key_points(reduced.key_points, inventory)
    problems = _build_problems(reduced.problems, inventory)
    solutions = _build_solutions(reduced.solutions, inventory, order)
    disagreements = _build_disagreements(reduced.disagreements, inventory)
    participants = _build_participants(reduced.participants, inventory)

    topics = _build_topics(reduced, inventory, order)
    _assign_to_topics(topics, key_points, "key_points", order)
    _assign_to_topics(topics, problems, "problems", order)
    _assign_to_topics(topics, solutions, "solutions", order)
    _assign_to_topics(topics, disagreements, "disagreements", order)

    missing = list(reduced.missing_information)
    for note in _programmatic_gaps(transcript, participants, actions, extractions):
        if note not in missing:
            missing.append(note)

    return ReportModel(
        meta=MeetingMeta(
            title=meeting.title,
            date=meeting.meeting_date,
            duration_label=human_duration(transcript.duration),
            duration_seconds=transcript.duration,
            context=meeting.context,
            subject=reduced.subject,
            participants=participants,
            missing_information=missing,
        ),
        executive_summary=ExecutiveSummary(
            objectives=reduced.executive_summary.objectives,
            main_topics=reduced.executive_summary.main_topics,
            key_outcomes=reduced.executive_summary.key_outcomes,
        ),
        topics=topics,
        decisions=decisions,
        actions=actions,
        open_questions=questions,
        risks=risks,
        conclusion=Conclusion(
            assessment=reduced.conclusion.assessment,
            next_steps=reduced.conclusion.next_steps,
            attention_points=reduced.conclusion.attention_points,
        ),
    )


def _groups_with_orphans(
    groups: Sequence[MergeGroup], inventory: Inventory, category: str
) -> list[tuple[str, list[object]]]:
    """Retourne (énoncé retenu, membres) en rattachant les éléments oubliés.

    Un élément extrait que la consolidation n'a placé dans aucun groupe serait
    purement et simplement perdu : on le reintegre comme groupe à lui seul.
    """
    result: list[tuple[str, list[object]]] = []
    consumed: set[str] = set()
    valid_ids = set(inventory.by_category[category])

    for group in groups:
        members_ids = [i for i in group.member_ids if i in valid_ids and i not in consumed]
        if not members_ids:
            continue
        consumed.update(members_ids)
        members = [inventory.items[i] for i in members_ids]
        result.append((group.canonical.strip() or _summarize(members[0]), members))

    for identifier in inventory.by_category[category]:
        if identifier in consumed:
            continue
        item = inventory.items[identifier]
        statement = _primary_text(item)
        if statement:
            result.append((statement, [item]))
    return result


def _primary_text(item: object) -> str:
    for attribute in ("statement", "task", "question", "title", "label"):
        value = getattr(item, attribute, None)
        if value:
            return str(value)
    return ""


def _build_decisions(groups: Sequence[MergeGroup], inventory: Inventory) -> list[Decision]:
    built: list[Decision] = []
    for index, (statement, members) in enumerate(_groups_with_orphans(groups, inventory, "decisions"), 1):
        built.append(
            Decision(
                id=f"D{index}",
                statement=statement,
                rationale=_first_non_null(members, "rationale"),
                stakeholders=_union(members, "stakeholders"),
                conditions=_first_non_null(members, "conditions"),
                # « explicite » seulement si toutes les extractions le disent.
                explicitness="explicite" if _all_equal(members, "explicitness", "explicite") else "implicite",
                evidence=_merged_evidence(members),
            )
        )
    return built


def _build_actions(groups: Sequence[MergeGroup], inventory: Inventory) -> list[ActionItem]:
    built: list[ActionItem] = []
    for index, (statement, members) in enumerate(_groups_with_orphans(groups, inventory, "actions"), 1):
        built.append(
            ActionItem(
                id=f"A{index}",
                task=statement,
                owner=_first_non_null(members, "owner"),
                due=_first_non_null(members, "due"),
                priority=_first_non_null(members, "priority"),
                dependencies=_union(members, "dependencies"),
                blockers=_union(members, "blockers"),
                # « ferme » seulement si aucune extraction ne la juge proposee.
                commitment="ferme" if _all_equal(members, "commitment", "ferme") else "propose",
                evidence=_merged_evidence(members),
            )
        )
    return built


def _build_key_points(groups: Sequence[MergeGroup], inventory: Inventory) -> list[KeyPoint]:
    return [
        KeyPoint(
            id=f"K{index}",
            statement=statement,
            kind=_first_non_null(members, "kind") or "fait",
            evidence=_merged_evidence(members),
        )
        for index, (statement, members) in enumerate(
            _groups_with_orphans(groups, inventory, "key_points"), 1
        )
    ]


def _build_problems(groups: Sequence[MergeGroup], inventory: Inventory) -> list[Problem]:
    return [
        Problem(
            id=f"P{index}",
            statement=statement,
            causes=_union(members, "causes"),
            evidence=_merged_evidence(members),
        )
        for index, (statement, members) in enumerate(
            _groups_with_orphans(groups, inventory, "problems"), 1
        )
    ]


def _build_solutions(
    groups: Sequence[MergeGroup], inventory: Inventory, order: dict[str, int]
) -> list[Solution]:
    built: list[Solution] = []
    for index, (statement, members) in enumerate(_groups_with_orphans(groups, inventory, "solutions"), 1):
        # L'état le plus tardif fait foi : une piste proposee puis retenue
        # doit apparaitre comme retenue.
        latest = _latest(members, order)
        built.append(
            Solution(
                id=f"S{index}",
                statement=statement,
                status=getattr(latest, "status", None) or "proposee",
                evidence=_merged_evidence(members),
            )
        )
    return built


def _build_questions(groups: Sequence[MergeGroup], inventory: Inventory) -> list[OpenQuestion]:
    return [
        OpenQuestion(
            id=f"Q{index}",
            question=statement,
            kind=_first_non_null(members, "kind") or "sans reponse",
            evidence=_merged_evidence(members),
        )
        for index, (statement, members) in enumerate(
            _groups_with_orphans(groups, inventory, "open_questions"), 1
        )
    ]


def _build_risks(groups: Sequence[MergeGroup], inventory: Inventory) -> list[RiskItem]:
    return [
        RiskItem(
            id=f"R{index}",
            statement=statement,
            kind=_first_non_null(members, "kind") or "risque",
            evidence=_merged_evidence(members),
        )
        for index, (statement, members) in enumerate(
            _groups_with_orphans(groups, inventory, "risks"), 1
        )
    ]


def _build_disagreements(groups: Sequence[MergeGroup], inventory: Inventory) -> list[Disagreement]:
    return [
        Disagreement(id=f"G{index}", statement=statement, evidence=_merged_evidence(members))
        for index, (statement, members) in enumerate(
            _groups_with_orphans(groups, inventory, "disagreements"), 1
        )
    ]


def _build_participants(groups: Sequence[MergeGroup], inventory: Inventory) -> list[Participant]:
    built: list[Participant] = []
    for index, (statement, members) in enumerate(
        _groups_with_orphans(groups, inventory, "participants"), 1
    ):
        label = _first_non_null(members, "label") or statement
        built.append(
            Participant(
                id=f"M{index}",
                label=str(label),
                name=_first_non_null(members, "name"),
                role=_first_non_null(members, "role"),
                evidence=_merged_evidence(members),
            )
        )
    return built


def _build_topics(
    reduced: ReduceResult, inventory: Inventory, order: dict[str, int]
) -> list[TopicSection]:
    topics: list[TopicSection] = []
    for index, group in enumerate(
        _groups_with_orphans(
            [MergeGroup(canonical=t.title, member_ids=t.member_ids) for t in reduced.topics],
            inventory,
            "topics",
        ),
        1,
    ):
        title, members = group
        matching = next((t for t in reduced.topics if t.title == title), None)
        segment_ids = _union(members, "segment_ids")
        segment_ids.sort(key=lambda sid: order.get(sid, 10**9))
        topics.append(
            TopicSection(
                id=f"T{index}",
                title=title,
                # Le résumé sert de point de depart à la rédaction détaillée.
                narrative=(matching.summary if matching else _first_non_null(members, "summary")) or "",
                related_topics=list(matching.related_titles) if matching else [],
                segment_ids=segment_ids,
            )
        )
    topics.sort(key=lambda t: order.get(t.segment_ids[0], 10**9) if t.segment_ids else 10**9)
    return topics


def _assign_to_topics(
    topics: list[TopicSection], items: Sequence[Grounded], attribute: str, order: dict[str, int]
) -> None:
    """Rattache chaque élément au thème qui couvre le mieux ses segments.

    L'affectation est calculée et non demandée au modèle : elle est donc
    reproductible et ne peut pas melanger les rubriques.
    """
    if not topics:
        return
    coverage = [set(topic.segment_ids) for topic in topics]
    for item in items:
        ids = set(item.evidence.segment_ids)
        if ids:
            scores = [len(ids & covered) for covered in coverage]
            best = max(range(len(topics)), key=lambda i: scores[i])
            if scores[best] == 0:
                # Aucun recouvrement : on retombe sur le thème chronologiquement
                # le plus proche du premier segment cité.
                position = min((order.get(i, 10**9) for i in ids), default=10**9)
                best = min(
                    range(len(topics)),
                    key=lambda i: abs(
                        order.get(topics[i].segment_ids[0], 10**9) - position
                    )
                    if topics[i].segment_ids
                    else 10**9,
                )
        else:
            best = 0
        getattr(topics[best], attribute).append(item)


def _programmatic_gaps(
    transcript: Transcript,
    participants: Sequence[Participant],
    actions: Sequence[ActionItem],
    extractions: Sequence[WindowExtraction],
) -> list[str]:
    """Manques constatés par le code, indépendamment de l'avis du modèle."""
    gaps: list[str] = []
    if not participants or all(p.name is None for p in participants):
        gaps.append(
            "Les participants ne sont pas identifiés nominativement : "
            "aucun nom n'a été prononcé dans l'enregistrement."
        )
    elif not transcript.diarization:
        gaps.append(
            "Les prises de parole ne sont pas attribuées à un locuteur "
            "(fonction non disponible avec le moteur de transcription utilisé)."
        )
    without_owner = [a for a in actions if not a.owner]
    if without_owner:
        gaps.append(
            f"{len(without_owner)} action(s) sur {len(actions)} n'ont pas de "
            "responsable explicitement désigné pendant la réunion."
        )
    without_due = [a for a in actions if not a.due]
    if without_due:
        gaps.append(
            f"{len(without_due)} action(s) sur {len(actions)} n'ont pas "
            "d'échéance énoncée."
        )
    unclear = [u for e in extractions for u in e.unclear_passages]
    if unclear:
        gaps.append(
            f"{len(unclear)} passage(s) n'ont pas pu être interprétés avec "
            "certitude et sont signalés dans la transcription."
        )
    return gaps


# ---------------------------------------------------------------------------
# Vérification et politique de publication
# ---------------------------------------------------------------------------

#: Champ portant l'énoncé à vérifier, par type d'élément.
STATEMENT_FIELDS = {
    "KeyPoint": "statement",
    "Problem": "statement",
    "Solution": "statement",
    "Disagreement": "statement",
    "Décision": "statement",
    "ActionItem": "task",
    "OpenQuestion": "question",
    "RiskItem": "statement",
    "Participant": "label",
}


def _check_spec(item: object) -> tuple[str, bool, str]:
    """Que faut-il vérifier pour cet élément : (énoncé, ignorer le premier mot,
    champs structurés à contrôler en plus).

    Le premier mot est systématiquement ignore : dans un compte rendu il
    s'agit d'un verbe, d'un interrogatif ou d'une nominalisation. Ce sont les
    champs structures — responsable, personnes concernees, échéance — qui
    portent les attributions, et ils sont vérifiés explicitement.
    """
    kind = type(item).__name__

    if kind == "ActionItem":
        extra = " ".join(filter(None, [item.owner or "", item.due or ""]))
        return item.task, True, extra

    if kind == "Décision":
        return item.statement, True, " ".join(item.stakeholders)

    if kind == "Participant":
        # L'etiquette (« Locuteur 1 ») vient du moteur de transcription, pas du
        # modèle : seul le nom réel, s'il a été attribué, est verifiable. Le
        # rôle attribué au locuteur est une interpretation, pas un fait cité :
        # il n'est pas soumis au contrôle textuel.
        return item.name or "", False, ""

    field = STATEMENT_FIELDS.get(kind, "statement")
    return getattr(item, field, ""), True, ""


def verify_report(model: ReportModel, transcript: Transcript) -> QualityReport:
    """Vérifie chaque élément et écarte ceux qui ne sont rattachables à rien."""
    segments_by_id = transcript.by_id()
    haystack = grounding.transcript_haystack(transcript.segments)
    quality = model.quality

    def check(items: list) -> list:
        kept = []
        for item in items:
            statement, skip_leading, extra = _check_spec(item)
            verification = grounding.verify_item(
                item,
                statement,
                segments_by_id,
                haystack,
                skip_leading_word=skip_leading,
                extra_text=extra,
            )
            quality.items_total += 1
            if verification.status == "verifie":
                quality.items_verified += 1
                kept.append(item)
            elif verification.status == "partiel":
                quality.items_partial += 1
                kept.append(item)
            else:
                # Aucun passage identifiable : l'information est retiree des
                # documents plutôt que publiee sans source.
                quality.items_unverified += 1
                quality.dropped_items += 1
        return kept

    model.decisions = check(model.decisions)
    model.actions = check(model.actions)
    model.open_questions = check(model.open_questions)
    model.risks = check(model.risks)
    model.meta.participants = check(model.meta.participants)
    for topic in model.topics:
        topic.key_points = check(topic.key_points)
        topic.problems = check(topic.problems)
        topic.solutions = check(topic.solutions)
        topic.disagreements = check(topic.disagreements)

    quality.low_confidence_ratio = transcript.low_confidence_ratio
    # Les éléments écartés sont déjà annonces par `render.common.quality_lines` :
    # on ne les repete pas ici pour ne pas afficher deux fois la même phrase.
    quality.transcription_notes = list(transcript.warnings)
    return quality


def save_report_model(model: ReportModel, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=1), encoding="utf-8")


def load_report_model(path: Path) -> ReportModel:
    return ReportModel.model_validate_json(path.read_text(encoding="utf-8"))
