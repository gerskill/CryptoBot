"""Confluence entre bras et arbitrage du quota social.

Deux pièges verrouillés ici :
  - le bras consensus ne doit PAS compter son propre vote, sinon il se valide
    tout seul dès qu'il accepte un token ;
  - le plafond Twitter est GLOBAL : N bras ne doivent pas le multiplier par N.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.confluence import (  # noqa: E402
    build_confluence,
    merge_social_wishlists,
    top_confluence,
)
from src.core.models import Candidate, ScanResult  # noqa: E402
from src.pipeline import ArmEvaluation  # noqa: E402


def candidate(symbol, score):
    return Candidate(
        token_address=f"addr_{symbol}", symbol=symbol, name=symbol, chain="solana",
        price_usd=1.0, alpha_score_absolute=score, alpha_score=score,
    )


def evaluation(*candidates):
    return ArmEvaluation(result=ScanResult(candidates=tuple(candidates)))


class TestConfluence(unittest.TestCase):
    def test_compte_les_bras_qui_acceptent(self):
        rows = build_confluence(
            {"a": evaluation(candidate("X", 80)), "b": evaluation(candidate("X", 90))},
            voters={"a", "b"},
            thresholds={"a": 75, "b": 75},
        )
        self.assertEqual(rows["addr_X"].accepted_count, 2)
        self.assertEqual(rows["addr_X"].above_threshold_count, 2)

    def test_ignore_le_bras_consensus_dans_le_quorum(self):
        rows = build_confluence(
            {
                "a": evaluation(candidate("X", 80)),
                "consensus": evaluation(candidate("X", 80)),
            },
            voters={"a"},
            thresholds={"a": 75, "consensus": 75},
        )
        self.assertEqual(rows["addr_X"].above_threshold_by, ("a",))

    def test_distingue_passer_les_filtres_et_franchir_le_seuil(self):
        # Un bras peut retenir un token sans vouloir l'acheter : ce n'est pas
        # un vote d'achat, et le quorum ne doit pas le compter comme tel.
        rows = build_confluence(
            {"a": evaluation(candidate("X", 60)), "b": evaluation(candidate("X", 90))},
            voters={"a", "b"},
            thresholds={"a": 75, "b": 75},
        )
        self.assertEqual(rows["addr_X"].accepted_count, 2)
        self.assertEqual(rows["addr_X"].above_threshold_by, ("b",))

    def test_chaque_bras_garde_son_propre_seuil(self):
        rows = build_confluence(
            {"strict": evaluation(candidate("X", 72)), "large": evaluation(candidate("X", 72))},
            voters={"strict", "large"},
            thresholds={"strict": 80, "large": 70},
        )
        self.assertEqual(rows["addr_X"].above_threshold_by, ("large",))

    def test_les_scores_par_bras_sont_conserves(self):
        rows = build_confluence(
            {"a": evaluation(candidate("X", 80)), "b": evaluation(candidate("X", 91))},
            voters={"a", "b"},
        )
        self.assertEqual(rows["addr_X"].scores_by_arm, {"a": 80, "b": 91})

    def test_top_confluence_classe_par_accord(self):
        rows = build_confluence(
            {
                "a": evaluation(candidate("SEUL", 90), candidate("PARTAGE", 90)),
                "b": evaluation(candidate("PARTAGE", 90)),
            },
            voters={"a", "b"},
            thresholds={"a": 75, "b": 75},
        )
        self.assertEqual(top_confluence(rows)[0]["symbol"], "PARTAGE")

    def test_serialisable_pour_le_dashboard(self):
        rows = build_confluence({"a": evaluation(candidate("X", 90))}, voters={"a"})
        payload = top_confluence(rows)[0]
        self.assertEqual(payload["accepted_by"], ["a"])
        self.assertIn("above_threshold_count", payload)


class TestArbitrageSocial(unittest.TestCase):
    def test_priorise_les_tokens_demandes_par_plusieurs_bras(self):
        # Une requête à quota MENSUEL vaut mieux sur un token que deux
        # stratégies veulent indépendamment que sur le premier choix d'une.
        merged = merge_social_wishlists(
            {"a": ("solo_a", "commun"), "b": ("solo_b", "commun")}, limit=1
        )
        self.assertEqual(merged, ("commun",))

    def test_respecte_le_plafond_global(self):
        merged = merge_social_wishlists(
            {"a": ("t1", "t2", "t3"), "b": ("t4", "t5"), "c": ("t6",)}, limit=2
        )
        self.assertEqual(len(merged), 2)

    def test_n_bras_ne_multiplient_pas_le_plafond(self):
        wishlists = {f"arm{i}": tuple(f"t{j}" for j in range(10)) for i in range(5)}
        self.assertEqual(len(merge_social_wishlists(wishlists, limit=2)), 2)

    def test_departage_par_meilleur_rang(self):
        # À égalité de demandes, le mieux classé chez un bras passe devant.
        merged = merge_social_wishlists({"a": ("premier", "second")}, limit=1)
        self.assertEqual(merged, ("premier",))

    def test_plafond_nul_ne_demande_rien(self):
        self.assertEqual(merge_social_wishlists({"a": ("t1",)}, limit=0), ())

    def test_aucune_demande_ne_consomme_rien(self):
        self.assertEqual(merge_social_wishlists({"a": (), "b": ()}, limit=5), ())


if __name__ == "__main__":
    unittest.main(verbosity=2)
