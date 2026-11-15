"""Surveillance des positions indépendante du scan. Hors réseau.

Le fil ne vend pas : il cadence le tick. Les sorties restent celles du
portefeuille. Un prix manquant n'est pas remplacé par le dernier prix connu.
"""

import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.technical import PriceHistory, Snapshot  # noqa: E402
from src.core.journal import TradeJournal  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.core.portfolio import PREPARED_UNSET, PaperPortfolio  # noqa: E402
from src.core.position_watch import (  # noqa: E402
    PositionWatch,
    fetch_unique,
    quote_is_usable,
    sample_gap_line,
)
from src.core.pricefeed import effective_monitor_interval  # noqa: E402


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


def candidate(token="mint1", symbol="TEST"):
    return Candidate(
        token_address=token, symbol=symbol, name=symbol, chain="solana",
        price_usd=1.0, liquidity_usd=30000, volume_1h=20000,
    )


class FakeCost:
    def __init__(self, total):
        self.total_cost_pct = total
        self.partial = False


class TestQuote(unittest.TestCase):
    def test_prix_inutilisable(self):
        for price in (None, float("nan"), float("inf"), 0, -1, "x", False):
            self.assertFalse(quote_is_usable(price), price)
        self.assertTrue(quote_is_usable(0.001))

    def test_une_paire_partagee_ne_coute_qu_une_lecture(self):
        calls = []

        def fetch(item):
            calls.append(item["pair"])
            return item["pair"]

        items = [
            {"pair": "PAIR", "arm": "a"},
            {"pair": "PAIR", "arm": "b"},
            {"pair": "AUTRE", "arm": "a"},
        ]
        found = fetch_unique(items, lambda item: item["pair"], fetch)
        self.assertEqual(calls, ["PAIR", "AUTRE"])
        self.assertEqual(found["PAIR"], "PAIR")

    def test_ecart_entre_deux_echantillons(self):
        snaps = [
            Snapshot(ts=1000, price=1.0, volume_5m=0, liquidity=100, source="monitor"),
            Snapshot(ts=1005, price=0.002, volume_5m=0, liquidity=3, source="monitor"),
        ]
        line = sample_gap_line(snaps)
        self.assertIn("écart 5s", line)
        self.assertIn("prix x0.002", line)
        self.assertIn("liq x0.030", line)
        self.assertIsNone(sample_gap_line(snaps[:1]))

    def test_prix_manquant_nechange_pas_lhistorique(self):
        history = PriceHistory()
        history.record_raw("mint", price=1.0, liquidity=100, ts=1000, source="monitor")
        history.record_raw("mint", price=None, liquidity=None, ts=1005, source="monitor")
        history.record_raw("mint", price=0, liquidity=0, ts=1006, source="monitor")
        self.assertEqual(len(history.snapshots("mint")), 1)


class TestWatchThread(unittest.TestCase):
    def test_ticks_pendant_un_scan_long(self):
        ticks = []

        def tick():
            ticks.append(time.monotonic())

        watch = PositionWatch(tick, lambda: 0.05)
        watch.start()
        try:
            time.sleep(0.32)  # le « scan » bloque CE fil, pas la surveillance
        finally:
            watch.stop()
        self.assertGreaterEqual(len(ticks), 3)
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        self.assertTrue(all(gap >= 0.04 for gap in gaps))

    def test_pas_de_deuxieme_tick_en_parallele(self):
        state = {"inside": 0, "max": 0}

        def tick():
            state["inside"] += 1
            state["max"] = max(state["max"], state["inside"])
            time.sleep(0.04)
            state["inside"] -= 1

        watch = PositionWatch(tick, lambda: 0.02)
        watch.start()
        try:
            time.sleep(0.25)
        finally:
            watch.stop()
        self.assertEqual(state["max"], 1)
        self.assertGreaterEqual(watch.ticks, 2)

    def test_erreur_espacee_pas_de_boucle_serree(self):
        def tick():
            raise RuntimeError("429")

        watch = PositionWatch(tick, lambda: 0.05)
        watch.start()
        try:
            time.sleep(0.22)
        finally:
            watch.stop()
        self.assertGreaterEqual(watch.errors, 1)
        self.assertLess(watch.errors, 15)
        self.assertEqual(watch.ticks, 0)

    def test_stop_arrete_le_fil(self):
        watch = PositionWatch(lambda: None, lambda: 1)
        watch.start()
        self.assertTrue(watch.alive)
        watch.stop(timeout=1)
        self.assertFalse(watch.alive)

    def test_budget_allonge_le_tick(self):
        # 20 paires * (60/5) = 240 > 180 : le tick passe au-dessus de 5 s.
        stretched = effective_monitor_interval(5, 20)
        self.assertGreater(stretched, 5)
        # 8 paires * 12 = 96 < 180 : la cadence nominale tient.
        self.assertEqual(effective_monitor_interval(5, 8), 5)


class TestSortiesInchangees(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._n = 0

    def _portfolio(self, measurer=None):
        self._n += 1
        journal = TradeJournal(os.path.join(self.tmp, f"t-{self._n}.jsonl"))
        return PaperPortfolio(capital=1000.0, journal=journal, exit_fee_measurer=measurer)

    def test_meme_sequence_de_prix_memes_sorties(self):
        direct = self._portfolio()
        prepared = self._portfolio()
        prices = [1.1, 0.7]
        left = direct.open(candidate("a", "AAA"), PARAMS, 100.0)
        right = prepared.open(candidate("a", "AAA"), PARAMS, 100.0)
        for price in prices:
            rows_left = direct.update(left.id, price)
            cost = prepared.quote_final_cost(right, price)
            rows_right = prepared.update(
                right.id, price, prepared_final_cost=cost if cost is not None else PREPARED_UNSET
            )
            self.assertEqual(
                [row["exit_reason"] for row in rows_left],
                [row["exit_reason"] for row in rows_right],
            )
            if rows_left:
                self.assertAlmostEqual(rows_left[0]["pnl_pct"], rows_right[0]["pnl_pct"])

    def test_devis_prepare_nappelle_pas_le_reseau_une_seconde_fois(self):
        calls = []

        def measurer(token, size):
            calls.append(size)
            return FakeCost(4.0)

        portfolio = self._portfolio(measurer)
        position = portfolio.open(candidate(), PARAMS, 100.0)
        cost = portfolio.quote_final_cost(position, 0.6)
        rows = portfolio.update(position.id, 0.6, prepared_final_cost=cost)
        self.assertEqual(len(calls), 1)
        self.assertEqual(rows[0]["exit_reason"].split()[0] if False else rows[0]["exit_cost_pct"], 4.0)

    def test_prix_absent_ne_ferme_pas(self):
        portfolio = self._portfolio()
        position = portfolio.open(candidate(), PARAMS, 100.0)
        self.assertEqual(portfolio.update(position.id, 0), [])
        self.assertIn(position.id, portfolio.positions)

    def test_entree_et_sortie_sous_le_meme_verrou_une_seule_ligne(self):
        portfolio = self._portfolio()
        position = portfolio.open(candidate("a", "AAA"), PARAMS, 100.0)
        lock = threading.RLock()
        errors = []

        def close():
            try:
                with lock:
                    portfolio.update(position.id, 0.5)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def enter():
            try:
                with lock:
                    portfolio.open(candidate("b", "BBB"), PARAMS, 30.0)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=close), threading.Thread(target=enter)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
        self.assertEqual(errors, [])
        self.assertEqual(len(portfolio.journal.read_final_exits()), 1)
        self.assertTrue(any(p.token_address == "b" for p in portfolio.positions.values()))
        self.assertNotIn(position.id, portfolio.positions)


if __name__ == "__main__":
    unittest.main()
