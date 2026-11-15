"""Apprentissage lent (2026-10-11) : cadence de 50 trades et petits pas."""

import json
import os
import tempfile
import unittest

from src.core.journal import TradeJournal
from src.core.learning import (
    BACKTEST_WINDOW,
    LEARNING_CADENCE_TRADES,
    MIN_EFFECTIVE_STOP_LOSS_PCT,
    LearningEngine,
)
from src.core.params import ParamsStore

PARAMS = {
    "filters": {
        "min_age_hours": 0.5, "max_age_hours": 20.5,
        "min_liquidity_usd": 40000, "min_holders": 100,
    },
    "exit_rules": {
        "stop_loss_pct": -25.0, "take_profit_1": 75.0, "take_profit_2": 300.0,
        "trailing_stop_activation": 200.0, "max_hold_time_minutes": 330,
        "stop_loss_slippage_buffer_pct": 0.0,
    },
    "scoring_weights": {
        "social_sentiment": 0.20, "liquidity": 0.40, "volume": 0.40,
    },
    "risk_per_trade": 0.03,
    "learning": {},
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        path = os.path.join(self.tmp, "params.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(PARAMS, fh)
        self.params = ParamsStore(path)
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))
        self.engine = LearningEngine(self.params, self.journal)
        self.n = 0

    def _trades(self, count, pnl_pct=-25, age=0.5, liquidity=50000):
        for _ in range(count):
            self.n += 1
            self.journal._append({
                "id": f"s{self.n}", "is_final_exit": True, "pnl_pct": pnl_pct,
                "pnl_usd": pnl_pct, "age_hours_at_entry": age,
                "liquidity_at_entry": liquidity, "social_score": None,
                "exit_reason": "STOP_LOSS" if pnl_pct < 0 else "TAKE_PROFIT_1",
            })


class TestCadence(Base):
    def test_cadence_par_defaut_50(self):
        self.assertEqual(LEARNING_CADENCE_TRADES, 50)
        self.assertEqual(self.engine.cadence(), 50)

    def test_fenetre_de_backtest_au_moins_une_cadence(self):
        self.assertGreaterEqual(BACKTEST_WINDOW, LEARNING_CADENCE_TRADES)

    def test_aucune_famille_ne_sajuste_avant_50_trades(self):
        self._trades(49)
        self.engine.run()
        for key in ("filters", "weights", "exits", "risk"):
            self.assertFalse(
                self.params.get(f"learning.last_adjustment_at.{key}"),
                f"{key} ne doit pas être évalué avant 50 trades",
            )

    def test_chaque_famille_au_plus_tous_les_50_trades(self):
        self._trades(50)
        self.engine.run()
        for key in ("filters", "weights", "exits", "risk"):
            self.assertEqual(self.params.get(f"learning.last_adjustment_at.{key}"), 50)
        # 49 trades plus tard : aucun palier ne redéclenche.
        self._trades(49)
        for _ in range(3):
            self.engine.run()
        for key in ("filters", "weights", "exits", "risk"):
            self.assertEqual(self.params.get(f"learning.last_adjustment_at.{key}"), 50)
        self._trades(1)
        self.engine.run()
        for key in ("filters", "weights", "exits", "risk"):
            self.assertEqual(self.params.get(f"learning.last_adjustment_at.{key}"), 100)

    def test_palier_herite_de_lancienne_cadence(self):
        # Un bras qui s'était ajusté à 118 avec l'ancienne cadence attend 168.
        self.params.set("learning.last_adjustment_at.filters", 118, log=False)
        self._trades(130)
        self.engine.run()
        self.assertEqual(self.params.get("learning.last_adjustment_at.filters"), 118)

    def test_cadence_surchargeable_par_bras(self):
        self.params.set("learning.cadence_trades", 80, log=False)
        self.assertEqual(self.engine.cadence(), 80)
        self.params.set("learning.cadence_trades", 3, log=False)
        self.assertEqual(self.engine.cadence(), 10)  # plancher

    def test_refresh_stats_conserve_la_surcharge(self):
        self.params.set("learning.cadence_trades", 80, log=False)
        self._trades(5)
        self.engine.refresh_stats()
        self.assertEqual(self.engine.cadence(), 80)


class TestPetitsPas(Base):
    def _set(self, path, value):
        return self.engine._bounded_set(path, value, "test", 50)

    def test_take_profit_1_plus_15_pts_max(self):
        self._set("exit_rules.take_profit_1", 150.0)
        self.assertEqual(self.params.get("exit_rules.take_profit_1"), 90.0)

    def test_liquidite_15_pourcent_max(self):
        self._set("filters.min_liquidity_usd", 80000)
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 46000)

    def test_liquidite_plancher_5000_pour_les_relachements(self):
        self.params.set("filters.min_liquidity_usd", 15000, log=False)
        self._set("filters.min_liquidity_usd", 5000)
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 10000)

    def test_age_max_2h(self):
        self._set("filters.max_age_hours", 16.5)
        self.assertEqual(self.params.get("filters.max_age_hours"), 18.5)

    def test_duree_max_30_min(self):
        self._set("exit_rules.max_hold_time_minutes", 420)
        self.assertEqual(self.params.get("exit_rules.max_hold_time_minutes"), 360)

    def test_stop_loss_2_pts(self):
        self._set("exit_rules.stop_loss_pct", -40.0)
        self.assertEqual(self.params.get("exit_rules.stop_loss_pct"), -27.0)

    def test_le_garde_fou_du_stop_effectif_garde_le_dernier_mot(self):
        self.params.set("exit_rules.stop_loss_slippage_buffer_pct", 2.0, log=False)
        self.params.set("exit_rules.stop_loss_pct", -18.0, log=False)
        self._set("exit_rules.stop_loss_pct", -10.0)
        effectif = (self.params.get("exit_rules.stop_loss_pct")
                    + self.params.get("exit_rules.stop_loss_slippage_buffer_pct"))
        self.assertLessEqual(effectif, MIN_EFFECTIVE_STOP_LOSS_PCT)

    def test_les_bornes_restent_appliquees(self):
        self.params.set("filters.min_liquidity_usd", 95000, log=False)
        self._set("filters.min_liquidity_usd", 500000)
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 100000)

    def test_defaut_10_pourcent(self):
        self._set("filters.min_holders", 2000)
        self.assertEqual(self.params.get("filters.min_holders"), 125)  # plancher 25

    def test_search_exits_avance_par_petits_pas(self):
        # Le rejeu recommande SL -40 / TP1 150 : on ne fait qu'un pas.
        self.engine._instrumented = lambda window=BACKTEST_WINDOW: [{}] * 20
        self.engine.simulate_exits = lambda positions, rules, ambiguity="x": {
            "pnl_per_trade": -5.0, "degraded": 0}
        self.engine.exit_grid = lambda positions, current: [{
            "stop_loss_pct": -40.0, "take_profit_1": 150.0, "changed": 10,
            "pnl_per_trade": 5.0, "ambiguous": 0}]
        self.engine._search_exits()
        self.assertEqual(self.params.get("exit_rules.stop_loss_pct"), -27.0)
        self.assertEqual(self.params.get("exit_rules.take_profit_1"), 90.0)

    def test_poids_002_max(self):
        self.params.set("learning", {"win_rate_by_social_score": {
            "faible": {"win_rate": 10.0, "sample": 30}}}, log=False)
        self.engine._adjust_weights(self.params.get("learning"))
        weights = self.params.get("scoring_weights")
        self.assertAlmostEqual(weights["social_sentiment"], 0.18, places=3)
        for key, before in PARAMS["scoring_weights"].items():
            self.assertLessEqual(abs(weights[key] - before), 0.02 + 1e-9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
