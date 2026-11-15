"""2026-10-24 : social_score réparé (liens déclarés, observation seule) + bras tp1_20_vl2."""
import json, os, unittest
from types import SimpleNamespace

from src.agents.onchain_observer import candidate_social_score, social_links, social_links_score

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cand(info=None, profile=None, boosts=None):
    pair = {"info": info or {}}
    if boosts is not None:
        pair["boosts"] = {"active": boosts}
    raw = {"pair": pair}
    if profile is not None:
        raw["profile"] = profile
    return SimpleNamespace(raw=raw, social_mentions_1h=None)


class TestSocialScore(unittest.TestCase):
    def test_complet(self):
        c = cand({"websites": [{"url": "https://a.io"}],
                  "socials": [{"type": "twitter", "url": "https://x.com/abc"},
                              {"type": "telegram", "url": "https://t.me/abc"}]},
                 profile={"links": []}, boosts=10)
        self.assertEqual(candidate_social_score(c), 30 + 30 + 15 + 10 + 5)

    def test_sans_liens_vaut_zero_pas_none(self):
        self.assertEqual(candidate_social_score(cand()), 0)

    def test_sans_source_none(self):
        self.assertIsNone(candidate_social_score(SimpleNamespace(raw={})))
        self.assertIsNone(social_links_score({}))

    def test_borne_100(self):
        links = social_links(cand({"websites": [{"url": "https://a.io"}],
            "socials": [{"type": "twitter", "url": "https://x.com/a"}, {"type": "telegram", "url": "t"},
                        {"type": "discord", "url": "d"}]}, profile={"links": []}, boosts=5))
        self.assertLessEqual(social_links_score(links), 100)

    def test_communaute_x_moins_forte(self):
        a = candidate_social_score(cand({"socials": [{"type": "twitter", "url": "https://x.com/abc"}]}))
        b = candidate_social_score(cand({"socials": [{"type": "twitter", "url": "https://x.com/i/communities/123"}]}))
        self.assertGreater(a, b)

    def test_observation_seule_learning_lit_les_mentions(self):
        """Le score de liens ne doit PAS nourrir le filtre min_social_mentions_1h
        ni l'ajustement du poids social : learning lit entry_social_mentions_1h."""
        src = open(os.path.join(ROOT, "src/core/learning.py"), encoding="utf-8").read()
        self.assertNotIn('row.get("social_score")', src)
        self.assertNotIn('segment_stats(rows, "social_score"', src)


class TestBrasVl2(unittest.TestCase):
    def test_manifeste_et_params(self):
        doc = json.load(open(os.path.join(ROOT, "config/strategies.json")))
        arms = {a["name"]: a for a in doc["arms"]}
        self.assertNotIn("narrative", arms)
        e = arms["tp1_20_vl2"]
        self.assertEqual(e["overrides"]["entry_rules.max_volume_liquidity_ratio"], 2)
        self.assertEqual(e["capital_pct"], 0.0)
        self.assertIs(e["learning"], False)
        base = json.load(open(os.path.join(ROOT, "config/arms/tp1_20.json")))
        vl2 = json.load(open(os.path.join(ROOT, "config/arms/tp1_20_vl2.json")))
        self.assertEqual(base["entry_rules"]["max_volume_liquidity_ratio"], 5)
        self.assertEqual(vl2["entry_rules"].pop("max_volume_liquidity_ratio"), 2)
        base["entry_rules"].pop("max_volume_liquidity_ratio")
        self.assertEqual(base, vl2)  # seule difference


if __name__ == "__main__":
    unittest.main()
