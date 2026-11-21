"""B2 : verdict contre le témoin peu fiable (audit du 2026-11-17).

Simulation de l'audit, deux bras IDENTIQUES : 10 % de faux verdicts à 3 positions, 5 % à 10, 1 % à
40. Avec 18 bras comparés au même témoin, au moins un faux « supérieur » devient probable. Verrouillé
ici : aucun verdict sous 30 positions de chaque côté, et le nombre de comparaisons est rendu avec.
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.stats import MIN_POSITIONS_VERDICT, verdict_vs_reference


def positions(valeurs):
    return [{"position_id": str(i), "pnl_usd": v, "pnl_pct": v, "is_final_exit": True}
            for i, v in enumerate(valeurs)]


class TestSeuil(unittest.TestCase):
    def test_le_seuil_est_trente(self):
        self.assertEqual(MIN_POSITIONS_VERDICT, 30)

    def test_un_ecart_franc_sous_le_seuil_ne_conclut_pas(self):
        v = verdict_vs_reference(positions([10.0] * 29), positions([-10.0] * 100))
        self.assertEqual(v["verdict"], "échantillon trop court")
        self.assertIsNotNone(v["interval"])  # les intervalles restent affichés

    def test_le_temoin_aussi_doit_avoir_assez_de_positions(self):
        v = verdict_vs_reference(positions([10.0] * 100), positions([-10.0] * 29))
        self.assertEqual(v["verdict"], "échantillon trop court")

    def test_au_dessus_du_seuil_un_ecart_franc_conclut(self):
        v = verdict_vs_reference(positions([10.0] * 30), positions([-10.0] * 30))
        self.assertEqual(v["verdict"], "supérieur")

    def test_le_seuil_rendu_avec_le_verdict(self):
        v = verdict_vs_reference(positions([1.0] * 5), positions([1.0] * 5))
        self.assertEqual(v["min_positions"], 30)


class TestComparaisons(unittest.TestCase):
    def test_le_nombre_de_bras_compares_est_rendu(self):
        v = verdict_vs_reference(positions([1.0] * 40), positions([1.0] * 40), comparisons=18)
        self.assertEqual(v["comparisons"], 18)

    def test_absent_si_non_fourni(self):
        self.assertNotIn("comparisons", verdict_vs_reference(positions([1.0] * 40), positions([1.0] * 40)))


class TestFauxVerdicts(unittest.TestCase):
    def test_deux_bras_identiques_donnent_peu_de_faux_verdicts_au_seuil(self):
        """Même loi des deux côtés (P&L asymétrique de memecoin) : à 30 positions, les faux
        « supérieur/inférieur » doivent rester rares. Graine fixe : test reproductible."""
        rng = random.Random(7)

        def tirage(n):
            return [rng.choice([-8.0, -6.0, -5.0, -4.0, 2.0, 3.0, 25.0]) for _ in range(n)]

        faux = sum(
            verdict_vs_reference(positions(tirage(30)), positions(tirage(30)))["verdict"]
            in ("supérieur", "inférieur")
            for _ in range(150)
        )
        self.assertLessEqual(faux / 150, 0.04)


if __name__ == "__main__":
    unittest.main()
