"""Score v2 (proposition 2026-10-10) et bras de test `score_v2`.

Verrouille :
  - la formule (poids explicites, bornes, monotonie anti-pump) ;
  - l'opt-in : sans `entry_rules.score_v2_min_prob`, aucun bras n'est filtré ;
  - le bras `score_v2` du manifeste : rôle test, apprentissage figé, capital 0,
    même seuil alpha v1 et mêmes sorties que le témoin ;
  - dans `_try_entries`, un candidat refusé par le v2 n'atteint jamais
    `pre_trade_check` et l'entonnoir garde la raison.
"""

import json
import math
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import main as main_module  # noqa: E402
from src.core import score_v2  # noqa: E402
from src.core.arm import TEST, bootstrap_arms  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from tests.test_arms import ArmsTestCase  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cand(pc5m: float, ratio: float, alpha: float = 80.0, addr: str = "T") -> Candidate:
    return Candidate(
        token_address=addr, symbol=addr, name=addr, chain="solana",
        price_change_5m=pc5m, volume_liquidity_ratio=ratio,
    ).with_fields(alpha_score_absolute=alpha, alpha_score=alpha)


class TestFormule(unittest.TestCase):
    def test_point_neutre(self):
        # momentum 0, volume = liquidité -> logit = intercept
        p = score_v2.score_v2_probability(_cand(0.0, 1.0))
        self.assertAlmostEqual(p, 1 / (1 + math.exp(1.2065)), places=6)

    def test_anti_pump_monotone(self):
        probs = [score_v2.score_v2_probability(_cand(m, 1.0)) for m in (-20, 0, 20, 60)]
        self.assertEqual(probs, sorted(probs, reverse=True))
        probs = [score_v2.score_v2_probability(_cand(0, r)) for r in (0.5, 1, 5, 50)]
        self.assertEqual(probs, sorted(probs, reverse=True))

    def test_bornes(self):
        self.assertEqual(
            score_v2.score_v2_probability(_cand(100, 1.0)),
            score_v2.score_v2_probability(_cand(3000, 1.0)),
        )
        self.assertEqual(
            score_v2.score_v2_probability(_cand(0, 0.0)),
            score_v2.score_v2_probability(_cand(0, 0.001)),
        )

    def test_seuil(self):
        ok, prob, why = score_v2.score_v2_gate(_cand(0, 1.0), 0.205)
        self.assertTrue(ok)
        self.assertEqual(why, "")
        ok, prob, why = score_v2.score_v2_gate(_cand(80, 20.0), 0.205)
        self.assertFalse(ok)
        self.assertIn("score_v2", why)

    def test_desactive_a_zero(self):
        self.assertTrue(score_v2.score_v2_gate(_cand(100, 100.0), 0)[0])
        self.assertTrue(score_v2.score_v2_gate(_cand(100, 100.0), None)[0])


class TestManifesteProduction(unittest.TestCase):
    def test_bras_score_v2(self):
        with open(os.path.join(ROOT, "config", "strategies.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        arms = {a["name"]: a for a in doc["arms"]}
        entry = arms["score_v2"]
        self.assertEqual(entry["role"], "test")
        self.assertIs(entry["learning"], False)
        self.assertEqual(entry["capital_pct"], 0.0)
        self.assertEqual(entry["overrides"], {"entry_rules.score_v2_min_prob": 0.205})
        total = sum(float(a.get("capital_pct", 0)) for a in doc["arms"] if a.get("enabled", True))
        self.assertAlmostEqual(total, 1.0, places=6)
        # Aucun autre bras n'active le filtre.
        for name, a in arms.items():
            if name != "score_v2":
                self.assertNotIn("entry_rules.score_v2_min_prob", a.get("overrides", {}))


class TestBootstrap(ArmsTestCase):
    def test_fork_du_temoin(self):
        self._manifest([
            {"name": "baseline", "role": "voter", "capital_pct": 1.0, "notify": "all"},
            {"name": "score_v2", "role": "test", "capital_pct": 0.0, "learning": False,
             "overrides": {"entry_rules.score_v2_min_prob": 0.205}},
        ])
        arms = {a.name: a for a in bootstrap_arms(with_journals=False)}
        v2, base = arms["score_v2"], arms["baseline"]
        self.assertEqual(v2.role, TEST)
        self.assertIsNone(v2.learning)
        self.assertEqual(v2.params.get("entry_rules.score_v2_min_prob"), 0.205)
        self.assertEqual(base.params.get("entry_rules.score_v2_min_prob", 0), 0)
        self.assertEqual(v2.entry_threshold, base.entry_threshold)
        self.assertEqual(v2.params.get("exit_rules"), base.params.get("exit_rules"))


class TestTryEntries(unittest.TestCase):
    def _run(self, min_prob, candidates):
        recorded = []
        funnel = SimpleNamespace(record=lambda *a, **k: recorded.append(a))
        params = {"entry_rules.score_v2_min_prob": min_prob}
        arm = SimpleNamespace(
            name="score_v2", role=TEST, entry_threshold=55, max_positions=3,
            params=SimpleNamespace(get=lambda k, d=None: params.get(k, d)),
            portfolio=SimpleNamespace(capital=7000.0), label="[score_v2]",
        )
        fake = SimpleNamespace(
            funnel=funnel, mode="PAPER", learning=None, params={},
            max_sector_positions=3, max_sector_exposure_pct=50,
            _fleet_exposure=lambda: {}, _last_evaluations={}, arms=[], voters=[],
        )
        evaluation = SimpleNamespace(result=SimpleNamespace(candidates=candidates))
        with mock.patch.object(main_module, "pre_trade_check", return_value="STOP test") as ptc, \
                mock.patch.object(main_module, "is_soft_reject", return_value=False):
            main_module.AlphaLoop._try_entries(fake, evaluation, arm, {})
        return recorded, ptc

    def test_pump_refuse_avant_pre_trade(self):
        recorded, ptc = self._run(0.205, [_cand(90, 30.0, addr="PUMP")])
        ptc.assert_not_called()
        gates = [(r[1], r[3], r[4]) for r in recorded]
        self.assertEqual(gates[0][:2], ("PUMP", "score_v2"))
        self.assertFalse(gates[0][2])

    def test_pump_refuse_puis_suivant_evalue(self):
        recorded, ptc = self._run(0.205, [_cand(90, 30.0, 90, "PUMP"), _cand(0, 1.0, 80, "CALME")])
        self.assertEqual(ptc.call_count, 1)
        self.assertEqual(ptc.call_args.kwargs["candidate"].token_address, "CALME")

    def test_desactive_ne_change_rien(self):
        recorded, ptc = self._run(0, [_cand(90, 30.0, addr="PUMP")])
        ptc.assert_called_once()
        self.assertNotIn("score_v2", [r[3] for r in recorded])


if __name__ == "__main__":
    unittest.main()
