"""Tests du budget mensuel d'appels API.

Le limiteur de débit protège du « trop vite », ces budgets du « trop au
total ». Twitter plafonne au MOIS : 100 posts en Free, 15 000 en Basic.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.budget import MonthlyBudget, load_budgets  # noqa: E402


class TestMonthlyBudget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "budgets.json")

    def _budget(self, limit=1000):
        return MonthlyBudget(self.path, "twitter", limit)

    def test_depense_dans_la_limite(self):
        budget = self._budget(limit=1000)
        self.assertTrue(budget.spend(1))
        self.assertEqual(budget.stats["used"], 1)
        self.assertEqual(budget.remaining, 999)

    def test_refuse_au_dela_du_plafond_mensuel(self):
        budget = self._budget(limit=1000)
        # On neutralise le lissage quotidien pour éprouver le plafond du MOIS.
        budget._used = 1000
        self.assertFalse(budget.spend(1))
        self.assertEqual(budget.remaining, 0)

    def test_le_lissage_quotidien_borne_la_journee(self):
        # 1000 pour le mois : impossible de tout dépenser aujourd'hui.
        budget = self._budget(limit=1000)
        allowance = budget.stats["daily_allowance"]
        accepted = sum(1 for _ in range(allowance + 5) if budget.spend(1))
        self.assertEqual(accepted, allowance)
        self.assertFalse(budget.spend(1))
        self.assertGreater(budget.remaining, 0)  # le reste du mois est préservé

    def test_part_quotidienne_stable_pendant_la_journee(self):
        # Si elle dépendait de `_used` courant, elle grandirait à chaque
        # dépense — un plafond qui monte quand on le consomme.
        budget = self._budget(limit=3000)
        avant = budget.stats["daily_allowance"]
        budget.spend(5)
        self.assertEqual(budget.stats["daily_allowance"], avant)

    def test_compteur_survit_au_redemarrage(self):
        # Sans persistance, relancer le bot remet le quota à zéro et le
        # plafond mensuel est franchi sans que rien ne l'annonce.
        first = self._budget(limit=1000)
        for _ in range(10):
            first.spend(1)

        restarted = self._budget(limit=1000)
        self.assertEqual(restarted.stats["used"], 10)
        self.assertEqual(restarted.remaining, 990)

    def test_lissage_quotidien_empeche_de_bruler_le_mois(self):
        # 3000 pour le mois ne doit pas partir le jour 1.
        budget = self._budget(limit=3000)
        allowance = budget.stats["daily_allowance"]
        self.assertLess(allowance, 3000)
        self.assertGreater(allowance, 0)

    def test_reserve_de_fin_de_mois(self):
        budget = self._budget(limit=1000)
        # La part quotidienne exclut 10% gardés en réserve.
        self.assertLessEqual(budget.stats["daily_allowance"], 900)

    def test_remise_a_zero_au_changement_de_mois(self):
        budget = self._budget(limit=100)
        budget._used, budget._used_today = 50, 50  # sans passer par le lissage
        self.assertEqual(budget.stats["used"], 50)

        budget._month = "2000-01"  # simule un mois révolu
        self.assertEqual(budget.stats["used"], 0)
        self.assertEqual(budget.remaining, 100)

    def test_can_spend_ne_consomme_rien(self):
        budget = self._budget(limit=100)
        self.assertTrue(budget.can_spend(1))
        self.assertEqual(budget.stats["used"], 0)

    def test_budget_nul_bloque_tout(self):
        budget = MonthlyBudget(self.path, "twitter", 0)
        self.assertFalse(budget.spend(1))

    def test_plusieurs_budgets_dans_un_seul_fichier(self):
        twitter = MonthlyBudget(self.path, "twitter", 1000)
        birdeye = MonthlyBudget(self.path, "birdeye", 500)
        twitter.spend(3)
        birdeye.spend(7)

        with open(self.path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["twitter"]["used"], 3)
        self.assertEqual(data["birdeye"]["used"], 7)

    def test_load_budgets_ignore_les_illimites(self):
        budgets = load_budgets(self.path, {"twitter": 15000, "helius": 0, "gmgn": 0})
        self.assertIn("twitter", budgets)
        self.assertNotIn("helius", budgets)


class TestTwitterSousBudget(unittest.TestCase):
    """Twitter ne doit jamais partir en appel si le mois est épuisé."""

    def setUp(self):
        from src.apis.twitter import TwitterAPI
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "budgets.json")
        self.budget = MonthlyBudget(self.path, "twitter", 1000)
        self.api = TwitterAPI("faux-token", budget=self.budget)

    def test_appel_refuse_quand_le_budget_est_epuise(self):
        self.budget._used = self.budget.monthly_limit
        with patch("requests.Session.get") as fake:
            result = self.api.get_volume("TEST")
            fake.assert_not_called()
        self.assertIsNone(result)
        self.assertEqual(self.api.skipped_no_budget, 1)

    def test_une_seule_requete_par_token_par_defaut(self):
        # `sample_quality` désactivé : `search/recent` renvoie de vrais posts
        # et pèse double sur le plafond mensuel.
        self.assertFalse(self.api.sample_quality)


if __name__ == "__main__":
    unittest.main(verbosity=2)
