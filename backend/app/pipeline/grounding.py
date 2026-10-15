"""Vérification programmatique des informations produites par l'analyse.

Ce module ne fait appel à aucun modèle de langage : c'est un contrôle
mécanique, et c'est précisément ce qui lui donne sa valeur. Pour chaque
information restituee, il vérifié que :

  1. les segments de transcription cités existent reellement ;
  2. les éléments factuels de l'énoncé — chiffres, pourcentages, acronymes,
     noms propres — se retrouvent bien dans ces segments.

Le résultat alimente `Vérification`, qui determine ensuite si l'information
est publiee telle quelle, signalée comme « à vérifier », ou écartée.
"""

from __future__ import annotations

import re
import unicodedata

from ..models import Grounded, Segment, Verification

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_PUNCTUATION = re.compile(r"[^\w\s%€$°]", flags=re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Minuscules, sans accent, ponctuation réduite, espaces normalisés."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    ascii_text = ascii_text.replace("'", " ").replace("’", " ")
    ascii_text = _PUNCTUATION.sub(" ", ascii_text)
    return _SPACES.sub(" ", ascii_text).strip()


def normalize_number(raw: str) -> str:
    """« 1 200,50 » et « 1.200,50 » deviennent « 1200.5 »."""
    cleaned = raw.replace(" ", "").replace(" ", "").replace(" ", "")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    elif cleaned.count(".") == 1:
        left, right = cleaned.split(".")
        if len(right) == 3 and len(left) <= 3:
            cleaned = left + right  # separateur de milliers
    try:
        value = float(cleaned)
    except ValueError:
        return cleaned
    return str(int(value)) if value == int(value) else str(value)


# ---------------------------------------------------------------------------
# Nombres ecrits en toutes lettres
# ---------------------------------------------------------------------------

_UNITS = {
    "zéro": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
    "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12,
    "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16, "vingt": 20,
    "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60,
    "cent": 100, "cents": 100, "mille": 1000,
}
_SKIP = {"et"}


def french_numbers_to_digits(normalized_text: str) -> set[str]:
    """Extrait les nombres écrits en lettres et les rend sous forme de chiffres.

    Indispensable : un participant dit « trois serveurs », le compte rendu
    ecrit « 3 serveurs ». Sans cette conversion, le contrôle signalerait à tort
    une information non sourcee.
    """
    found: set[str] = set()
    words = normalized_text.split()
    index = 0
    while index < len(words):
        if words[index] not in _UNITS:
            index += 1
            continue
        total = 0
        current = 0
        consumed = 0
        while index + consumed < len(words):
            word = words[index + consumed]
            if word in _SKIP and consumed > 0:
                consumed += 1
                continue
            if word not in _UNITS:
                break
            value = _UNITS[word]
            if value == 1000:
                total += (current or 1) * 1000
                current = 0
            elif value == 100:
                current = (current or 1) * 100
            else:
                current += value
            consumed += 1
        if consumed:
            result = total + current
            if result:
                found.add(str(result))
            index += consumed
        else:
            index += 1
    return found


# ---------------------------------------------------------------------------
# Extraction des éléments verifiables
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"\d[\d   .,]*\d|\d")
_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,}\b")
_PROPER = re.compile(r"\b[A-ZÀ-Þ][a-zà-ÿ]{2,}(?:[-'][A-ZÀ-Þ]?[a-zà-ÿ]+)*\b")

#: Mots à majuscule qui ne sont pas des noms propres verifiables.
_STOPWORDS = {
    normalize(w)
    for w in (
        "Le", "La", "Les", "Un", "Une", "Des", "Ce", "Cette", "Ces", "Il", "Elle",
        "Ils", "Elles", "On", "Nous", "Vous", "Je", "Tu", "Pour", "Par", "Avec",
        "Sans", "Dans", "Sur", "Sous", "Entre", "Après", "Avant", "Pendant",
        "Lors", "Selon", "Depuis", "Jusqu", "Aucun", "Aucune", "Tous", "Toutes",
        "Tout", "Toute", "Plusieurs", "Certains", "Chaque", "Autre", "Autres",
        "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche",
        "Janvier", "Fevrier", "Mars", "Avril", "Mai", "Juin", "Juillet", "Août",
        "Septembre", "Octobre", "Novembre", "Decembre", "Réunion", "Décision",
        "Action", "Point", "Projet", "Equipe", "Objectif", "Probleme", "Solution",
        "Risque", "Question", "Résumé", "Contexte", "Participant", "Participants",
        "Responsable", "Échéance", "Priorite", "Synthèse", "Rapport", "Compte",
        "Si", "Mais", "Donc", "Car", "Que", "Qui", "Quoi", "Dont", "Ou", "Et",
        "Oui", "Non", "Puis", "Ensuite", "Enfin", "Aussi", "Alors", "Ainsi",
        "Suite", "Afin", "Malgre", "Faute", "Cependant", "Toutefois", "Neanmoins",
        "Plusieurs", "Beaucoup", "Peu", "Trop", "Bien", "Mieux", "Moins", "Plus",
        "Aucunement", "Notamment", "Concernant", "Suivant", "Près", "Loin",
        "Mise", "Prise", "Création", "Suppression", "Ajout", "Modification",
        "Validation", "Analyse", "Etude", "Mesure", "Suivi", "Reprise", "Arrêt",
        "Deploiement", "Migration", "Livraison", "Developpement", "Correction",
        "Documentation", "Formation", "Planning", "Budget", "Delai", "Coût",
        "Information", "Discussion", "Echange", "Presentation", "Préparation",
    )
}


def checkable_tokens(statement: str, *, skip_leading_word: bool = False) -> list[str]:
    """Éléments de l'énoncé dont la présence dans la source doit être vérifiée.

    `skip_leading_word` ignore le premier mot lorsqu'il porte une majuscule.
    Dans un compte rendu, ce premier mot est presque toujours un verbe
    (« Porter le pool à 300 »), un interrogatif (« Quelle durée... ») ou une
    nominalisation (« Saturation des connexions... »), jamais un fait à
    vérifier. Le signaler noierait les vraies alertes sous le bruit.

    Contrepartie assumée : un nom propre place en tout premier mot d'un
    énoncé libre échappe au contrôle. Les attributions qui comptent —
    responsable d'une action, personnes concernees par une décision — sont
    couvertes autrement, par `extra_text` dans `verify_statement`.
    """
    tokens: list[str] = []
    leading = _leading_word_span(statement) if skip_leading_word else None

    def outside_leading(match: re.Match) -> bool:
        return leading is None or match.start() >= leading

    for match in _NUMBER.finditer(statement):
        tokens.append(normalize_number(match.group()))

    # Un sigle n'est jamais un verbe : il reste vérifié même en tete de phrase.
    for match in _ACRONYM.finditer(statement):
        tokens.append(normalize(match.group()))

    # Les noms propres sont vérifiés ou qu'ils se trouvent dans la phrase :
    # un compte rendu commence souvent par le nom de la personne concernee
    # (« Marie prend en charge... »), et c'est précisément ce qu'il faut
    # contrôler. Les mots-outils à majuscule sont écartés par la liste
    # `_STOPWORDS`.
    for match in _PROPER.finditer(statement):
        if not outside_leading(match):
            continue
        token = normalize(match.group())
        if token and token not in _STOPWORDS:
            tokens.append(token)

    seen: set[str] = set()
    unique = []
    for token in tokens:
        if token and token not in seen:
            seen.add(token)
            unique.append(token)
    return unique


def _leading_word_span(statement: str) -> int | None:
    """Position de fin du premier mot, s'il commence par une majuscule."""
    match = re.match(r"\s*([A-ZÀ-Þ][\wÀ-ÿ'-]*)", statement)
    return match.end(1) if match else None


def build_haystack(texts: list[str]) -> str:
    """Texte de référence, enrichi des nombres convertis depuis les lettres."""
    normalized = normalize(" ".join(texts))
    numbers = french_numbers_to_digits(normalized)
    digits_in_text = {normalize_number(m.group()) for m in _NUMBER.finditer(" ".join(texts))}
    return " ".join([normalized, *sorted(numbers | digits_in_text)])


def _contains(haystack: str, token: str) -> bool:
    if not token:
        return True
    if token.replace(".", "").isdigit():
        # Un nombre doit correspondre à un mot entier (« 3 » ne vaut pas « 30 »).
        return re.search(rf"(?<![\w.]){re.escape(token)}(?![\w.])", haystack) is not None
    return token in haystack


# ---------------------------------------------------------------------------
# Vérification
# ---------------------------------------------------------------------------


def verify_statement(
    statement: str,
    segment_ids: list[str],
    segments_by_id: dict[str, Segment],
    *,
    full_transcript_haystack: str | None = None,
    skip_leading_word: bool = False,
    extra_text: str = "",
) -> Verification:
    """Contrôle un énoncé contre les segments qu'il cite.

    `extra_text` permet de soumettre au même contrôle les champs structurés
    associés — responsable, personnes concernees, échéance — afin qu'une
    attribution non justifiée par le passage cité soit signalée.
    """
    valid_ids = [sid for sid in segment_ids if sid in segments_by_id]
    if not valid_ids:
        invalid = [sid for sid in segment_ids if sid not in segments_by_id]
        note = (
            f"Aucun passage valide n'est cité (références inconnues : {', '.join(invalid)})."
            if invalid
            else "Aucun passage de la transcription n'est cité."
        )
        return Verification(status="non verifie", note=note)

    haystack = build_haystack([segments_by_id[sid].text for sid in valid_ids])
    tokens = checkable_tokens(statement, skip_leading_word=skip_leading_word)
    if extra_text:
        for token in checkable_tokens(extra_text):
            if token not in tokens:
                tokens.append(token)
    missing = [t for t in tokens if not _contains(haystack, t)]

    if not missing:
        return Verification(status="verifie")

    note = None
    if full_transcript_haystack is not None:
        elsewhere = [t for t in missing if _contains(full_transcript_haystack, t)]
        if elsewhere:
            note = (
                "Présents ailleurs dans la transcription mais pas dans le passage "
                f"cité : {', '.join(elsewhere)}."
            )
    return Verification(status="partiel", unmatched=missing, note=note)


def verify_item(
    item: Grounded,
    statement: str,
    segments_by_id: dict[str, Segment],
    full_transcript_haystack: str | None = None,
    *,
    skip_leading_word: bool = False,
    extra_text: str = "",
) -> Verification:
    verification = verify_statement(
        statement,
        item.evidence.segment_ids,
        segments_by_id,
        full_transcript_haystack=full_transcript_haystack,
        skip_leading_word=skip_leading_word,
        extra_text=extra_text,
    )
    item.evidence.segment_ids = [
        sid for sid in item.evidence.segment_ids if sid in segments_by_id
    ]
    item.verification = verification
    return verification


def transcript_haystack(segments: list[Segment]) -> str:
    return build_haystack([s.text for s in segments])
