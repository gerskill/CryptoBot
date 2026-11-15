"""Bras de test tp1_15 / tp1_20 / tp1_30 (2026-10-04).

Verrouille :
  - le breakeven EXACT après TP1 (opt-in `breakeven_stop_exact`), sans
    changer le comportement par défaut (0 + tampon) des bras existants ;
  - le trailing activé dès TP1 sur la moitié restante ;
  - l'isolation du rôle `test` (ne vote pas, apprentissage figé, hors pool).
"""

import json
import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import arm as arm_module  # noqa: E402
from src.core.arm import bootstrap_arms  # noqa: E402
from src.core.positions import (  # noqa: E402
    Position,
    apply_exit,
    evaluate_exits,
    update_water_marks,
)
from tests.test_arms import ArmsTestCase  # noqa: E402


def _position(tp1: float = 15.0, exact: bool = True) -> Position:
    return Position(
        token_address="TOKEN",
        symbol="TST",
        chain="solana",
        entry_price=1.0,
        size_usd=100.0,
        stop_loss_pct=-25.0,
        stop_loss_slippage_buffer_pct=10.0,
        take_profit_1=tp1,
        take_profit_2=300.0,
        take_profit_3=500.0,
        partial_sell_tp1_pct=0.5,
        trailing_stop_activation=tp1,
        trailing_stop_distance_pct=20.0,
        breakeven_trigger=100.0,
        breakeven_stop_exact=exact,
    )


def _tick(position: Position, price: float):
    """Même séquence que `PaperPortfolio.update` : marques, puis sorties."""
    position = update_water_marks(position, price)
    actions = evaluate_exits(position, price)
    for action in actions:
        position = apply_exit(position, action, price)
    return position, actions


class TestBreakevenApresTP1(unittest.TestCase):
    def test_stop_initial_inchange_avec_tampon(self):
        pos = _position()
        self.assertEqual(pos.effective_stop_loss_pct, -15.0)

    def test_tp1_vend_50_et_remonte_le_stop_au_prix_dentree(self):
        pos, actions = _tick(_position(15.0), 1.16)
        self.assertEqual(len(actions), 1)
        self.assertTrue(actions[0].reason.startswith("TAKE_PROFIT_1"))
        self.assertAlmostEqual(actions[0].fraction, 0.5)
        self.assertAlmostEqual(pos.remaining_fraction, 0.5)
        self.assertTrue(pos.breakeven_moved)
        self.assertEqual(pos.effective_stop_loss_pct, 0.0, "breakeven exact, tampon ignoré")

        # +5 % : au-dessus du prix d'entrée, la moitié restante reste ouverte
        pos, actions = _tick(pos, 1.05)
        self.assertEqual(actions, [])
        # sous le prix d'entrée : sortie BREAKEVEN de tout le reste
        pos, actions = _tick(pos, 0.995)
        self.assertEqual(len(actions), 1)
        self.assertTrue(actions[0].reason.startswith("BREAKEVEN_STOP"))
        self.assertFalse(pos.is_open)

    def test_defaut_inchange_pour_les_bras_existants(self):
        pos, _ = _tick(_position(15.0, exact=False), 1.16)
        self.assertTrue(pos.breakeven_moved)
        self.assertEqual(pos.effective_stop_loss_pct, 10.0, "0 + tampon, comme avant")

    def test_champ_absent_vaut_false(self):
        pos = replace(_position(), breakeven_stop_exact=False)
        self.assertFalse(Position(token_address="a", symbol="b", chain="c",
                                  entry_price=1, size_usd=1).breakeven_stop_exact)
        self.assertFalse(pos.breakeven_stop_exact)


class TestTrailingApresTP1(unittest.TestCase):
    def test_trailing_actif_des_tp1_puis_sortie_sur_repli(self):
        pos, _ = _tick(_position(20.0), 1.21)
        self.assertTrue(pos.trailing_active)
        self.assertTrue(pos.tp1_hit)
        pos, actions = _tick(pos, 1.60)  # plus-haut +60 %
        self.assertEqual(actions, [])
        pos, actions = _tick(pos, 1.45)  # repli de 15 pts : on garde
        self.assertEqual(actions, [])
        pos, actions = _tick(pos, 1.39)  # repli de 21 pts : trailing
        self.assertEqual(len(actions), 1)
        self.assertTrue(actions[0].reason.startswith("TRAILING_STOP"))
        self.assertFalse(pos.is_open)
        # 50 % à +21 % puis 50 % à +39 % sur 100 $
        self.assertAlmostEqual(pos.realized_pnl_usd, 50 * 0.21 + 50 * 0.39, places=6)

    def test_pas_de_trailing_avant_tp1(self):
        pos, actions = _tick(_position(30.0), 1.25)
        self.assertFalse(pos.trailing_active)
        self.assertEqual(actions, [])


class TestRoleTest(ArmsTestCase):
    def test_bras_test_isole(self):
        self._manifest([
            {"name": "baseline", "role": "voter", "capital_pct": 1.0, "notify": "all"},
            {"name": "tp1_15", "role": "test", "capital_pct": 0.0, "learning": False,
             "overrides": {"exit_rules.take_profit_1": 15.0,
                           "exit_rules.breakeven_stop_exact": True}},
        ])
        arms = {a.name: a for a in bootstrap_arms()}
        test = arms["tp1_15"]
        self.assertTrue(test.is_test)
        self.assertFalse(test.is_voter, "un bras de test ne vote pas")
        self.assertIsNone(test.learning, "paramètres figés")
        self.assertIsNotNone(arms["baseline"].learning)
        self.assertFalse(arms["baseline"].is_test)
        self.assertTrue(test.params.get("exit_rules.breakeven_stop_exact"))
        self.assertEqual(test.params.get("exit_rules.take_profit_1"), 15.0)
        self.assertEqual(test.params.get("filters"), arms["baseline"].params.get("filters"))

    def test_bras_test_hors_pool_des_autres(self):
        self._manifest([
            {"name": "baseline", "role": "voter", "capital_pct": 0.5, "notify": "all"},
            {"name": "runner", "role": "voter", "capital_pct": 0.5},
            {"name": "tp1_15", "role": "test", "capital_pct": 0.0, "learning": False},
        ])
        arms = {a.name: a for a in bootstrap_arms()}
        for name in ("runner", "tp1_15"):
            path = arms[name].journal.path
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "a").close()
        original = arm_module.TradeJournal.read_positions
        arm_module.TradeJournal.read_positions = lambda self: [{"src": self.path}]
        try:
            sources = [row["src"] for row in arms["baseline"].learning.pool()]
        finally:
            arm_module.TradeJournal.read_positions = original
        self.assertEqual(sources, [arms["runner"].journal.path],
                         "le journal d'un bras de test n'entre pas dans le rejeu des autres")

class TestQualityConsensusTp1(unittest.TestCase):
    """quality et consensus vendent 50 % à +15 %, puis le reste au prix d'entrée.

    Même séquence que tp1_15 (`update_water_marks` puis `evaluate_exits`).
    Les autres réglages de sortie de ces bras ne bougent pas, sauf
    `max_hold_time_minutes` que l'apprentissage allonge (time stop).
    """

    def _rules(self, name):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "arms", f"{name}.json",
        )
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)["exit_rules"]

    def test_reglages_et_sortie(self):
        attendu = {
            "quality": {
                "stop_loss_pct": -17.6,
                "trailing_stop_activation": 150.0,
                "trailing_stop_distance_pct": 80,
                "stop_loss_slippage_buffer_pct": 2.6,
                "take_profit_2": 250.0,
            },
            "consensus": {
                "stop_loss_pct": -21.0,
                "trailing_stop_activation": 150.0,
                "trailing_stop_distance_pct": 80,
                "stop_loss_slippage_buffer_pct": 6.0,
                "take_profit_2": 250.0,
            },
        }
        for name, figes in attendu.items():
            rules = self._rules(name)
            self.assertEqual(rules["take_profit_1"], 15.0, name)
            self.assertTrue(rules["breakeven_stop_exact"], name)
            self.assertEqual(rules["partial_sell_tp1_pct"], 0.5, name)
            for key, value in figes.items():
                self.assertEqual(rules[key], value, f"{name}.{key}")
            pos = _position(tp1=rules["take_profit_1"], exact=True)
            pos = replace(
                pos,
                stop_loss_pct=rules["stop_loss_pct"],
                stop_loss_slippage_buffer_pct=rules["stop_loss_slippage_buffer_pct"],
                trailing_stop_activation=rules["trailing_stop_activation"],
                trailing_stop_distance_pct=rules["trailing_stop_distance_pct"],
            )
            pos, actions = _tick(pos, 1.16)  # +16 % : TP1
            self.assertEqual(len(actions), 1, name)
            self.assertTrue(actions[0].reason.startswith("TAKE_PROFIT_1"), name)
            self.assertAlmostEqual(actions[0].fraction, 0.5)
            self.assertEqual(pos.effective_stop_loss_pct, 0.0, name)
            pos, actions = _tick(pos, 0.995)
            self.assertTrue(actions[0].reason.startswith("BREAKEVEN_STOP"), name)
            self.assertFalse(pos.is_open)


class TestExpositionFlotte(unittest.TestCase):
    def test_bras_test_hors_exposition_sectorielle(self):
        from types import SimpleNamespace

        from src.main import AlphaLoop

        def bras(nom, role, n):
            positions = {
                f"{nom}{i}": replace(_position(), id=f"{nom}{i}", sector="dog")
                for i in range(n)
            }
            return SimpleNamespace(name=nom, is_test=(role == "test"),
                                   portfolio=SimpleNamespace(positions=positions))

        fake = SimpleNamespace(arms=[bras("baseline", "voter", 1), bras("tp1_15", "test", 2)])
        exposure = AlphaLoop._fleet_exposure(fake)
        self.assertEqual(exposure.total_positions, 1)


if __name__ == "__main__":
    unittest.main()
