"""Bras de test consensus_af (2026-11-02) : consensus + 2 filtres."""
import json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.core.arm import bootstrap_arms  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(p):
    return json.load(open(os.path.join(ROOT, p)))


class TestConsensusAf(unittest.TestCase):
    def test_copie_complete_plus_deux_filtres(self):
        c, a = _load("config/arms/consensus.json"), _load("config/arms/consensus_af.json")
        self.assertEqual(a["filters"]["max_price_change_5m"], 10.0)
        self.assertEqual(a["entry_rules"]["max_exit_cost_pct_entry"], 3.0)
        self.assertTrue(a["entry_rules"]["consensus_gate"])
        for sec in ("filters", "entry_rules", "exit_rules", "scan", "risk_rules"):
            for k, v in c[sec].items():
                self.assertEqual(a[sec][k], v, f"{sec}.{k}")
        self.assertNotIn("consensus_gate", c["entry_rules"])
        self.assertNotIn("max_price_change_5m", c["filters"])
        self.assertEqual(a["mode"], "PAPER")

    def test_manifeste(self):
        arms = {x["name"]: x for x in _load("config/strategies.json")["arms"]}
        e, c = arms["consensus_af"], arms["consensus"]
        self.assertEqual((e["role"], e["capital_pct"], e["learning"], e["notify"]),
                         ("test", 0.0, False, "none"))
        self.assertEqual(e["min_confluence"], c["min_confluence"])
        for k, v in c["overrides"].items():
            self.assertEqual(e["overrides"][k], v)

    def test_ne_vote_pas_et_passe_apres_les_votants(self):
        arms = bootstrap_arms(with_journals=False)
        names = [a.name for a in arms]
        voters = {a.name for a in arms if a.is_voter}
        self.assertNotIn("consensus_af", voters)
        self.assertNotIn("fixes3", voters)
        self.assertNotIn("consensus", voters)
        i = names.index("consensus_af")
        self.assertTrue(all(names.index(v) < i for v in voters))
        self.assertEqual(names[-1], "consensus")


if __name__ == "__main__":
    unittest.main()
