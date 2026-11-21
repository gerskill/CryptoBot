"""M2 : entrées perdues par un `break` sur le seuil absolu (audit du 2026-11-17).

Les candidats sont triés par score de CLASSEMENT (60 % absolu + 40 % rang), le seuil porte sur le
score ABSOLU. La boucle d'entrée s'arrêtait au premier candidat sous le seuil absolu : un candidat
éligible plus bas dans le classement n'était jamais tenté.
"""

import inspect
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import main  # noqa: E402


def c(nom, classement, absolu):
    return SimpleNamespace(symbol=nom, alpha_score=classement, alpha_score_absolute=absolu)


class TestCandidatsAuSeuil(unittest.TestCase):
    def test_un_eligible_place_apres_un_non_eligible_est_tente(self):
        lot = [c("A", 90, 80), c("B", 85, 60), c("C", 70, 76)]  # trié par classement
        noms = [x.symbol for x in main.candidats_au_seuil(lot, 75)]
        self.assertEqual(noms, ["A", "C"])

    def test_l_ordre_du_classement_est_garde(self):
        lot = [c("A", 90, 76), c("B", 80, 90), c("C", 70, 80)]
        self.assertEqual([x.symbol for x in main.candidats_au_seuil(lot, 75)], ["A", "B", "C"])

    def test_le_seuil_est_inclusif(self):
        self.assertEqual(len(list(main.candidats_au_seuil([c("A", 1, 75)], 75))), 1)

    def test_aucun_eligible(self):
        self.assertEqual(list(main.candidats_au_seuil([c("A", 99, 10)], 75)), [])

    def test_la_boucle_d_entree_utilise_la_selection_et_n_a_plus_le_break(self):
        source = inspect.getsource(main.AlphaLoop._try_entries)
        self.assertIn("candidats_au_seuil(", source)
        self.assertNotIn("alpha_score_absolute < threshold", source)


if __name__ == "__main__":
    unittest.main()
