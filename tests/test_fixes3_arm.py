"""Bras de test fixes3 (2026-11-02) : 3 corrections approuvées par Killian."""
import json, os, sys, unittest
from dataclasses import replace
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.core.models import Candidate  # noqa: E402
from src.pipeline import ScanPipeline as Pipeline  # noqa: E402
from tests.test_tp1_arms import _position, _tick  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cand(**kw):
    return Candidate(
        token_address="addr", symbol="TST", name="TST", chain="solana",
        price_usd=1.0, liquidity_usd=50000,
        price_change_5m=kw.get("p5", 0.0), price_change_1h=kw.get("p1h", 0.0),
    )


class TestAntiChasse(unittest.TestCase):
    def test_refuse_pump_5m(self):
        r = Pipeline._context_reason(_cand(p5=12.0), {"max_price_change_5m": 10.0})
        self.assertIn("5m", r)

    def test_accepte_sous_le_plafond(self):
        self.assertIsNone(Pipeline._context_reason(_cand(p5=9.9), {"max_price_change_5m": 10.0}))

    def test_absent_desactive(self):
        self.assertIsNone(Pipeline._context_reason(_cand(p5=80.0), {}))

    def test_refuse_pump_1h(self):
        self.assertIsNotNone(Pipeline._context_reason(_cand(p1h=250.0), {"max_price_change_1h": 200.0}))


class TestBreakeven20(unittest.TestCase):
    def test_stop_au_prix_dentree_des_plus_20(self):
        pos = replace(_position(tp1=150.0), breakeven_trigger=20.0)
        pos, actions = _tick(pos, 1.21)
        self.assertEqual(actions, [])
        self.assertTrue(pos.breakeven_moved)
        self.assertEqual(pos.effective_stop_loss_pct, 0.0)
        pos, actions = _tick(pos, 0.99)
        self.assertTrue(actions[0].reason.startswith("BREAKEVEN_STOP"))

    def test_pas_arme_sous_20(self):
        pos = replace(_position(tp1=150.0), breakeven_trigger=20.0)
        pos, _ = _tick(pos, 1.19)
        self.assertFalse(pos.breakeven_moved)


class TestManifeste(unittest.TestCase):
    def test_overrides_et_isolation(self):
        d = json.load(open(os.path.join(ROOT, "config/strategies.json")))
        e = [a for a in d["arms"] if a["name"] == "fixes3"][0]
        self.assertEqual(e["role"], "test")
        self.assertEqual(e["capital_pct"], 0.0)
        self.assertFalse(e["learning"])
        o = e["overrides"]
        self.assertEqual(o["entry_rules.max_exit_cost_pct_entry"], 3.0)
        self.assertEqual(o["exit_rules.breakeven_trigger"], 20.0)
        self.assertTrue(o["exit_rules.breakeven_stop_exact"])
        self.assertEqual(o["filters.max_price_change_5m"], 10.0)


class TestBaseTp115(unittest.TestCase):
    def test_copie_de_tp1_15(self):
        f = json.load(open(os.path.join(ROOT, "config/arms/fixes3.json")))
        t = json.load(open(os.path.join(ROOT, "config/arms/tp1_15.json")))
        self.assertEqual(f["filters"]["min_liquidity_usd"], t["filters"]["min_liquidity_usd"])
        self.assertEqual(f["exit_rules"]["take_profit_1"], 15.0)
        self.assertEqual(f["exit_rules"]["breakeven_trigger"], 20.0)
        self.assertEqual(f["filters"]["max_price_change_5m"], 10.0)
        self.assertEqual(f["entry_rules"]["max_exit_cost_pct_entry"], 3.0)
        self.assertEqual(f["mode"], "PAPER")


if __name__ == "__main__":
    unittest.main()
