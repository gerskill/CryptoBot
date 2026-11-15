"""Porte unique d'entrée — src/core/risk.py:pre_trade_check."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import correlation  # noqa: E402
from src.core.journal import TradeJournal  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.core.portfolio import PaperPortfolio  # noqa: E402
from src.core.positions import Position  # noqa: E402
from src.core.risk import is_soft_reject, pre_trade_check  # noqa: E402


def make_candidate(**kwargs) -> Candidate:
    base = dict(
        token_address="mint1",
        symbol="TEST",
        name="Test",
        chain="solana",
        price_usd=1.0,
        liquidity_usd=30000,
        volume_1h=20000,
    )
    base.update(kwargs)
    return Candidate(**base)


class TestPreTradeCheckPaper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))
        self.portfolio = PaperPortfolio(
            1000.0,
            self.journal,
            positions_path=os.path.join(self.tmp, "open.json"),
            cooldown_hours=0,
            max_drawdown_stop_pct=0,
        )

    def test_paper_autorise_quand_tout_est_clair(self):
        reason = pre_trade_check(
            mode="PAPER",
            candidate=make_candidate(),
            portfolio=self.portfolio,
            max_positions=3,
            fleet_exposure=correlation.measure([]),
            size_usd=30.0,
            max_sector_positions=3,
            max_sector_exposure_pct=50.0,
            risk_rules={"cooldown_hours": 0, "max_drawdown_stop_pct": 0},
            require_market_data=True,
            jupiter_ok=False,
            sellable=None,
            price_ok=False,
        )
        self.assertIsNone(reason)

    def test_paper_donnees_jupiter_manquantes_non_bloquantes(self):
        # Invariant PAPER : une donnée manquante n'empêche pas d'apprendre.
        reason = pre_trade_check(
            mode="PAPER",
            candidate=make_candidate(),
            portfolio=self.portfolio,
            max_positions=3,
            require_market_data=True,
            jupiter_ok=False,
            sellable=None,
            price_ok=False,
        )
        self.assertIsNone(reason)

    def test_paper_respecte_can_open(self):
        self.portfolio.open(
            make_candidate(token_address="deja"),
            {"exit_rules": {"stop_loss_pct": -25, "take_profit_1": 100}},
            30.0,
        )
        reason = pre_trade_check(
            mode="PAPER",
            candidate=make_candidate(token_address="autre"),
            portfolio=self.portfolio,
            max_positions=1,
        )
        self.assertIsNotNone(reason)
        self.assertIn("positions", reason)

    def test_paper_refuse_economie(self):
        reason = pre_trade_check(
            mode="PAPER",
            candidate=make_candidate(),
            portfolio=self.portfolio,
            max_positions=3,
            economics_reject="honeypot — unsellable",
        )
        self.assertEqual(reason, "honeypot — unsellable")

    def test_paper_concentration_sectorielle(self):
        positions = [
            Position(
                token_address=f"a{i}",
                symbol="TOAD",
                chain="solana",
                entry_price=1.0,
                size_usd=30.0,
            )
            for i in range(3)
        ]
        exposure = correlation.measure(positions)
        reason = pre_trade_check(
            mode="PAPER",
            candidate=make_candidate(symbol="FROG", name="Frog Coin"),
            portfolio=self.portfolio,
            max_positions=5,
            fleet_exposure=exposure,
            size_usd=30.0,
            max_sector_positions=3,
            max_sector_exposure_pct=50.0,
        )
        self.assertIsNotNone(reason)
        self.assertIn("concentration meta", reason)
        self.assertTrue(is_soft_reject(reason))


class TestPreTradeCheckLive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))
        # LIVE avec gardes armés côté portefeuille.
        self.portfolio = PaperPortfolio(
            1000.0,
            self.journal,
            positions_path=os.path.join(self.tmp, "open.json"),
            cooldown_hours=2,
            max_drawdown_stop_pct=25,
            mode="LIVE",
        )
        self.rules_ok = {"cooldown_hours": 2, "max_drawdown_stop_pct": 25}
        self.rules_disarmed = {"cooldown_hours": 0, "max_drawdown_stop_pct": 0}

    def test_live_refuse_si_pas_autorise(self):
        reason = pre_trade_check(
            mode="LIVE",
            candidate=make_candidate(),
            portfolio=self.portfolio,
            max_positions=3,
            live_allowed=(False, "WR insuffisant"),
            risk_rules=self.rules_ok,
            require_market_data=False,
        )
        self.assertIsNotNone(reason)
        self.assertIn("LIVE verrouillé", reason)

    def test_live_refuse_risk_rules_desarmes(self):
        reason = pre_trade_check(
            mode="LIVE",
            candidate=make_candidate(),
            portfolio=self.portfolio,
            max_positions=3,
            live_allowed=(True, "ok"),
            risk_rules=self.rules_disarmed,
            require_market_data=False,
        )
        self.assertIsNotNone(reason)
        self.assertIn("cooldown_hours", reason)

    def test_live_fail_closed_sans_jupiter(self):
        reason = pre_trade_check(
            mode="LIVE",
            candidate=make_candidate(),
            portfolio=self.portfolio,
            max_positions=3,
            live_allowed=(True, "ok"),
            risk_rules=self.rules_ok,
            require_market_data=True,
            jupiter_ok=False,
            sellable=True,
            price_ok=True,
        )
        self.assertIsNotNone(reason)
        self.assertIn("Jupiter", reason)

    def test_live_fail_closed_vendabilite_inconnue(self):
        reason = pre_trade_check(
            mode="LIVE",
            candidate=make_candidate(),
            portfolio=self.portfolio,
            max_positions=3,
            live_allowed=(True, "ok"),
            risk_rules=self.rules_ok,
            require_market_data=True,
            jupiter_ok=True,
            sellable=None,
            price_ok=True,
        )
        self.assertIsNotNone(reason)
        self.assertIn("vendabilité", reason)

    def test_live_autorise_si_strict_ok(self):
        reason = pre_trade_check(
            mode="LIVE",
            candidate=make_candidate(),
            portfolio=self.portfolio,
            max_positions=3,
            fleet_exposure=correlation.measure([]),
            size_usd=30.0,
            live_allowed=(True, "ok"),
            risk_rules=self.rules_ok,
            require_market_data=True,
            jupiter_ok=True,
            sellable=True,
            price_ok=True,
        )
        self.assertIsNone(reason)


class TestSoftReject(unittest.TestCase):
    def test_concentration_est_soft(self):
        self.assertTrue(
            is_soft_reject("concentration meta « chien » — 3 position(s)")
        )

    def test_portefeuille_plein_est_dur(self):
        self.assertFalse(is_soft_reject("2/2 positions déjà ouvertes"))

    def test_none_est_dur(self):
        self.assertFalse(is_soft_reject(None))


if __name__ == "__main__":
    unittest.main()
