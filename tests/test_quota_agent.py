"""Tests de l'agent de quota — src/core/quota_agent.py."""

import json
import os
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.budget import MonthlyBudget  # noqa: E402
from src.core.params import ParamsStore  # noqa: E402
from src.core.quota_agent import MIN_CYCLES_SAMPLE, QuotaAgent  # noqa: E402

PARAM_PATH = "scan.social_max_lookups_per_cycle"


class TestQuotaAgent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        params_path = os.path.join(self.tmp, "params.json")
        with open(params_path, "w", encoding="utf-8") as fh:
            json.dump({"scan": {"social_max_lookups_per_cycle": 3}}, fh)
        self.params = ParamsStore(params_path)
        self.budget_path = os.path.join(self.tmp, "budgets.json")

    def _budget(self, used_pct: float, limit: int = 1000) -> MonthlyBudget:
        budget = MonthlyBudget(self.budget_path, "twitter", limit)
        budget._used = int(limit * used_pct / 100)
        return budget

    def _agent(self, day: int = 15) -> QuotaAgent:
        # Avril compte 30 jours : jour 15 -> ~50% du mois écoulé, une
        # référence simple pour juger "chaud" (>65%) vs "froid" (<35%).
        fixed_date = date(2026, 4, day)
        return QuotaAgent(self.params, today=lambda: fixed_date)

    def test_aucun_ajustement_sous_le_plancher_dechantillon(self):
        agent = self._agent(day=15)
        for _ in range(MIN_CYCLES_SAMPLE - 1):
            agent.record_demand("twitter", wanted=5, served=1)
        budget = self._budget(used_pct=90.0)  # très chaud, jour 15/30 -> 50% attendu
        changes = agent.recalibrate({"twitter": budget})
        self.assertEqual(changes, [])
        self.assertEqual(self.params.get(PARAM_PATH), 3)

    def test_rythme_chaud_resserre(self):
        agent = self._agent(day=15)  # attendu ~50%
        for _ in range(MIN_CYCLES_SAMPLE):
            agent.record_demand("twitter", wanted=5, served=3)
        budget = self._budget(used_pct=90.0)  # 90% consommé pour 50% attendu
        changes = agent.recalibrate({"twitter": budget})
        self.assertEqual(len(changes), 1)
        self.assertIn("resserré", changes[0])
        self.assertEqual(self.params.get(PARAM_PATH), 2)

    def test_rythme_chaud_ne_descend_pas_sous_le_plancher(self):
        # Déjà à la borne basse (1) : rien à resserrer davantage.
        self.params.set(PARAM_PATH, 1, "setup", 0, log=False)
        agent = self._agent(day=15)
        for _ in range(MIN_CYCLES_SAMPLE):
            agent.record_demand("twitter", wanted=5, served=1)
        budget = self._budget(used_pct=95.0)
        changes = agent.recalibrate({"twitter": budget})
        self.assertEqual(changes, [])
        self.assertEqual(self.params.get(PARAM_PATH), 1)

    def test_rythme_froid_et_demande_non_servie_desserre(self):
        agent = self._agent(day=15)  # attendu ~50%
        for _ in range(MIN_CYCLES_SAMPLE):
            agent.record_demand("twitter", wanted=5, served=2)  # demande insatisfaite
        budget = self._budget(used_pct=10.0)  # très en dessous du rythme attendu
        changes = agent.recalibrate({"twitter": budget})
        self.assertEqual(len(changes), 1)
        self.assertIn("desserré", changes[0])
        self.assertEqual(self.params.get(PARAM_PATH), 4)

    def test_rythme_froid_sans_demande_non_servie_ne_change_rien(self):
        agent = self._agent(day=15)
        for _ in range(MIN_CYCLES_SAMPLE):
            agent.record_demand("twitter", wanted=2, served=2)  # toute la demande servie
        budget = self._budget(used_pct=10.0)
        changes = agent.recalibrate({"twitter": budget})
        self.assertEqual(changes, [])
        self.assertEqual(self.params.get(PARAM_PATH), 3)

    def test_rythme_dans_la_marge_ne_change_rien(self):
        agent = self._agent(day=15)  # attendu ~50%
        for _ in range(MIN_CYCLES_SAMPLE):
            agent.record_demand("twitter", wanted=5, served=1)
        budget = self._budget(used_pct=55.0)  # proche du rythme attendu
        changes = agent.recalibrate({"twitter": budget})
        self.assertEqual(changes, [])

    def test_budget_inconnu_est_ignore_sans_erreur(self):
        agent = self._agent(day=15)
        for _ in range(MIN_CYCLES_SAMPLE):
            agent.record_demand("gmgn", wanted=5, served=1)
        budget = self._budget(used_pct=90.0)
        changes = agent.recalibrate({"gmgn": budget})
        self.assertEqual(changes, [])

    def test_budget_sans_plafond_est_ignore(self):
        agent = self._agent(day=15)
        for _ in range(MIN_CYCLES_SAMPLE):
            agent.record_demand("twitter", wanted=5, served=1)
        budget = self._budget(used_pct=0.0, limit=0)
        changes = agent.recalibrate({"twitter": budget})
        self.assertEqual(changes, [])

    def test_ajustement_journalise_dans_lhistorique(self):
        agent = self._agent(day=15)
        for _ in range(MIN_CYCLES_SAMPLE):
            agent.record_demand("twitter", wanted=5, served=3)
        budget = self._budget(used_pct=90.0)
        agent.recalibrate({"twitter": budget})
        history = self.params.data["learning"]["parameter_adjustment_history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["param_name"], PARAM_PATH)
        self.assertEqual(history[0]["trades_sample_size"], MIN_CYCLES_SAMPLE)


if __name__ == "__main__":
    unittest.main()
