"""Corrections du 2026-10-24 : coût de sortie ramené à la mise + filtre anti-pump."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.exit_fees import ExitCost  # noqa: E402
from src.core.journal import TradeJournal  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.core.portfolio import PaperPortfolio  # noqa: E402
from src.core.positions import Position, cost_vs_entry  # noqa: E402
from src.core.risk import anti_pump_verdict, is_soft_reject, pre_trade_check  # noqa: E402


def cand(**kw):
    base = dict(token_address="m", symbol="T", name="T", chain="solana",
                price_usd=1.0, liquidity_usd=30000, volume_1h=20000)
    base.update(kw)
    return Candidate(**base)


class TestCoutRameneALaMise(unittest.TestCase):
    def test_formule(self):
        self.assertAlmostEqual(cost_vs_entry(10.0, 0.5, 1.0), 5.0)
        self.assertEqual(cost_vs_entry(0.0, 0.5, 1.0), 0.0)
        self.assertEqual(cost_vs_entry(3.0, 1.0, 0.0), 3.0)

    def test_kaite_moins_99_5(self):
        """KAITE 21/10 : token à -99,5 %, coût mesuré ~3 728 % du notionnel revendu.

        Avant : PnL -3 828 %. Après : le coût vaut ~18,6 % de la mise
        (3 728 × 0,005), PnL ~ -118 %, borné par le réel (frais ≈ quelques $).
        """
        tmp = tempfile.mkdtemp()
        cost = ExitCost(price_impact_pct=0.0, priority_fee_usd=1.0,
                        total_cost_pct=3728.0, reason="test")
        pf = PaperPortfolio(1000.0, TradeJournal(os.path.join(tmp, "t.jsonl")),
                            positions_path=os.path.join(tmp, "o.json"),
                            cooldown_hours=0, max_drawdown_stop_pct=0,
                            exit_fee_measurer=lambda addr, notional: cost)
        pos = Position(token_address="kaite", symbol="KAITE", chain="solana",
                       entry_price=1.0, size_usd=50.0)
        pf.positions[pos.id] = pos
        row = pf.force_close(pos.id, 0.005, reason="STOP_LOSS")
        self.assertIsNotNone(row)
        pnl = row.get("pnl_pct")
        self.assertGreater(pnl, -120.0)
        self.assertAlmostEqual(pnl, -99.5 - 3728.0 * 0.005, places=1)
        self.assertEqual(row.get("exit_cost_pct"), 3728.0)  # mesure brute conservée


class TestFiltreAntiPump(unittest.TestCase):
    def test_score_trop_haut(self):
        r = anti_pump_verdict(cand(alpha_score=80), max_score_alpha=75, max_volume_liquidity_ratio=5)
        self.assertIn("score alpha", r)

    def test_ratio_trop_haut(self):
        r = anti_pump_verdict(cand(alpha_score=50, volume_liquidity_ratio=6.2),
                              max_score_alpha=75, max_volume_liquidity_ratio=5)
        self.assertIn("volume/liquidité", r)

    def test_passe(self):
        self.assertIsNone(anti_pump_verdict(cand(alpha_score=75, volume_liquidity_ratio=5),
                                            max_score_alpha=75, max_volume_liquidity_ratio=5))

    def test_desactive(self):
        self.assertIsNone(anti_pump_verdict(cand(alpha_score=99, volume_liquidity_ratio=50)))
        self.assertIsNone(anti_pump_verdict(cand(alpha_score=99), max_score_alpha=0))

    def test_via_pre_trade_check(self):
        tmp = tempfile.mkdtemp()
        pf = PaperPortfolio(1000.0, TradeJournal(os.path.join(tmp, "t.jsonl")),
                            positions_path=os.path.join(tmp, "o.json"),
                            cooldown_hours=0, max_drawdown_stop_pct=0)
        r = pre_trade_check(mode="PAPER", candidate=cand(alpha_score=90), portfolio=pf,
                            max_positions=3, max_score_alpha=75, max_volume_liquidity_ratio=5)
        self.assertTrue(r and r.startswith("filtre anti-pump"))
        self.assertTrue(is_soft_reject(r))
        ok = pre_trade_check(mode="PAPER", candidate=cand(alpha_score=60), portfolio=pf,
                             max_positions=3, max_score_alpha=75, max_volume_liquidity_ratio=5)
        self.assertIsNone(ok)


class TestConfig(unittest.TestCase):
    def test_seuils_dans_tous_les_bras(self):
        import glob, json
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        files = [os.path.join(root, "config/params.json")] + glob.glob(os.path.join(root, "config/arms/*.json"))
        for f in files:
            d = json.load(open(f))
            self.assertEqual(d["entry_rules"]["max_score_alpha"], 75, f)
            attendu = 2 if f.endswith("tp1_20_vl2.json") else 5  # seul bras test a 2
            self.assertEqual(d["entry_rules"]["max_volume_liquidity_ratio"], attendu, f)
            self.assertEqual(d["mode"], "PAPER", f)


if __name__ == "__main__":
    unittest.main()


class TestCorrectionLecture(unittest.TestCase):
    def test_ligne_ancienne_corrigee_une_seule_fois(self):
        from src.core.journal import correct_legacy_cost
        row = {"pnl_pct": -3828.0, "pnl_usd": -1914.0, "exit_cost_pct": 3728.0, "entry_price": 1.0,
               "exit_price": 0.005, "timestamp_exit": "2026-10-21T10:00:00+00:00"}
        out = correct_legacy_cost(dict(row))
        self.assertAlmostEqual(out["pnl_pct"], -3828.0 + 3728.0 - 3728.0 * 0.005, places=3)
        self.assertAlmostEqual(out["pnl_usd"], -1914.0 * out["pnl_pct"] / -3828.0, places=2)
        self.assertEqual(out["pnl_pct_journal"], -3828.0)
        self.assertEqual(correct_legacy_cost(dict(out))["pnl_pct"], out["pnl_pct"])

    def test_ligne_recente_ou_marquee_intacte(self):
        from src.core.journal import correct_legacy_cost
        new = {"pnl_pct": -46.0, "pnl_usd": -46.0, "exit_cost_pct": 10.0, "entry_price": 1.0,
               "exit_price": 0.6, "timestamp_exit": "2026-10-24T09:30:00+00:00"}
        self.assertEqual(correct_legacy_cost(dict(new))["pnl_pct"], -46.0)
        marked = dict(new, timestamp_exit="2026-10-01T00:00:00+00:00", cost_basis="stake")
        self.assertEqual(correct_legacy_cost(dict(marked))["pnl_pct"], -46.0)


if __name__ == "__main__":
    unittest.main()
