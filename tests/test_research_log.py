"""Journal de recherche : observation seule, hors réseau.

Verrouille : un champ absent reste null, les refusés sont tirés au hasard,
les entrées sont toutes suivies, le fil a un plafond d'appels, et
l'enregistrement s'arrête si le disque est bas.
"""

import gzip
import json
import os
import random
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.models import Candidate, ScanResult  # noqa: E402
from src.core.research_log import (  # noqa: E402
    ResearchDisk,
    ResearchJournal,
    build_candidate_row,
    estimate_day,
    reason_code,
)
from src.pipeline import ArmEvaluation  # noqa: E402


def gmgn_candidate(**raw):
    base = {"address": "MINT", "symbol": "AAA", "name": "Aaa"}
    base.update(raw)
    return Candidate(
        token_address="MINT", symbol="AAA", name="Aaa", chain="solana",
        price_usd=0.0, liquidity_usd=0.0, buys_5m=0, sells_5m=0,
        age_hours=999.0, raw=base,
    )


def evaluation(kept=(), rejected=()):
    return ArmEvaluation(result=ScanResult(candidates=tuple(kept), rejected=tuple(rejected)))


class TestLigne(unittest.TestCase):
    def test_champ_absent_reste_null_meme_si_le_modele_met_zero(self):
        row = build_candidate_row(
            gmgn_candidate(), now=1_700_000_000, cycle_id=3, decisions=[],
        )
        self.assertIsNone(row["price"]["price_usd"])
        self.assertIsNone(row["price"]["price_change_5m_pct"])
        self.assertIsNone(row["price"]["price_change_15m_pct"])
        self.assertIsNone(row["flow"]["buys_5m"])
        self.assertIsNone(row["flow"]["unique_sellers_5m"])
        self.assertIsNone(row["lifecycle"]["age_minutes"])
        self.assertIsNone(row["lifecycle"]["bonding_curve_progress_pct"])
        self.assertIsNone(row["structure"]["rugcheck_score"])
        self.assertIsNone(row["cost"]["roundtrip_cost_pct"])
        self.assertIsNone(row["regime"]["sol_change_1h_pct"])
        self.assertEqual(row["price"]["schema_version"], 1)
        self.assertEqual(row["price"]["source"], "gmgn")

    def test_zero_reel_reste_zero(self):
        row = build_candidate_row(
            gmgn_candidate(price=1.5, buys_5m=0, price_change_percent5m=0),
            now=1_700_000_000, cycle_id=1, decisions=[],
        )
        self.assertEqual(row["price"]["price_usd"], 1.5)
        self.assertEqual(row["flow"]["buys_5m"], 0)
        self.assertEqual(row["price"]["price_change_5m_pct"], 0.0)

    def test_dex_lit_la_fenetre_5m_et_pas_une_autre(self):
        candidate = Candidate(
            token_address="MINT", symbol="AAA", name="Aaa", chain="solana",
            raw={"pair": {
                "pairAddress": "POOL",
                "dexId": "pumpswap",
                "priceUsd": "0.01",
                "liquidity": {"usd": 1000},
                "volume": {"m5": 50, "h1": 400},
                "priceChange": {"m5": -3.5},
                "txns": {"m5": {"buys": 4, "sells": 1}},
                "pairCreatedAt": 1_700_000_000_000 - 600_000,
            }},
        )
        row = build_candidate_row(candidate, now=1_700_000_000, cycle_id=2, decisions=[])
        self.assertEqual(row["pool_address"], "POOL")
        self.assertEqual(row["dex"], "pumpswap")
        self.assertEqual(row["price"]["source"], "dexscreener")
        self.assertEqual(row["flow"]["volume_5m_usd"], 50.0)
        self.assertEqual(row["flow"]["buys_5m"], 4)
        self.assertEqual(row["price"]["price_change_5m_pct"], -3.5)
        self.assertAlmostEqual(row["lifecycle"]["age_minutes"], 10.0)

    def test_bougies_deja_lues_remplissent_le_plus_haut_5m(self):
        now = 1_000_000
        candles = [
            {"t": now - 20 * 60, "h": 1, "l": 1, "c": 1, "v": 1},
            {"t": now - 15 * 60, "h": 2, "l": 1, "c": 2, "v": 1},
            {"t": now - 60, "h": 4, "l": 3, "c": 3, "v": 1},
        ]
        row = build_candidate_row(
            gmgn_candidate(price=3), now=now, cycle_id=1, decisions=[], candles=candles,
        )
        self.assertEqual(row["price"]["high_5m"], 4)
        self.assertAlmostEqual(row["price"]["drawdown_from_high_5m_pct"], 25.0)
        self.assertAlmostEqual(row["price"]["price_change_15m_pct"], 50.0)

    def test_code_de_raison_court(self):
        self.assertEqual(reason_code("liquidité 1000$ < 80000$"), "liquidity")
        self.assertIsNone(reason_code(""))


class TestCycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.calls = []

        def fetch(addresses):
            self.calls.append(list(addresses))
            return {addr: (1.0, 10.0) for addr in addresses}

        self.journal = ResearchJournal(
            self.tmp,
            {
                "enabled": True,
                "candidates_enabled": True,
                "price_track_enabled": True,
                "klines_enabled": True,
                "max_total_bytes": 10_000_000,
                "min_free_bytes": 0,
                "price_interval_seconds": 60,
                "price_horizon_seconds": 7200,
                "reject_sample_rate": 0.5,
                "max_calls_per_min": 1,
                "kline_lookback_seconds": 1800,
            },
            fetch=fetch,
            clock=lambda: 1_700_000_000,
            mono=lambda: 0,
            rng=random.Random(1),
            free_bytes=lambda: 9_000_000_000,
        )

    def test_une_ligne_par_jeton_et_tirage_des_refuses(self):
        kept = gmgn_candidate(price=2)
        kept = kept.with_fields(alpha_score_absolute=80)
        refused = Candidate(
            token_address="NOPE", symbol="NO", name="No", chain="solana",
            rejected_reason="liquidité 1$ < 10$",
            raw={"address": "NOPE", "price": 0.1},
        )
        self.journal.note_entered("MINT")
        n = self.journal.record_cycle(
            cycle_id=7,
            evaluations={"quality": evaluation([kept], [refused])},
            thresholds={"quality": 50},
            funnel_rows=[
                {
                    "arm": "quality", "token": "MINT", "gate": "economie",
                    "passed": True, "reason": "", "round_trip_pct": 2.5, "size_usd": 30,
                },
                {
                    "arm": "quality", "token": "MINT", "gate": "entree",
                    "passed": True, "reason": "",
                },
            ],
        )
        self.assertEqual(n, 2)
        folder = os.path.join(self.tmp, "data", "candidates")
        files = os.listdir(folder)
        rows = [json.loads(line) for line in open(os.path.join(folder, files[0]), encoding="utf-8")]
        by_token = {row["token_address"]: row for row in rows}
        self.assertEqual(len(rows), 2)
        entered = by_token["MINT"]
        self.assertTrue(entered["decisions"][0]["passed"])
        self.assertIsNone(entered["decisions"][0]["gate_failed"])
        self.assertIsNone(entered["price_track"]["sampled"])
        self.assertTrue(entered["price_track"]["tracked"])
        self.assertEqual(entered["price_track"]["reason"], "entered")
        self.assertEqual(entered["price_track"]["sample_rate"], 0.5)
        self.assertEqual(entered["cost"]["roundtrip_cost_pct"], 2.5)
        self.assertEqual(entered["cost"]["quote_size_usd"], 30)
        refused_row = by_token["NOPE"]
        self.assertFalse(refused_row["decisions"][0]["passed"])
        self.assertEqual(refused_row["decisions"][0]["gate_failed"], "filtres")
        self.assertEqual(refused_row["decisions"][0]["reason_code"], "liquidity")
        self.assertIn(refused_row["price_track"]["sampled"], (True, False))
        self.assertEqual(self.calls, [])

    def test_plafond_d_appels_et_priorite_aux_entrees(self):
        now = {"t": 1_700_000_000}

        def clock():
            return now["t"]

        self.journal.clock = clock
        tokens = []
        for i in range(40):
            token = f"T{i}"
            tokens.append(token)
            self.journal._register(token, now["t"] - 120, 1.0, 1.0, "entered" if i < 5 else "sample")
        now["t"] += 120
        calls = self.journal.tick()
        self.assertEqual(calls, 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.calls[0]), 30)
        self.assertTrue(all(token.startswith("T") and int(token[1:]) < 5 for token in self.calls[0][:5]))
        self.assertEqual(self.journal.tick(), 0)

    def test_desactive_n_ecrit_rien(self):
        quiet = ResearchJournal(
            self.tmp, {"enabled": False, "candidates_enabled": True,
                       "price_track_enabled": True, "klines_enabled": True,
                       "max_total_bytes": 1, "min_free_bytes": 0,
                       "price_interval_seconds": 60, "price_horizon_seconds": 60,
                       "reject_sample_rate": 1, "max_calls_per_min": 2,
                       "kline_lookback_seconds": 1800},
        )
        self.assertEqual(quiet.record_cycle(
            cycle_id=1, evaluations={}, thresholds={}, funnel_rows=[],
        ), 0)
        self.assertFalse(os.path.isdir(os.path.join(self.tmp, "data", "candidates")))

    def test_arret_si_disque_bas_et_gzip_du_jour_precedent(self):
        disk = ResearchDisk(
            os.path.join(self.tmp, "data"),
            max_total_bytes=10_000_000,
            min_free_bytes=5_000_000_000,
            free_bytes=lambda: 9_000_000_000,
            today=lambda: "20261115",
        )
        old = disk.path_for("candidates", "20261114")
        with open(old, "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        disk.maintain()
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(old + ".gz"))
        with gzip.open(old + ".gz", "rt", encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "{}")
        disk._free_bytes = lambda: 1_000_000_000
        self.assertFalse(disk.append("candidates", {"a": 1}))
        self.assertTrue(disk.halted)
        self.assertIn("espace libre", disk.halt_reason)

    def test_le_menage_ne_touche_pas_a_la_memoire_de_trading(self):
        disk = ResearchDisk(
            os.path.join(self.tmp, "data"),
            max_total_bytes=1000,
            min_free_bytes=0,
            free_bytes=lambda: 9_000_000_000,
            today=lambda: "20261115",
        )
        root = os.path.join(self.tmp, "data")
        os.makedirs(root, exist_ok=True)
        trades = os.path.join(root, "trades_log.jsonl.gz")
        with open(trades, "wb") as fh:
            fh.write(b"x" * 5000)
        stray = os.path.join(root, "20200101.jsonl")
        with open(stray, "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        old = disk.path_for("candidates", "20261114")
        with open(old, "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        disk.maintain()
        self.assertTrue(os.path.exists(trades))
        self.assertEqual(os.path.getsize(trades), 5000)
        self.assertTrue(os.path.exists(stray))
        self.assertFalse(os.path.exists(stray + ".gz"))
        self.assertTrue(os.path.exists(old + ".gz"))
        self.assertFalse(disk.halted)

    def test_une_entree_relance_les_deux_heures(self):
        now = 1_700_000_000
        self.journal._register("MINT", now, 1.0, 2.0, "sample")
        first = self.journal._tracks["MINT"]["until"]
        self.journal._register("MINT", now + 3000, 1.1, 2.0, "entered")
        track = self.journal._tracks["MINT"]
        self.assertEqual(track["reason"], "entered")
        self.assertEqual(track["since"], now + 3000)
        self.assertEqual(track["until"], now + 3000 + 7200)
        self.assertGreater(track["until"], first)
        self.journal._register("MINT", now + 4000, 1.2, 2.0, "entered")
        self.assertEqual(self.journal._tracks["MINT"]["until"], now + 3000 + 7200)

    def test_klines_deja_lues_bornees_a_30_min(self):
        now = 1_700_000_000
        self.journal.clock = lambda: now

        class Candle:
            def __init__(self, bucket, high):
                self.bucket = bucket
                self.high = high
                self.low = high
                self.close = high
                self.volume = 1

        self.journal.note_klines("MINT", [
            Candle(now - 40 * 60, 1),
            Candle(now - 10 * 60, 2),
        ], "gmgn")
        path = os.path.join(self.tmp, "data", "klines_pre")
        files = os.listdir(path)
        rows = [json.loads(line) for line in open(os.path.join(path, files[0]), encoding="utf-8")]
        self.assertEqual(rows[0]["source"], "gmgn")
        self.assertEqual([c["t"] for c in rows[0]["candles"]], [now - 10 * 60])


class TestEstimation(unittest.TestCase):
    def test_borne_haute_a_partir_d_une_ligne_reelle(self):
        row = build_candidate_row(
            gmgn_candidate(price=1, liquidity=1000, market_cap=5000),
            now=1_700_000_000, cycle_id=1,
            decisions=[{"arm": "quality", "passed": False, "gate_failed": "seuil_alpha", "reason_code": "alpha"}],
        )
        line = json.dumps(row).encode()
        packed = gzip.compress(line * 20)
        ratio = len(packed) / (len(line) * 20)
        # 45 s dans config/params.json → 1920 cycles/jour, plafond 25 enrichis.
        estimate = estimate_day(
            cycles_per_day=1920,
            tokens_per_cycle=25,
            line_bytes=len(line),
            gzip_ratio=ratio,
            price_samples_per_track=120,
            tracks_per_day=40,
            price_line_bytes=120,
        )
        self.assertEqual(estimate["candidate_lines"], 48_000)
        self.assertGreater(estimate["candidate_mb_gzip"], 0.5)
        self.assertLess(estimate["candidate_mb_gzip"], 40)


class TestDexSansAttente(unittest.TestCase):
    def test_limiteur_plein_ne_bloque_pas(self):
        from src.apis.dexscreener import DexScreenerAPI
        from src.core.ratelimit import RateLimiter

        dex = DexScreenerAPI()
        dex.rl_data = RateLimiter(1, 60.0)
        self.assertTrue(dex.rl_data.acquire(timeout=0))
        called = {"n": 0}

        def boom(*_a, **_k):
            called["n"] += 1
            raise AssertionError("ne doit pas partir")

        dex.session.get = boom
        self.assertIsNone(dex.try_tokens_snapshot(["MINT"]))
        self.assertEqual(called["n"], 0)


if __name__ == "__main__":
    unittest.main()
