"""Faux RUG_PULL « liquidité -100 % » — proposals/rugpull_fix.

Mesuré le 2026-10-11 : 229 RUG_PULL sur 290 sortaient à un prix proche de
l'entrée (médiane +0,5 %), dont 225 sur des positions dont le monitoring n'a
JAMAIS lu de liquidité (depth_usd = 0). Cause : liquidité INCONNUE (repli
Jupiter sans pair_address — candidats GMGN —, ou paire pump.fun sans champ
liquidité) convertie en 0 par `liquidity or 0`, puis comparée à la liquidité
du scan → -100 % → sortie immédiate.
"""

import os
import sys
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents.microstructure_agent import MicrostructureAgent  # noqa: E402
from src.analysis.technical import PriceHistory, Snapshot  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.core.positions import Position, evaluate_exits  # noqa: E402
from src.main import (  # noqa: E402
    AlphaLoop, RUG_CONFIRM_READINGS, RUG_PRICE_CRASH_PCT, RUG_WINDOW_SECONDS,
)

MINT = "DzMwArmdzPqcqgvyqAmTxy79XdA9NwK9mMteqWLJpump"


def drop(history, **kwargs):
    params = dict(window_seconds=RUG_WINDOW_SECONDS, source="monitor",
                  confirm_readings=RUG_CONFIRM_READINGS,
                  price_crash_pct=RUG_PRICE_CRASH_PCT)
    params.update(kwargs)
    return history.liquidity_drop_pct(MINT, **params)


def position(entry=0.00089059):
    return Position(token_address=MINT, symbol="Normie", chain="solana",
                    entry_price=entry, size_usd=20.0)


def is_rug(actions):
    return any(a.reason.startswith("RUG_PULL") for a in actions)


class TestCasNormie(unittest.TestCase):
    """Reproduction du cas réel du 11/10 16:57 UTC : RUG_PULL à +11,8 %."""

    def test_liquidite_inconnue_ne_declenche_pas_de_rug(self):
        h = PriceHistory()
        now = time.time()
        # Scan : candidat GMGN, liquidité 105 650 $, pas de pair_address.
        h.record(Candidate(token_address=MINT, symbol="Normie", name="",
                           chain="solana", price_usd=0.00089059,
                           liquidity_usd=105650.0), ts=now - 60)
        # Monitoring : prix Jupiter, liquidité None (inconnue).
        for i, p in enumerate([0.00095, 0.00101, 0.00103126]):
            h.record_raw(MINT, price=p, liquidity=None, ts=now - 30 + 10 * i)
        d = drop(h)
        self.assertIsNone(d)
        self.assertFalse(is_rug(evaluate_exits(position(), 0.00103126, d)))

    def test_ancien_comportement_reproduit_le_faux_rug(self):
        """Témoin : avec `liquidity or 0` et sans filtre, -100 % → RUG_PULL."""
        h = PriceHistory()
        now = time.time()
        h.record_raw(MINT, price=0.00089059, liquidity=105650.0, ts=now - 60, source="scan")
        h.record_raw(MINT, price=0.00103126, liquidity=0, ts=now)
        d = h.liquidity_drop_pct(MINT, RUG_WINDOW_SECONDS)  # appel d'origine
        self.assertEqual(d, -100.0)
        self.assertTrue(is_rug(evaluate_exits(position(), 0.00103126, d)))


class TestConfirmation(unittest.TestCase):
    def test_une_seule_lecture_a_zero_ne_suffit_pas(self):
        h = PriceHistory()
        now = time.time()
        h.record_raw(MINT, price=1.0, liquidity=100000, ts=now - 20)
        h.record_raw(MINT, price=1.0, liquidity=100000, ts=now - 10)
        h.record_raw(MINT, price=1.01, liquidity=0, ts=now)
        self.assertGreater(drop(h), -50)  # la moins sévère des 2 dernières

    def test_lecture_isolee_puis_retour_a_la_normale(self):
        h = PriceHistory()
        now = time.time()
        for i, liq in enumerate([100000, 100000, 0, 99000]):
            h.record_raw(MINT, price=1.0, liquidity=liq, ts=now - 30 + 10 * i)
        self.assertGreater(drop(h), -50)

    def test_deux_lectures_consecutives_confirment_le_rug(self):
        h = PriceHistory()
        now = time.time()
        for i, liq in enumerate([100000, 100000, 5000, 3000]):
            h.record_raw(MINT, price=0.9, liquidity=liq, ts=now - 30 + 10 * i)
        d = drop(h)
        self.assertLessEqual(d, -50)
        self.assertTrue(is_rug(evaluate_exits(position(entry=1.0), 0.9, d)))

    def test_prix_effondre_une_lecture_suffit(self):
        """Un vrai rug n'attend pas la 2e lecture si le prix s'écroule aussi."""
        h = PriceHistory()
        now = time.time()
        h.record_raw(MINT, price=1.0, liquidity=100000, ts=now - 10)
        h.record_raw(MINT, price=0.3, liquidity=2000, ts=now)
        d = drop(h)
        self.assertLessEqual(d, -50)
        self.assertTrue(is_rug(evaluate_exits(position(entry=1.0), 0.3, d)))

    def test_pas_assez_de_lectures_rend_none(self):
        h = PriceHistory()
        now = time.time()
        h.record_raw(MINT, price=1.0, liquidity=100000, ts=now - 5)
        h.record_raw(MINT, price=1.0, liquidity=1000, ts=now)
        self.assertIsNone(drop(h))

    def test_liquidite_mesuree_zero_confirmee_reste_un_rug(self):
        """0 MESURÉ (paire vidée) ≠ None : deux lectures à 0 → -100 %."""
        h = PriceHistory()
        now = time.time()
        for i, liq in enumerate([80000, 0, 0]):
            h.record_raw(MINT, price=1.0, liquidity=liq, ts=now - 20 + 10 * i)
        self.assertEqual(drop(h), -100.0)


class TestSources(unittest.TestCase):
    def test_meilleure_paire_du_scan_vs_paire_suivie(self):
        """Scan = max des paires (200 k), monitoring = paire suivie (80 k)."""
        h = PriceHistory()
        now = time.time()
        h.record_raw(MINT, price=1.0, liquidity=200000, ts=now - 50, source="scan")
        for i in range(4):
            h.record_raw(MINT, price=1.0, liquidity=80000, ts=now - 40 + 10 * i)
        self.assertEqual(drop(h), 0.0)
        # Sans filtre de source, la même série simulait -60 %.
        self.assertLess(h.liquidity_drop_pct(MINT, 120, confirm_readings=2), -50)

    def test_scan_sans_liquidite_nest_pas_zero(self):
        h = PriceHistory()
        h.record(Candidate(token_address=MINT, symbol="X", name="", chain="solana",
                           price_usd=1.0, liquidity_usd=0.0))
        self.assertIsNone(h.snapshots(MINT)[0].liquidity)


class FakeDex:
    def __init__(self, pair):
        self.pair = pair

    def get_pair_data(self, chain, pair_address):
        return {"pairs": [self.pair]} if self.pair else {}


class TestCurrentMarket(unittest.TestCase):
    def _market(self, pair, pair_address="PAIR", jupiter_price=1.23):
        fake = SimpleNamespace(
            dex=FakeDex(pair),
            jupiter=SimpleNamespace(price=lambda mint: jupiter_price),
            birdeye=SimpleNamespace(get_price=lambda *a, **k: None),
            _mark_pair_stale=lambda token: None,
        )
        pos = SimpleNamespace(chain="solana", pair_address=pair_address,
                              token_address=MINT)
        return AlphaLoop._current_market(fake, pos)

    def test_paire_pumpfun_sans_champ_liquidite_rend_none(self):
        self.assertEqual(self._market({"dexId": "pumpfun", "priceUsd": "0.001"}),
                         (0.001, None))

    def test_liquidite_mesuree_conservee(self):
        self.assertEqual(
            self._market({"priceUsd": "0.001", "liquidity": {"usd": 104000}}),
            (0.001, 104000.0),
        )

    def test_liquidite_zero_explicite_reste_zero(self):
        self.assertEqual(
            self._market({"priceUsd": "0.001", "liquidity": {"usd": 0}}), (0.001, 0.0)
        )

    def test_sans_pair_address_repli_jupiter_liquidite_inconnue(self):
        self.assertEqual(self._market(None, pair_address=None), (1.23, None))


class TestMicrostructure(unittest.TestCase):
    def test_liquidite_inconnue_pas_de_derive_de_moins_100(self):
        snaps = [Snapshot(ts=1000.0 + 10 * i, price=1.0 + 0.01 * i, volume_5m=0.0,
                          liquidity=(105650.0 if i == 0 else None))
                 for i in range(6)]
        lecture = MicrostructureAgent().read(snaps)
        self.assertIsNone(lecture.liquidity_drift_pct)
        self.assertEqual(lecture.depth_usd, 105650.0)
        self.assertIsNotNone(lecture.price_drift_pct)


if __name__ == "__main__":
    unittest.main()
