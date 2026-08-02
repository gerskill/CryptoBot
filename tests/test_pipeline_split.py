"""Découpage collecte / évaluation — ancre de non-régression.

`collect()` paie les appels API une fois ; `evaluate()` juge sans I/O ni effet
de bord, autant de fois qu'il y a de stratégies. Ce fichier verrouille les
deux propriétés qui rendent le multi-stratégie sûr :

  1. l'enveloppe de découverte couvre toutes les stratégies — et prend le
     MAXIMUM pour `min_holders`, qui est une profondeur de pagination et pas
     un filtre ;
  2. chaque stratégie réapplique SES seuils de liquidité / volume / âge, sinon
     elle hérite silencieusement du seuil le plus laxiste de l'enveloppe.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json  # noqa: E402

from src.core.cache import TokenCache  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.core.params import ParamsStore  # noqa: E402
from src.pipeline import ScanPipeline, discovery_envelope  # noqa: E402

BASE_PARAMS = {
    "version": "test",
    "scan": {"chains": ["solana"], "max_candidates_enriched": 25,
             "social_lookup_min_alpha": 70},
    "filters": {
        "min_liquidity_usd": 25000, "min_volume_1h": 8000, "min_holders": 75,
        "min_rugcheck_score": 70, "max_top_wallet_concentration": 20,
        "max_dev_wallet_pct": 10, "min_social_mentions_1h": 0,
        "min_smart_money_buys_30min": 0, "max_age_hours": 6, "min_age_hours": 1.5,
    },
    "scoring_weights": {"liquidity": 0.4, "volume_momentum": 0.6},
}


def candidate(symbol, liquidity=50000, volume=20000, age=3.0):
    return Candidate(
        token_address=f"addr_{symbol}", symbol=symbol, name=symbol, chain="solana",
        price_usd=1.0, liquidity_usd=liquidity, volume_1h=volume, age_hours=age,
        pair_address=f"pair_{symbol}",
    )


class FakeDex:
    """Renvoie un lot fixe et compte ses appels — aucun réseau."""

    def __init__(self, candidates):
        self._candidates = candidates
        self.calls = []
        self.request_count = 0

    def scan_new_meme_coins(self, chain, min_liquidity, min_volume_1h,
                            max_age_hours, min_age_hours, always_include=()):
        self.calls.append(
            {"min_liquidity": min_liquidity, "min_volume_1h": min_volume_1h,
             "max_age_hours": max_age_hours, "min_age_hours": min_age_hours}
        )
        return list(self._candidates), 0


class Disabled:
    """API désactivée : le pipeline doit dégrader sans planter."""

    enabled = False

    def get_report(self, _address):
        return None


class TestEnveloppeDeDecouverte(unittest.TestCase):
    def test_prend_le_seuil_le_plus_laxiste(self):
        envelope = discovery_envelope([
            {"min_liquidity_usd": 25000, "min_volume_1h": 8000,
             "max_age_hours": 6, "min_age_hours": 1.5},
            {"min_liquidity_usd": 60000, "min_volume_1h": 15000,
             "max_age_hours": 4, "min_age_hours": 2.0},
        ])
        self.assertEqual(envelope["min_liquidity_usd"], 25000)
        self.assertEqual(envelope["min_volume_1h"], 8000)
        self.assertEqual(envelope["max_age_hours"], 6)
        self.assertEqual(envelope["min_age_hours"], 1.5)

    def test_prend_le_MAXIMUM_pour_min_holders(self):
        # Inversion volontaire : min_holders est la profondeur de pagination
        # Helius. Trop bas, le compte de holders est une borne inférieure et
        # une stratégie stricte rejette à tort un token qui passait.
        envelope = discovery_envelope([{"min_holders": 75}, {"min_holders": 300}])
        self.assertEqual(envelope["min_holders"], 300)

    def test_liste_vide_retombe_sur_les_defauts(self):
        self.assertEqual(discovery_envelope([])["min_liquidity_usd"], 15000)


class TestEvaluationParStrategie(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = TokenCache(os.path.join(self.tmp, "cache.json"))
        self.dex = FakeDex([
            candidate("RICHE", liquidity=80000),
            candidate("PAUVRE", liquidity=30000),
            candidate("VIEUX", liquidity=80000, age=5.0),
        ])
        self.params = self._params(BASE_PARAMS)
        self.pipeline = ScanPipeline(
            params=self.params, cache=self.cache, dex=self.dex,
            helius=Disabled(), rugcheck=Disabled(),
        )

    def _params(self, data, name="params.json"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return ParamsStore(path)

    def _strict(self):
        data = json.loads(json.dumps(BASE_PARAMS))
        data["filters"]["min_liquidity_usd"] = 60000
        data["filters"]["max_age_hours"] = 4
        return self._params(data, "strict.json")

    def test_evaluate_reapplique_la_liquidite_de_la_strategie(self):
        # PAUVRE (30K) passe l'enveloppe à 25K mais pas le seuil strict à 60K.
        batch = self.pipeline.collect(discovery_envelope([{"min_liquidity_usd": 25000}]))
        evaluation = self.pipeline.evaluate(batch, params=self._strict())
        kept = {c.symbol for c in evaluation.result.candidates}
        self.assertNotIn("PAUVRE", kept)
        reason = next(
            c.rejected_reason for c in evaluation.result.rejected if c.symbol == "PAUVRE"
        )
        self.assertIn("liquidité", reason)

    def test_evaluate_reapplique_lage_de_la_strategie(self):
        batch = self.pipeline.collect()
        evaluation = self.pipeline.evaluate(batch, params=self._strict())
        self.assertNotIn("VIEUX", {c.symbol for c in evaluation.result.candidates})

    def test_deux_strategies_jugent_le_meme_lot_differemment(self):
        batch = self.pipeline.collect()
        large = self.pipeline.evaluate(batch, params=self.params)
        strict = self.pipeline.evaluate(batch, params=self._strict())
        self.assertGreater(len(large.result.candidates), len(strict.result.candidates))

    def test_une_seule_decouverte_pour_n_strategies(self):
        batch = self.pipeline.collect()
        for _ in range(4):
            self.pipeline.evaluate(batch, params=self.params)
        self.assertEqual(len(self.dex.calls), 1, "evaluate() ne doit coûter aucun appel")

    def test_evaluate_na_aucun_effet_de_bord_sur_le_cache(self):
        batch = self.pipeline.collect()
        before = dict(self.cache.stats)
        self.pipeline.evaluate(batch, params=self.params)
        self.assertEqual(dict(self.cache.stats), before)

    def test_apply_cache_effects_surveille_lunion(self):
        # Surveillé si UNE stratégie le garde, même si l'autre le rejette :
        # sinon son historique de prix cesse de se remplir.
        self.pipeline.apply_cache_effects(kept={"addr_A"}, rejected={"addr_A", "addr_B"})
        self.assertIn("addr_A", self.cache.watched())
        self.assertNotIn("addr_B", self.cache.watched())

    def test_wishlist_ordonnee_par_score(self):
        batch = self.pipeline.collect()
        evaluation = self.pipeline.evaluate(batch, params=self.params)
        scores = [
            c.alpha_score_absolute
            for c in evaluation.result.candidates
            if c.token_address in evaluation.wishlist
        ]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_run_cycle_reste_equivalent(self):
        # Le chemin mono-stratégie doit rendre exactement ce que rend
        # collect + evaluate, ordre des candidats compris.
        result = self.pipeline.run_cycle()
        batch = self.pipeline.collect()
        direct = self.pipeline.evaluate(batch, params=self.params).result
        self.assertEqual(
            [c.symbol for c in result.candidates], [c.symbol for c in direct.candidates]
        )
        self.assertEqual(
            sorted(c.rejected_reason for c in result.rejected),
            sorted(c.rejected_reason for c in direct.rejected),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
