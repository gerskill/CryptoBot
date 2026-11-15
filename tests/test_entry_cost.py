"""Coût d'entrée PAPER : la moitié du même aller-retour qu'à la sortie.

Les positions sans `entry_cost_version` gardent l'ancien décompte
(tout sur la vente). On ne réécrit pas leur journal.
"""

import os
import sys
import tempfile
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.journal import TradeJournal  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.core.portfolio import PaperPortfolio  # noqa: E402


PARAMS = {
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
    "filters": {},
    "scoring_weights": {},
    "learning": {},
}


class Cost:
    def __init__(self, total):
        self.total_cost_pct = total
        self.partial = False
        self.price_impact_pct = total
        self.priority_fee_usd = 0.0


def candidate(token="mint1"):
    return Candidate(
        token_address=token, symbol="TEST", name="Test", chain="solana",
        price_usd=1.0, liquidity_usd=30000, volume_1h=20000,
    )


class TestCoutEntree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))
        self.positions = os.path.join(self.tmp, "positions.json")

    def _portfolio(self, total=10.0):
        def measurer(token, size):
            return Cost(total)

        return PaperPortfolio(
            capital=1000.0,
            journal=self.journal,
            positions_path=self.positions,
            exit_fee_measurer=measurer,
        )

    def test_prix_inchange_total_identique_a_l_ancien_modele(self):
        """À prix égal, moitié + moitié = l'aller-retour d'avant, pas le double."""
        portfolio = self._portfolio(10.0)
        position = portfolio.open(candidate(), PARAMS, 100.0)
        self.assertEqual(position.entry_cost_version, "buy-leg-v1")
        self.assertEqual(position.entry_cost_pct, 5.0)
        self.assertTrue(position.entry_impact_charged)
        # Le cash a déjà perdu la moitié d'achat (5 $) en plus de la mise.
        self.assertAlmostEqual(portfolio.capital, 1000.0 - 100.0 - 5.0)

        row = portfolio.force_close(position.id, price=1.0, reason="MANUAL")
        self.assertEqual(row["exit_cost_pct"], 5.0)
        self.assertEqual(row["exit_cost_round_trip_pct"], 10.0)
        self.assertAlmostEqual(row["pnl_pct"], -10.0)
        self.assertAlmostEqual(portfolio.capital, 990.0)

    def test_position_ancienne_garde_le_cout_complet_a_la_sortie(self):
        portfolio = self._portfolio(10.0)
        position = portfolio.open(candidate(), PARAMS, 100.0)
        # Comme une position restaurée d'avant le changement.
        portfolio.capital += position.size_usd * position.entry_cost_pct / 100.0
        portfolio.positions[position.id] = replace(
            position,
            entry_cost_version="",
            entry_cost_pct=0.0,
            entry_impact_charged=False,
        )
        rows = portfolio.update(position.id, price=0.6)
        self.assertEqual(rows[0]["exit_cost_pct"], 10.0)
        self.assertIsNone(rows[0]["exit_cost_round_trip_pct"])
        self.assertIsNone(rows[0]["entry_cost_version"])
        self.assertAlmostEqual(rows[0]["pnl_pct"], -46.0)

    def test_sans_mesureur_rien_ne_change(self):
        portfolio = PaperPortfolio(capital=1000.0, journal=self.journal)
        position = portfolio.open(candidate(), PARAMS, 100.0)
        self.assertEqual(position.entry_cost_version, "")
        rows = portfolio.update(position.id, price=0.6)
        self.assertIsNone(rows[0]["entry_cost_pct"])
        self.assertAlmostEqual(rows[0]["pnl_pct"], -40.0)

    def test_rechargement_ne_recalcule_pas_et_garde_le_frais_ouvert(self):
        portfolio = self._portfolio(10.0)
        portfolio.open(candidate(), PARAMS, 100.0)
        frais = 5.0
        self.assertAlmostEqual(portfolio.capital, 1000.0 - 100.0 - frais)

        rechargé = PaperPortfolio(
            capital=1000.0,
            journal=self.journal,
            positions_path=self.positions,
            exit_fee_measurer=lambda token, size: Cost(10.0),
        )
        self.assertEqual(len(rechargé.positions), 1)
        seule = next(iter(rechargé.positions.values()))
        self.assertEqual(seule.entry_cost_version, "buy-leg-v1")
        self.assertAlmostEqual(seule.entry_cost_pct, 5.0)
        self.assertAlmostEqual(rechargé.capital, 1000.0 - 100.0 - frais)

    def test_le_cash_egal_le_journal_une_fois_tout_vendu(self):
        portfolio = self._portfolio(10.0)
        position = portfolio.open(candidate(), PARAMS, 100.0)
        portfolio.update(position.id, price=2.0)  # TP1, moitié
        reste = portfolio.positions[position.id]
        portfolio.update(reste.id, price=6.0)  # le reste
        self.assertEqual(portfolio.positions, {})
        realized = portfolio._realized_pnl_from_journal()
        self.assertAlmostEqual(portfolio.capital, 1000.0 + realized)

    def test_un_gain_de_prix_plus_petit_que_le_frais_compte_comme_une_perte(self):
        """Le streak et la notification lisent le réalisé, pas le prix nu."""
        portfolio = self._portfolio(2.0)
        position = portfolio.open(candidate(), PARAMS, 100.0)
        row = portfolio.force_close(position.id, price=1.02, reason="MANUAL")
        self.assertLess(row["pnl_pct"], 0)
        self.assertLess(row["pnl_usd"], 0)
        self.assertEqual(portfolio.consecutive_losses, 1)

    def test_devis_deja_pris_nest_pas_redemande_a_louverture(self):
        calls = []

        def measurer(token, size):
            calls.append(size)
            return Cost(10.0)

        portfolio = PaperPortfolio(
            capital=1000.0, journal=self.journal, exit_fee_measurer=measurer,
        )
        quote = portfolio._measure_entry_leg("mint1", 100.0)
        portfolio.open(candidate(), PARAMS, 100.0, entry_quote=quote)
        self.assertEqual(calls, [100.0])


if __name__ == "__main__":
    unittest.main()
