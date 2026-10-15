"""Structuration et garde-fous de l'analyse.

Le jeu de test contient volontairement trois pièges, qui simulent les erreurs
qu'un modèle de langage peut commettre :
  - une décision citant un chiffre absent de la transcription (« pool à 500 ») ;
  - une information inventée (« budget ramené à 900 euros ») ;
  - une décision rattachée à un segment inexistant (« contrat résilié »).
Les tests vérifient que les deux premières sont publiées mais signalées, et
que la troisième est écartée.
"""

from __future__ import annotations

import pytest

from backend.app.models import Segment, TopicSection
from backend.app.pipeline import analyze
from backend.app.pipeline.schemas import MergeGroup


# --- découpage en fenêtres ---------------------------------------------------


def test_fenetres_couvrent_tous_les_segments():
    segments = [
        Segment(id=f"s{i:04d}", start=i * 5, end=i * 5 + 5, text="phrase " * 50)
        for i in range(1, 40)
    ]
    fenetres = analyze.build_windows(segments)
    assert len(fenetres) > 1
    assert sum(len(f) for f in fenetres) == len(segments), "aucun segment ne doit être perdu"
    identifiants = [s.id for f in fenetres for s in f]
    assert identifiants == [s.id for s in segments], "l'ordre doit être préservé"


def test_transcription_vide_donne_zero_fenetre():
    assert analyze.build_windows([]) == []


def test_rendu_des_segments_porte_identifiant_et_horodatage():
    rendu = analyze.render_segments(
        [Segment(id="s0007", start=3661, end=3665, text="bonjour", speaker="Locuteur 2")]
    )
    assert "[s0007]" in rendu and "[01:01:01]" in rendu and "Locuteur 2" in rendu


def test_passage_incertain_signale_au_modele():
    rendu = analyze.render_segments(
        [Segment(id="s0001", start=0, end=2, text="inaudible", low_confidence=True)]
    )
    assert "passage incertain" in rendu


# --- consolidation -----------------------------------------------------------


class FauxElement:
    def __init__(self, statement, **champs):
        self.statement = statement
        self.segment_ids = champs.pop("segment_ids", ["s0001"])
        self.quote = champs.pop("quote", "citation")
        for nom, valeur in champs.items():
            setattr(self, nom, valeur)


def inventaire_avec(categorie, elements):
    inv = analyze.Inventory()
    for element in elements:
        inv.add(categorie, element)
    return inv


def test_element_oublie_par_la_fusion_est_recupere():
    """Un élément qu'aucun groupe ne mentionne ne doit pas disparaître."""
    inv = inventaire_avec("key_points", [FauxElement("A"), FauxElement("B"), FauxElement("C")])
    groupes = [MergeGroup(canonical="A regroupé", member_ids=["k1"])]
    resultat = analyze._groups_with_orphans(groupes, inv, "key_points")
    enonces = [enonce for enonce, _ in resultat]
    assert "A regroupé" in enonces
    assert "B" in enonces and "C" in enonces, "les orphelins doivent être réintégrés"


def test_identifiant_invente_par_la_fusion_est_ignore():
    inv = inventaire_avec("key_points", [FauxElement("A")])
    groupes = [MergeGroup(canonical="X", member_ids=["k99"])]
    assert analyze._groups_with_orphans(groupes, inv, "key_points") == [("A", [inv.items["k1"]])]


def test_un_element_nest_rattache_qua_un_seul_groupe():
    inv = inventaire_avec("key_points", [FauxElement("A")])
    groupes = [
        MergeGroup(canonical="premier", member_ids=["k1"]),
        MergeGroup(canonical="second", member_ids=["k1"]),
    ]
    resultat = analyze._groups_with_orphans(groupes, inv, "key_points")
    assert len(resultat) == 1, "pas de duplication entre groupes"


def test_fusion_conservatrice_sur_le_caractere_ferme():
    """Si une extraction juge l'action seulement proposée, le doute l'emporte."""
    inv = inventaire_avec("actions", [
        FauxElement("tâche", task="tâche", owner="Marie", due=None, priority=None,
                    dependencies=[], blockers=[], commitment="ferme"),
        FauxElement("tâche", task="tâche", owner=None, due="lundi", priority=None,
                    dependencies=[], blockers=[], commitment="propose"),
    ])
    actions = analyze._build_actions([MergeGroup(canonical="tâche", member_ids=["a1", "a2"])], inv)
    assert actions[0].commitment == "propose"
    assert actions[0].owner == "Marie", "les champs structurés sont repris, pas réinventés"
    assert actions[0].due == "lundi"


def test_fusion_conservatrice_sur_le_caractere_explicite():
    inv = inventaire_avec("decisions", [
        FauxElement("d", rationale=None, stakeholders=[], conditions=None,
                    explicitness="explicite"),
        FauxElement("d", rationale=None, stakeholders=[], conditions=None,
                    explicitness="implicite"),
    ])
    decisions = analyze._build_decisions([MergeGroup(canonical="d", member_ids=["d1", "d2"])], inv)
    assert decisions[0].explicitness == "implicite"


def test_etat_dune_solution_suit_le_dernier_echange():
    """Une piste proposée puis retenue doit apparaître comme retenue."""
    inv = inventaire_avec("solutions", [
        FauxElement("s", status="proposee", segment_ids=["s0001"]),
        FauxElement("s", status="retenue", segment_ids=["s0020"]),
    ])
    ordre = {f"s{i:04d}": i for i in range(1, 30)}
    solutions = analyze._build_solutions(
        [MergeGroup(canonical="s", member_ids=["s1", "s2"])], inv, ordre
    )
    assert solutions[0].status == "retenue"


def test_rattachement_aux_themes_par_recouvrement():
    """L'affectation est calculée, pas demandée au modèle."""
    from backend.app.models import KeyPoint, Evidence

    themes = [
        TopicSection(id="T1", title="A", segment_ids=["s0001", "s0002"]),
        TopicSection(id="T2", title="B", segment_ids=["s0010", "s0011"]),
    ]
    element = KeyPoint(id="K1", statement="x", evidence=Evidence(segment_ids=["s0011"]))
    analyze._assign_to_topics(themes, [element], "key_points", {f"s{i:04d}": i for i in range(20)})
    assert themes[1].key_points == [element] and themes[0].key_points == []


# --- analyse complète et vérification ----------------------------------------


def test_analyse_complete_produit_un_rapport_structure(reunion_analysee):
    _, _, modele, _ = reunion_analysee
    assert len(modele.topics) == 3
    assert all(t.narrative for t in modele.topics), "chaque thème doit être rédigé"
    assert modele.executive_summary.objectives
    assert modele.conclusion.assessment


def test_information_sans_source_est_ecartee(reunion_analysee):
    _, _, modele, _ = reunion_analysee
    enonces = [d.statement for d in modele.decisions]
    assert not any("résilié" in e for e in enonces), (
        "une décision citant un segment inexistant doit être retirée des documents"
    )
    assert modele.quality.dropped_items >= 1


def test_chiffre_non_justifie_est_publie_mais_signale(reunion_analysee):
    _, _, modele, _ = reunion_analysee
    suspecte = next(d for d in modele.decisions if "500" in d.statement)
    assert suspecte.verification.status == "partiel"
    assert "500" in suspecte.verification.unmatched


def test_information_inventee_est_signalee(reunion_analysee):
    _, _, modele, _ = reunion_analysee
    inventee = next(k for k in modele.all_key_points() if "900" in k.statement)
    assert inventee.verification.status == "partiel"


def test_toute_information_publiee_cite_un_passage_reel(reunion_analysee):
    _, transcription, modele, _ = reunion_analysee
    connus = set(transcription.by_id())
    elements = [
        *modele.decisions, *modele.actions, *modele.open_questions, *modele.risks,
        *modele.all_key_points(), *modele.meta.participants,
    ]
    for element in elements:
        assert element.evidence.segment_ids, f"{element.id} ne cite aucun passage"
        assert set(element.evidence.segment_ids) <= connus, (
            f"{element.id} cite un passage inexistant"
        )


def test_responsable_non_nomme_reste_vide(reunion_analysee):
    _, _, modele, _ = reunion_analysee
    sans_responsable = [a for a in modele.actions if a.owner is None]
    assert sans_responsable, "aucun responsable ne doit être inventé"
    assert any("juridique" in a.task.lower() for a in sans_responsable)


def test_manques_signales_dans_le_rapport(reunion_analysee):
    _, _, modele, _ = reunion_analysee
    manques = " ".join(modele.meta.missing_information).lower()
    assert "responsable" in manques
    assert "échéance" in manques or "echeance" in manques


def test_compteurs_de_qualite_coherents(reunion_analysee):
    _, _, modele, _ = reunion_analysee
    q = modele.quality
    assert q.items_total == q.items_verified + q.items_partial + q.items_unverified
    assert q.items_total > 30
    assert q.items_verified / q.items_total > 0.7, "trop d'alertes : le contrôle serait inutilisable"


def test_analyse_refuse_une_transcription_vide(settings, tmp_path):
    from backend.app import storage
    from backend.app.models import Meeting, Transcript

    chemins = storage.meeting_paths("vide")
    chemins.ensure()
    with pytest.raises(ValueError, match="vide"):
        analyze.run_analysis(
            Meeting(id="vide", title="T", meeting_date="2026-10-15"),
            Transcript(language="fr", provider="x", model="x", duration=0, segments=[]),
            chemins, settings,
        )


def test_reprise_de_lanalyse_reutilise_le_cache(reunion_analysee, settings):
    """Relancer l'analyse ne doit pas refaire les appels déjà payés."""
    reunion, transcription, _, chemins = reunion_analysee
    assert chemins.window_file(0).exists()
    assert chemins.merged_analysis.exists()
    # Un second passage réussit alors que le fournisseur de rejeu ne dispose
    # que d'un exemplaire de chaque réponse : la preuve que le cache a servi.
    modele, _ = analyze.run_analysis(reunion, transcription, chemins, settings)
    assert len(modele.topics) == 3
