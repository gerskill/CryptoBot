"""Corrections mécaniques (B) et instrumentation (A) issues des 10 trades.

Mesures d'origine, 10 trades papier :
  - 8 sorties SL sur 8 dépassaient leur seuil, de 4.6 points en moyenne
  - pire cas -40.5% pour un stop loss réglé à -25%
  - aucun trade n'a approché TP1 (+100%), durée de vie médiane 5.9 min
"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.journal import TradeJournal  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.core.portfolio import PaperPortfolio  # noqa: E402
from src.core.positions import Position, evaluate_exits, update_water_marks  # noqa: E402


def candidate(symbol="TEST", price=1.0, change_5m=0.0):
    return Candidate(
        token_address=f"addr_{symbol}", symbol=symbol, name=symbol, chain="solana",
        price_usd=price, liquidity_usd=30000, volume_1h=20000, price_change_5m=change_5m,
    )


class TestStopLossAnticipe(unittest.TestCase):
    """Le SL doit se déclencher AVANT son seuil pour atterrir dessus."""

    def _position(self, buffer_pct):
        return Position(
            token_address="a", symbol="T", chain="solana", entry_price=1.0,
            size_usd=30.0, stop_loss_pct=-25.0,
            stop_loss_slippage_buffer_pct=buffer_pct,
        )

    def test_sans_tampon_le_seuil_reste_brut(self):
        self.assertEqual(self._position(0.0).effective_stop_loss_pct, -25.0)

    def test_le_tampon_remonte_le_declenchement(self):
        self.assertAlmostEqual(self._position(4.6).effective_stop_loss_pct, -20.4)

    def test_sortie_declenchee_plus_tot(self):
        position = self._position(4.6)
        # -21% : sous le déclencheur (-20.4), au-dessus du seuil visé (-25)
        actions = evaluate_exits(position, price=0.79)
        self.assertEqual(len(actions), 1)
        self.assertIn("STOP_LOSS", actions[0].reason)

    def test_pas_de_sortie_au_dessus_du_declencheur(self):
        self.assertEqual(evaluate_exits(self._position(4.6), price=0.85), [])

    def test_le_tampon_sapplique_aussi_au_breakeven(self):
        # Après TP1 le stop passe à 0% ; le tampon le remonte au-dessus.
        position = self._position(4.6).__class__(
            token_address="a", symbol="T", chain="solana", entry_price=1.0,
            size_usd=30.0, stop_loss_pct=-25.0, stop_loss_slippage_buffer_pct=4.6,
            breakeven_moved=True,
        )
        self.assertAlmostEqual(position.effective_stop_loss_pct, 4.6)


class TestInstrumentationPicEtCreux(unittest.TestCase):
    """Sans mesure du pic réel, tout réglage de TP serait deviné."""

    def _position(self):
        return Position(
            token_address="a", symbol="T", chain="solana", entry_price=1.0,
            size_usd=30.0, stop_loss_pct=-25.0, trailing_stop_activation=200.0,
        )

    def test_suit_le_plus_haut(self):
        position = update_water_marks(self._position(), price=1.4)
        self.assertAlmostEqual(position.high_water_pct, 40.0)
        self.assertIsNotNone(position.high_water_at)

    def test_suit_le_plus_bas(self):
        position = update_water_marks(self._position(), price=0.8)
        self.assertAlmostEqual(position.low_water_pct, -20.0)

    def test_le_pic_ne_redescend_pas(self):
        position = update_water_marks(self._position(), price=1.5)
        position = update_water_marks(position, price=1.1)
        self.assertAlmostEqual(position.high_water_pct, 50.0)
        self.assertAlmostEqual(position.low_water_pct, 0.0)

    def test_delai_jusquau_pic(self):
        position = self._position()
        position = update_water_marks(position, price=1.3)
        delay = position.minutes_to_peak
        self.assertIsNotNone(delay)
        self.assertLess(delay, 1)

    def test_delai_absent_si_jamais_positif(self):
        self.assertIsNone(self._position().minutes_to_peak)

    def test_delai_jusquau_creux(self):
        # Le pendant de minutes_to_peak : sans lui, le rejeu des sorties ne
        # peut pas savoir si le stop a été touché avant ou après le TP.
        position = update_water_marks(self._position(), price=0.85)
        delay = position.minutes_to_trough
        self.assertIsNotNone(delay)
        self.assertLess(delay, 1)

    def test_delai_creux_absent_si_jamais_negatif(self):
        position = update_water_marks(self._position(), price=1.3)
        self.assertIsNone(position.minutes_to_trough)

    def test_la_date_du_creux_ne_remonte_pas(self):
        position = update_water_marks(self._position(), price=0.7)
        stamped = position.low_water_at
        position = update_water_marks(position, price=0.9)
        self.assertEqual(position.low_water_at, stamped)
        self.assertAlmostEqual(position.low_water_pct, -30.0)


class TestJournalInstrumente(unittest.TestCase):
    """Le journal doit porter de quoi recalibrer TP et SL."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))
        self.portfolio = PaperPortfolio(
            1000.0, self.journal,
            positions_path=os.path.join(self.tmp, "open.json"), cooldown_hours=0,
        )
        self.params = {
            "version": "2.0",
            "exit_rules": {"stop_loss_pct": -25, "stop_loss_slippage_buffer_pct": 4.6},
        }

    def test_pic_et_creux_journalises(self):
        position = self.portfolio.open(candidate(price=1.0, change_5m=12.5), self.params, 30.0)
        self.portfolio.update(position.id, 1.6)   # +60% : pic
        self.portfolio.update(position.id, 0.70)  # -30% : sortie

        row = self.journal.read_final_exits()[-1]
        self.assertAlmostEqual(row["peak_pct"], 60.0)
        self.assertIsNotNone(row["minutes_to_peak"])
        self.assertLess(row["trough_pct"], 0)
        self.assertIsNotNone(row["minutes_to_trough"])

    def test_contexte_de_prix_a_lentree_journalise(self):
        # Teste l'hypothèse « le score achète le haut de la bougie ».
        position = self.portfolio.open(candidate(price=1.0, change_5m=45.0), self.params, 30.0)
        self.portfolio.update(position.id, 0.70)
        row = self.journal.read_final_exits()[-1]
        self.assertEqual(row["price_change_5m_at_entry"], 45.0)

    def test_seuil_vise_et_declencheur_journalises(self):
        position = self.portfolio.open(candidate(price=1.0), self.params, 30.0)
        self.portfolio.update(position.id, 0.70)
        row = self.journal.read_final_exits()[-1]
        self.assertEqual(row["stop_loss_target_pct"], -25)
        self.assertAlmostEqual(row["stop_loss_trigger_pct"], -20.4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
