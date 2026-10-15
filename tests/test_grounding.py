"""Vérification programmatique : c'est le garde-fou contre l'invention
d'informations. Ces tests décrivent précisément ce qu'il attrape et ce qu'il
laisse passer."""

from __future__ import annotations

import pytest

from backend.app.models import Segment
from backend.app.pipeline import grounding


@pytest.fixture
def segments():
    return [
        Segment(id="s0001", start=0, end=5,
                text="On a eu trois incidents sur le cluster Kubernetes la semaine dernière."),
        Segment(id="s0002", start=5, end=12,
                text="Le budget validé est de 1 200 euros, Marie s'en occupe pour le 15 octobre."),
        Segment(id="s0003", start=12, end=18,
                text="Le taux d'erreur est monté à 12 % sur l'API Grafana."),
    ]


@pytest.fixture
def index(segments):
    return {s.id: s for s in segments}


@pytest.fixture
def ensemble(segments):
    return grounding.transcript_haystack(segments)


def verifier(enonce, ids, index, ensemble, **kwargs):
    return grounding.verify_statement(
        enonce, ids, index, full_transcript_haystack=ensemble, **kwargs
    )


# --- ce qui doit passer ----------------------------------------------------


@pytest.mark.parametrize(
    "enonce, ids, raison",
    [
        ("Il y a eu 3 incidents sur le cluster Kubernetes.", ["s0001"],
         "chiffre écrit en lettres dans la source, en chiffres dans le compte rendu"),
        ("Le budget de 1200 euros est validé.", ["s0002"],
         "séparateur de milliers différent"),
        ("Le budget de 1 200 euros concerne Marie.", ["s0002"],
         "nom propre présent dans le passage cité"),
        ("Le taux d'erreur atteint 12 % sur Grafana.", ["s0003"],
         "pourcentage et nom de produit présents"),
        ("Échéance fixée au 15 octobre.", ["s0002"], "date présente"),
    ],
)
def test_informations_fidèles_sont_validées(enonce, ids, raison, index, ensemble):
    assert verifier(enonce, ids, index, ensemble).status == "verifie", raison


# --- ce qui doit être signalé ----------------------------------------------


def test_chiffre_inventé_est_signalé(index, ensemble):
    resultat = verifier("Le budget est de 5000 euros.", ["s0002"], index, ensemble)
    assert resultat.status == "partiel"
    assert "5000" in resultat.unmatched


def test_nom_propre_inventé_est_signalé(index, ensemble):
    resultat = verifier("Le dossier a été confié à Jean.", ["s0002"], index, ensemble)
    assert resultat.status == "partiel"
    assert "jean" in resultat.unmatched


def test_fait_réel_mais_mal_rattaché_est_signalé(index, ensemble):
    """Grafana existe dans la réunion, mais pas dans le passage cité."""
    resultat = verifier("Le problème vient de Grafana.", ["s0001"], index, ensemble)
    assert resultat.status == "partiel"
    assert resultat.note and "ailleurs dans la transcription" in resultat.note


def test_segment_inexistant_rend_non_vérifiable(index, ensemble):
    resultat = verifier("Une décision a été prise.", ["s9999"], index, ensemble)
    assert resultat.status == "non verifie"
    assert "s9999" in (resultat.note or "")


def test_aucune_source_citée_rend_non_vérifiable(index, ensemble):
    assert verifier("Tout va bien.", [], index, ensemble).status == "non verifie"


# --- réduction du bruit -----------------------------------------------------


@pytest.mark.parametrize(
    "enonce",
    [
        "Porter le budget à 1 200 euros.",      # verbe à l'infinitif
        "Quelle est la part des 12 % d'erreurs ?",  # interrogatif
        "Saturation constatée à 12 %.",         # nominalisation
    ],
)
def test_premier_mot_ignoré_quand_demandé(enonce, index, ensemble):
    """Le premier mot d'un énoncé n'est jamais un fait à vérifier : sans cette
    règle, chaque action serait signalée à tort."""
    cible = ["s0002"] if "budget" in enonce else ["s0003"]
    assert verifier(enonce, cible, index, ensemble, skip_leading_word=True).status == "verifie"


def test_responsable_non_justifié_est_signalé(index, ensemble):
    """Une action attribuée à quelqu'un que le passage cité ne nomme pas."""
    resultat = verifier(
        "Traiter les erreurs de l'API.", ["s0003"], index, ensemble,
        skip_leading_word=True, extra_text="Marie",
    )
    assert resultat.status == "partiel"
    assert "marie" in resultat.unmatched


def test_responsable_justifié_est_validé(index, ensemble):
    resultat = verifier(
        "Suivre le budget.", ["s0002"], index, ensemble,
        skip_leading_word=True, extra_text="Marie",
    )
    assert resultat.status == "verifie"


# --- briques internes -------------------------------------------------------


@pytest.mark.parametrize(
    "texte, attendu",
    [
        ("trois", {"3"}), ("quinze", {"15"}), ("vingt cinq", {"25"}),
        ("cent vingt", {"120"}), ("mille deux cents", {"1200"}),
        ("deux mille", {"2000"}),
    ],
)
def test_nombres_en_lettres_convertis(texte, attendu):
    assert attendu <= grounding.french_numbers_to_digits(grounding.normalize(texte))


@pytest.mark.parametrize(
    "brut, attendu",
    [("1 200", "1200"), ("1.200", "1200"), ("12,5", "12.5"), ("99,5", "99.5"), ("2.4", "2.4")],
)
def test_normalisation_des_nombres(brut, attendu):
    assert grounding.normalize_number(brut) == attendu


def test_un_nombre_ne_correspond_pas_a_un_prefixe():
    """« 3 » ne doit pas être validé par la présence de « 30 »."""
    ensemble = grounding.build_haystack(["Nous avons 30 serveurs."])
    assert grounding._contains(ensemble, "30")
    assert not grounding._contains(ensemble, "3")


def test_les_accents_ne_gênent_pas_la_comparaison():
    ensemble = grounding.build_haystack(["La réunion a été décalée."])
    assert grounding._contains(ensemble, grounding.normalize("Réunion"))
    assert grounding._contains(ensemble, grounding.normalize("reunion"))
