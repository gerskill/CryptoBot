"""Tests des règles de sortie, du portefeuille papier et de l'apprentissage."""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.journal import TradeJournal  # noqa: E402
from src.core.learning import LearningEngine  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.core.params import ParamsStore  # noqa: E402
from src.core.portfolio import PaperPortfolio  # noqa: E402
from src.core.positions import (  # noqa: E402
    Position,
    apply_exit,
    evaluate_exits,
    update_high_water,
)

PARAMS_MINIMAL = {
    "version": "2.0",
    "risk_per_trade": 0.03,
    "exit_rules": {
        "stop_loss_pct": -25,
        "take_profit_1": 100,
        "take_profit_2": 300,
        "take_profit_3": 500,
        "partial_sell_tp1_pct": 0.5,
        "partial_sell_tp2_pct": 0.5,
        "trailing_stop_activation": 200,
        "trailing_stop_distance_pct": 50,
        "max_hold_time_minutes": 240,
    },
    "filters": {"min_age_hours": 0.5, "max_age_hours": 6, "min_liquidity_usd": 15000},
    "scoring_weights": {
        "liquidity": 0.20, "volume_momentum": 0.25, "social_sentiment": 0.20,
        "smart_money": 0.20, "rugcheck": 0.15,
    },
    "learning": {},
}


def make_position(**kwargs) -> Position:
    base = dict(token_address="mint1", symbol="TEST", chain="solana",
                entry_price=1.0, size_usd=100.0)
    base.update(kwargs)
    return Position(**base)


def make_candidate(**kwargs) -> Candidate:
    base = dict(token_address="mint1", symbol="TEST", name="Test", chain="solana",
                price_usd=1.0, liquidity_usd=30000, volume_1h=20000)
    base.update(kwargs)
    return Candidate(**base)


class TestExitRules(unittest.TestCase):
    def test_stop_loss_vend_tout(self):
        actions = evaluate_exits(make_position(), price=0.70)  # -30%
        self.assertEqual(len(actions), 1)
        self.assertTrue(actions[0].is_final)
        self.assertIn("STOP_LOSS", actions[0].reason)

    def test_pas_de_sortie_dans_la_zone_neutre(self):
        self.assertEqual(evaluate_exits(make_position(), price=1.20), [])

    def test_tp1_vend_la_moitie_et_passe_au_breakeven(self):
        position = make_position()
        actions = evaluate_exits(position, price=2.0)  # +100%
        self.assertEqual(actions[0].fraction, 0.5)

        position = apply_exit(position, actions[0], 2.0)
        self.assertTrue(position.tp1_hit)
        self.assertTrue(position.breakeven_moved)
        self.assertEqual(position.effective_stop_loss_pct, 0.0)
        self.assertAlmostEqual(position.remaining_fraction, 0.5)
        self.assertAlmostEqual(position.realized_pnl_usd, 50.0)

    def test_breakeven_protege_apres_tp1(self):
        # Après TP1, un retour au prix d'entrée doit sortir, pas attendre -25%.
        position = make_position(tp1_hit=True, breakeven_moved=True, remaining_fraction=0.5)
        actions = evaluate_exits(position, price=0.99)
        self.assertIn("BREAKEVEN_STOP", actions[0].reason)

    def test_tp2_active_le_trailing(self):
        position = make_position(tp1_hit=True, breakeven_moved=True, remaining_fraction=0.5)
        actions = evaluate_exits(position, price=4.0)  # +300%
        position = apply_exit(position, actions[0], 4.0)
        self.assertTrue(position.tp2_hit)
        self.assertTrue(position.trailing_active)
        self.assertAlmostEqual(position.remaining_fraction, 0.25)

    def test_tp3_vend_tout_le_reste(self):
        position = make_position(remaining_fraction=0.25, tp1_hit=True, tp2_hit=True)
        actions = evaluate_exits(position, price=6.0)  # +500%
        self.assertTrue(actions[0].is_final)
        self.assertAlmostEqual(actions[0].fraction, 0.25)

    def test_trailing_stop_se_declenche_sur_repli(self):
        position = make_position(remaining_fraction=0.25, trailing_active=True,
                                 high_water_pct=400.0)
        actions = evaluate_exits(position, price=4.4)  # repli de 60 > distance 50
        self.assertIn("TRAILING_STOP", actions[0].reason)

    def test_trailing_ne_se_declenche_pas_avant_la_distance(self):
        position = make_position(remaining_fraction=0.25, trailing_active=True,
                                 high_water_pct=400.0)
        actions = evaluate_exits(position, price=4.8)  # repli de 20
        self.assertFalse(any("TRAILING" in a.reason for a in actions))

    def test_time_stop(self):
        position = make_position(entry_time=time.time() - 300 * 60)
        actions = evaluate_exits(position, price=1.5)
        self.assertTrue(actions[0].is_final)
        self.assertIn("TIME_STOP", actions[0].reason)

    def test_rug_pull_prioritaire_sur_take_profit(self):
        # Prix à +400% mais liquidité effondrée : on sort en rug, pas en TP.
        actions = evaluate_exits(make_position(), price=5.0, liquidity_drop_pct=-80)
        self.assertEqual(len(actions), 1)
        self.assertIn("RUG_PULL", actions[0].reason)

    def test_high_water_suit_le_plus_haut(self):
        position = update_high_water(make_position(), 3.0)  # +200%
        self.assertAlmostEqual(position.high_water_pct, 200.0)
        self.assertTrue(position.trailing_active)

        position = update_high_water(position, 2.0)  # redescend
        self.assertAlmostEqual(position.high_water_pct, 200.0)


class TestPaperPortfolio(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))
        self.portfolio = PaperPortfolio(capital=1000.0, journal=self.journal)

    def test_taille_plafonnee_a_5pct(self):
        size = self.portfolio.position_size(risk_per_trade=0.20, win_rate=0.9, total_trades=100)
        self.assertLessEqual(size, 50.0)

    def test_kelly_inactif_sur_petit_echantillon(self):
        small = self.portfolio.position_size(risk_per_trade=0.03, win_rate=0.9, total_trades=5)
        self.assertAlmostEqual(small, 30.0)

    def test_refuse_au_dela_du_max_positions(self):
        self.portfolio.open(make_candidate(), PARAMS_MINIMAL, 30.0)
        blocker = self.portfolio.can_open(make_candidate(token_address="autre"), max_positions=1)
        self.assertIn("positions déjà ouvertes", blocker)

    def test_refuse_doublon_sur_le_meme_token(self):
        self.portfolio.open(make_candidate(), PARAMS_MINIMAL, 30.0)
        self.assertIn("déjà ouverte", self.portfolio.can_open(make_candidate(), max_positions=5))

    def test_cooldown_apres_3_pertes(self):
        for index in range(3):
            position = self.portfolio.open(
                make_candidate(token_address=f"mint{index}"), PARAMS_MINIMAL, 30.0
            )
            self.portfolio.update(position.id, price=0.5)  # -50% -> stop loss
        self.assertTrue(self.portfolio.in_cooldown)
        blocker = self.portfolio.can_open(make_candidate(token_address="neuf"), max_positions=5)
        self.assertIn("cooldown", blocker)

    def test_gain_remet_le_compteur_de_pertes_a_zero(self):
        losing = self.portfolio.open(make_candidate(token_address="a"), PARAMS_MINIMAL, 30.0)
        self.portfolio.update(losing.id, price=0.5)
        self.assertEqual(self.portfolio.consecutive_losses, 1)

        winning = self.portfolio.open(make_candidate(token_address="b"), PARAMS_MINIMAL, 30.0)
        self.portfolio.update(winning.id, price=7.0)  # +600% -> TP3
        self.assertEqual(self.portfolio.consecutive_losses, 0)

    def test_cycle_complet_journalise_le_trade(self):
        position = self.portfolio.open(make_candidate(), PARAMS_MINIMAL, 100.0)
        rows = self.portfolio.update(position.id, price=0.6)  # -40%
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["is_final_exit"])
        self.assertLess(rows[0]["pnl_usd"], 0)
        self.assertEqual(len(self.journal.read_final_exits()), 1)

    def test_force_close_ferme_tout(self):
        position = self.portfolio.open(make_candidate(), PARAMS_MINIMAL, 100.0)
        row = self.portfolio.force_close(position.id, price=1.5, reason="PANIC_STOP")
        self.assertEqual(row["exit_reason"], "PANIC_STOP")
        self.assertEqual(len(self.portfolio.positions), 0)
        self.assertAlmostEqual(row["pnl_pct"], 50.0)

    def test_stats_win_rate_et_profit_factor(self):
        winner = self.portfolio.open(make_candidate(token_address="a"), PARAMS_MINIMAL, 100.0)
        self.portfolio.update(winner.id, price=7.0)
        loser = self.portfolio.open(make_candidate(token_address="b"), PARAMS_MINIMAL, 100.0)
        self.portfolio.update(loser.id, price=0.5)

        stats = self.portfolio.stats()
        self.assertEqual(stats["total_trades"], 2)
        self.assertEqual(stats["win_rate"], 50.0)
        self.assertGreater(stats["profit_factor"], 1)


class TestLearning(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.params_path = os.path.join(self.tmp, "params.json")
        with open(self.params_path, "w", encoding="utf-8") as fh:
            json.dump(PARAMS_MINIMAL, fh)
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))
        self.params = ParamsStore(self.params_path)
        self.engine = LearningEngine(self.params, self.journal)

    def _write_trades(self, count, pnl_pct, age_hours, liquidity=30000):
        for index in range(count):
            self.journal._append({
                "id": f"t{index}", "is_final_exit": True, "pnl_pct": pnl_pct,
                "pnl_usd": pnl_pct, "age_hours_at_entry": age_hours,
                "liquidity_at_entry": liquidity, "social_score": None,
                "exit_reason": "STOP_LOSS" if pnl_pct < 0 else "TAKE_PROFIT_1",
            })

    def test_pas_dajustement_sous_le_seuil_dechantillon(self):
        # 6 trades perdants : au-dessus de la cadence de 5, mais sous les
        # 10 trades requis DANS le segment.
        self._write_trades(6, pnl_pct=-25, age_hours=0.5)
        self.engine.run()
        self.assertEqual(self.params.get("filters.min_age_hours"), 0.5)

    def test_ajuste_lage_minimum_quand_le_segment_jeune_perd(self):
        self._write_trades(12, pnl_pct=-25, age_hours=0.5)
        changes = self.engine.run()
        self.assertEqual(self.params.get("filters.min_age_hours"), 1.0)
        self.assertTrue(any("min_age_hours" in c for c in changes))

    def test_cadence_ne_rejoue_pas_le_meme_palier(self):
        self._write_trades(12, pnl_pct=-25, age_hours=0.5)
        self.engine.run()
        first = self.params.get("filters.min_age_hours")
        self.engine.run()
        self.assertEqual(self.params.get("filters.min_age_hours"), first)

    def test_stats_globales(self):
        self._write_trades(5, pnl_pct=100, age_hours=2)
        self._write_trades(5, pnl_pct=-25, age_hours=2)
        learning = self.engine.refresh_stats()
        self.assertEqual(learning["total_trades"], 10)
        self.assertEqual(learning["winning_trades"], 5)
        self.assertEqual(learning["avg_win_pct"], 100)
        self.assertEqual(learning["avg_loss_pct"], -25)

    def _authorize_owner(self):
        """Lève le verrou propriétaire — sans lui, LIVE est refusé quoi qu'il arrive."""
        self.params.set("live_mode_authorized_by_owner", True, log=False)

    def test_live_refuse_sans_autorisation_du_proprietaire(self):
        # Statistiques excellentes, mais le verrou n'est pas levé.
        self._write_trades(12, pnl_pct=100, age_hours=2)
        self._write_trades(8, pnl_pct=-25, age_hours=2)
        allowed, why = self.engine.live_mode_allowed()
        self.assertFalse(allowed)
        self.assertIn("verrou propriétaire", why)

    def test_live_bloque_sous_20_trades(self):
        self._authorize_owner()
        self._write_trades(10, pnl_pct=100, age_hours=2)
        allowed, why = self.engine.live_mode_allowed()
        self.assertFalse(allowed)
        self.assertIn("10/20", why)

    def test_live_bloque_si_win_rate_insuffisant(self):
        self._authorize_owner()
        self._write_trades(5, pnl_pct=100, age_hours=2)
        self._write_trades(15, pnl_pct=-25, age_hours=2)
        allowed, why = self.engine.live_mode_allowed()
        self.assertFalse(allowed)
        self.assertIn("win rate", why)

    def test_live_autorise_seulement_si_verrou_leve_ET_criteres_remplis(self):
        self._authorize_owner()
        self._write_trades(12, pnl_pct=100, age_hours=2)
        self._write_trades(8, pnl_pct=-25, age_hours=2)
        self.assertTrue(self.engine.live_mode_allowed()[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
