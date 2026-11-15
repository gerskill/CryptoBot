"""Observation GMGN hors boucle : pas de réseau, pas de décision, pas d'écriture ailleurs."""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.gmgn_observe import (  # noqa: E402
    INCIDENT_COOLDOWN_UNTIL,
    GmgnObserver,
    build_argv,
    child_env,
    incident_cooldown_epoch,
    parse_security,
    valid_mint,
)

MINT_A = "So11111111111111111111111111111111111111112"
MINT_B = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
# Avant la levée annoncée (17:27:31 Zurich le 8 novembre 2026).
PENDANT_BAN = incident_cooldown_epoch() - 60
APRES_BAN = incident_cooldown_epoch() + 120


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class Runner:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def __call__(self, argv):
        self.calls.append(list(argv))
        if not self.responses:
            return {"returncode": 0, "stdout": "{}", "stderr": ""}
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def security_ok():
    return {
        "returncode": 0,
        "stdout": json.dumps({"rug_ratio": 0.2, "renounced_mint": True, "top_10_holder_rate": 0.4}),
        "stderr": "",
    }


def holders_ok():
    return {
        "returncode": 0,
        "stdout": json.dumps({"list": [
            {"amount_percentage": 0.2, "tags": ["bundler"]},
            {"amount_percentage": 0.1, "tags": ["rat_trader"]},
        ]}),
        "stderr": "",
    }


def traders_ok():
    return {
        "returncode": 0,
        "stdout": json.dumps({"list": [{"tags": ["sniper"]}]}),
        "stderr": "",
    }


def state_doc(*mints, rejected=False, as_position=False):
    rows = [{"token_address": mint, "symbol": "T", "rejected_reason": "non" if rejected else None} for mint in mints]
    positions = [{"token_address": mint, "arm": "sniper"} for mint in mints] if as_position else []
    return {"mode": "PAPER", "candidates": [] if as_position else rows, "positions": positions}


class TestObservationGmgn(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.clock = Clock(APRES_BAN)
        self.runner = Runner([security_ok(), holders_ok(), traders_ok()])
        self.obs = GmgnObserver(self.tmp, clock=self.clock, runner=self.runner, executable="gmgn-cli")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_demarrage_ignore_les_tokens_deja_la_et_ne_promet_pas_lavant_achat(self):
        self.obs.poll(state_doc(MINT_A))
        self.assertEqual(self.runner.calls, [])
        self.obs.poll(state_doc(MINT_A, MINT_B))
        self.assertEqual(len(self.runner.calls), 3)
        self.assertIn(MINT_B, self.obs.cache)
        self.assertNotIn(MINT_A, self.obs.cache)
        row = self.obs.cache[MINT_B]
        self.assertFalse(row["pretrade_guaranteed"])
        self.assertIsNone(row["decision"])
        self.assertFalse(row["affects_trading"])
        self.assertIn("pas une preuve", row["timing_note"])

    def test_position_seule_nest_pas_une_analyse_avant_achat(self):
        self.obs.poll({"mode": "PAPER", "candidates": [], "positions": []})
        self.obs.poll(state_doc(MINT_B, as_position=True))
        row = self.obs.cache[MINT_B]
        self.assertEqual(row["seen_as"], "position")
        self.assertIn("entrée a déjà eu lieu", row["timing_note"])
        self.assertFalse(row["pretrade_guaranteed"])

    def test_dedoublonnage_et_cache_ne_rappellent_pas(self):
        self.obs.poll({"mode": "PAPER", "candidates": [], "positions": []})
        self.obs.poll(state_doc(MINT_B))
        self.obs.poll(state_doc(MINT_B))
        self.assertEqual(len(self.runner.calls), 3)
        joined = " ".join(" ".join(call) for call in self.runner.calls)
        self.assertNotIn("swap", joined)
        self.assertNotIn("order", joined)
        self.assertNotIn("cooking", joined)

    def test_cooldown_initial_ne_fait_aucun_appel(self):
        clock = Clock(PENDANT_BAN)
        runner = Runner([security_ok()])
        obs = GmgnObserver(self.tmp + "b", clock=clock, runner=runner, executable="gmgn-cli")
        obs.poll({"mode": "PAPER", "candidates": [], "positions": []})
        heart = obs.poll(state_doc(MINT_B))
        self.assertEqual(runner.calls, [])
        self.assertEqual(heart["status"], "cooldown")
        self.assertLess(clock.now, incident_cooldown_epoch())
        self.assertTrue(INCIDENT_COOLDOWN_UNTIL.startswith("2026-11-08T17:27:31"))

    def test_premier_429_arrete_sans_second_appel(self):
        self.obs.poll({"mode": "PAPER", "candidates": [], "positions": []})
        self.runner.responses = [{
            "returncode": 1,
            "stdout": json.dumps({"code": 429, "error": "RATE_LIMIT_BANNED", "reset_at": 1762620000}),
            "stderr": "RATE_LIMIT_BANNED",
        }, security_ok()]
        heart = self.obs.poll(state_doc(MINT_B))
        self.assertEqual(len(self.runner.calls), 1)
        self.assertEqual(heart["status"], "cooldown")
        self.assertGreaterEqual(self.obs.cooldown_until, 1762620000)
        self.obs.poll(state_doc(MINT_B))
        self.assertEqual(len(self.runner.calls), 1)

    def test_json_invalide_et_mint_malforme_ne_partent_pas_en_rafale(self):
        self.obs.poll({"mode": "PAPER", "candidates": [], "positions": []})
        self.runner.responses = [{"returncode": 0, "stdout": "{", "stderr": ""}]
        self.obs.poll({
            "mode": "PAPER",
            "candidates": [
                {"token_address": "pas un mint $(rm)"},
                {"token_address": MINT_B},
            ],
            "positions": [],
        })
        self.assertEqual(len(self.runner.calls), 1)
        self.assertIsNone(self.obs.cache[MINT_B]["security"])
        self.assertEqual(self.obs.cache[MINT_B]["status"], "unknown")

    def test_timeout_compte_un_appel_et_narrête_la_serie(self):
        self.obs.poll({"mode": "PAPER", "candidates": [], "positions": []})
        self.runner.responses = [TimeoutError("gmgn-cli timeout"), security_ok()]
        self.obs.poll(state_doc(MINT_B))
        self.assertEqual(len(self.runner.calls), 1)
        self.assertEqual(self.obs.last_error, "timeout")

    def test_budget_survit_au_redemarrage_et_ne_double_pas(self):
        self.obs.poll({"mode": "PAPER", "candidates": [], "positions": []})
        self.obs.poll(state_doc(MINT_B))
        calls = self.obs.calls
        again = GmgnObserver(self.tmp, clock=self.clock, runner=self.runner, executable="gmgn-cli")
        again.poll(state_doc(MINT_B))
        self.assertEqual(again.calls, calls)
        self.assertEqual(len(self.runner.calls), 3)
        self.clock.now += 10
        self.runner.responses = [security_ok(), holders_ok(), traders_ok()]
        again.poll(state_doc(MINT_B, "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"))
        # Budget horaire déjà plein : pas un second mint.
        self.assertEqual(len(self.runner.calls), 3)

    def test_zero_ecriture_hors_dossier_observation_et_zero_decision(self):
        arm = os.path.join(self.tmp, "data", "arms", "sniper")
        os.makedirs(arm)
        watched = {
            os.path.join(self.tmp, "data", "state.json"): b'{"mode":"PAPER","candidates":[],"positions":[]}',
            os.path.join(arm, "trades_log.jsonl"): b"",
            os.path.join(self.tmp, "config", "params.json"): b"{}",
        }
        os.makedirs(os.path.join(self.tmp, "config"))
        for path, content in watched.items():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(content)
        digest = {path: hashlib.sha256(open(path, "rb").read()).hexdigest() for path in watched}
        self.obs.poll({"mode": "PAPER", "candidates": [], "positions": []})
        for path, sha in digest.items():
            with open(path, "rb") as handle:
                self.assertEqual(hashlib.sha256(handle.read()).hexdigest(), sha, path)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "data", "gmgn_observe", "heartbeat.json")))
        heart = json.load(open(os.path.join(self.tmp, "data", "gmgn_observe", "heartbeat.json"), encoding="utf-8"))
        self.assertFalse(heart["affects_trading"])
        self.assertIn(heart["status"], ("observation", "cooldown", "erreur"))

    def test_mode_live_ne_sonde_pas(self):
        self.obs.poll({"mode": "LIVE", "candidates": [{"token_address": MINT_B}], "positions": []})
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.obs.status, "erreur")

    def test_champ_absent_nest_pas_zero_et_argv_sans_shell(self):
        parsed = parse_security({"rug_ratio": 0, "renounced_mint": False})
        self.assertEqual(parsed["rug_ratio"], 0.0)
        self.assertTrue(parsed["fields_known"]["rug_ratio"])
        missing = parse_security({})
        self.assertIsNone(missing["rug_ratio"])
        self.assertFalse(missing["fields_known"]["rug_ratio"])
        self.assertIsNone(valid_mint("$(rm -rf)"))
        argv = build_argv("gmgn-cli", "security", MINT_A)
        self.assertEqual(argv[0], "gmgn-cli")
        self.assertNotIn(";", " ".join(argv))
        with self.assertRaises(ValueError):
            build_argv("gmgn-cli", "swap", MINT_A)
        env = child_env({"PATH": "/usr/bin", "HOME": "/tmp", "GMGN_API_KEY": "secret", "GMGN_PRIVATE_KEY": "secret"})
        self.assertNotIn("GMGN_API_KEY", env)
        self.assertNotIn("GMGN_PRIVATE_KEY", env)


if __name__ == "__main__":
    unittest.main()
