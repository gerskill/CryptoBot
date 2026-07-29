"""Tests unitaires du noyau : rate limiter, cache, scoring, technique."""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.technical import PriceHistory, analyze  # noqa: E402
from src.core.cache import TokenCache  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.core.params import ParamsStore  # noqa: E402
from src.core.ratelimit import RateLimiter  # noqa: E402
from src.core.scoring import score_candidates  # noqa: E402

WEIGHTS = {
    "liquidity": 0.20,
    "volume_momentum": 0.25,
    "social_sentiment": 0.20,
    "smart_money": 0.20,
    "rugcheck": 0.15,
}


def make_candidate(symbol="TEST", **kwargs) -> Candidate:
    base = dict(
        token_address=f"addr_{symbol}",
        symbol=symbol,
        name=symbol,
        chain="solana",
        price_usd=0.001,
        liquidity_usd=30000,
        volume_1h=20000,
        volume_liquidity_ratio=0.66,
    )
    base.update(kwargs)
    return Candidate(**base)


class TestRateLimiter(unittest.TestCase):
    def test_bloque_au_dela_du_quota(self):
        # Arrange : 2 appels autorisés par seconde (marge 0.9 -> 1 slot)
        limiter = RateLimiter(max_calls=2, period=1.0)
        # Act
        start = time.monotonic()
        for _ in range(3):
            limiter.acquire()
        elapsed = time.monotonic() - start
        # Assert : au moins une attente de fenêtre
        self.assertGreater(elapsed, 0.9)

    def test_refuse_quota_invalide(self):
        with self.assertRaises(ValueError):
            RateLimiter(max_calls=0)


class TestTokenCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "cache.json")

    def test_token_vu_est_frais_puis_expire(self):
        cache = TokenCache(self.path, ttl_minutes=30)
        self.assertFalse(cache.is_fresh("mint1"))
        cache.mark_seen("mint1")
        self.assertTrue(cache.is_fresh("mint1"))

        cache.ttl_seconds = 0  # simule l'expiration
        self.assertFalse(cache.is_fresh("mint1"))

    def test_blacklist_expire(self):
        cache = TokenCache(self.path)
        cache.blacklist("mint2", "honeypot", hours=48)
        self.assertTrue(cache.is_blacklisted("mint2"))
        self.assertEqual(cache.blacklist_reason("mint2"), "honeypot")

        cache.blacklist("mint3", "test", hours=-1)  # déjà expiré
        self.assertFalse(cache.is_blacklisted("mint3"))

    def test_persistance_disque(self):
        cache = TokenCache(self.path)
        cache.mark_seen("mint4")
        cache.blacklist("mint5", "rug", hours=24)
        cache.watch("mint6")
        cache.save()

        reloaded = TokenCache(self.path)
        self.assertTrue(reloaded.is_fresh("mint4"))
        self.assertTrue(reloaded.is_blacklisted("mint5"))
        self.assertTrue(reloaded.is_watched("mint6"))

    def test_token_surveille_ignore_le_ttl(self):
        # Sans ça l'historique de prix ne se remplit jamais -> analyse technique bloquée.
        cache = TokenCache(self.path, ttl_minutes=30)
        cache.mark_seen("mint7")
        self.assertTrue(cache.is_fresh("mint7"))

        cache.watch("mint7")
        self.assertFalse(cache.is_fresh("mint7"))

        cache.unwatch("mint7")
        self.assertTrue(cache.is_fresh("mint7"))

    def test_blacklist_retire_de_la_watchlist(self):
        cache = TokenCache(self.path)
        cache.watch("mint8")
        cache.blacklist("mint8", "risque critique", hours=168)
        self.assertFalse(cache.is_watched("mint8"))

    def test_fichier_corrompu_ne_crashe_pas(self):
        with open(self.path, "w") as fh:
            fh.write("{ pas du json")
        cache = TokenCache(self.path)
        self.assertEqual(cache.stats["seen"], 0)


class TestScoring(unittest.TestCase):
    def test_score_dans_les_bornes(self):
        candidates = [
            make_candidate("A", liquidity_usd=15000, volume_liquidity_ratio=0.2, rugcheck_score=70),
            make_candidate("B", liquidity_usd=90000, volume_liquidity_ratio=2.5, rugcheck_score=95),
        ]
        scored = score_candidates(candidates, WEIGHTS)
        for candidate in scored:
            self.assertGreaterEqual(candidate.alpha_score, 0)
            self.assertLessEqual(candidate.alpha_score, 100)

    def test_meilleur_token_classe_premier(self):
        candidates = [
            make_candidate("FAIBLE", liquidity_usd=15000, volume_liquidity_ratio=0.1, rugcheck_score=70),
            make_candidate("FORT", liquidity_usd=120000, volume_liquidity_ratio=2.8, rugcheck_score=98),
        ]
        scored = score_candidates(candidates, WEIGHTS)
        self.assertEqual(scored[0].symbol, "FORT")

    def test_poids_redistribues_si_donnee_absente(self):
        # Sans social ni smart money : seuls 3 composants sur 5 sont utilisés.
        scored = score_candidates([make_candidate(rugcheck_score=80)], WEIGHTS)
        used = scored[0].sub_scores
        self.assertNotIn("social_sentiment", used)
        self.assertAlmostEqual(used["_weights_used"], 0.60, places=3)
        self.assertLessEqual(scored[0].alpha_score, 100)

    def test_liste_vide(self):
        self.assertEqual(score_candidates([], WEIGHTS), [])

    def test_immuabilite_des_candidats(self):
        original = make_candidate(rugcheck_score=80)
        score_candidates([original], WEIGHTS)
        self.assertEqual(original.alpha_score, 0.0)


class TestTechnical(unittest.TestCase):
    def test_pending_si_historique_insuffisant(self):
        candidate = make_candidate(price_change_24h=20)
        verdict = analyze(candidate, PriceHistory())
        self.assertTrue(verdict.pending)
        self.assertFalse(verdict.passed)

    def test_rejette_dump_initial(self):
        candidate = make_candidate(price_change_24h=-70)
        verdict = analyze(candidate, PriceHistory())
        self.assertFalse(verdict.checks["no_initial_dump"])

    def test_valide_structure_haussiere(self):
        history = PriceHistory()
        candidate = make_candidate(price_change_24h=50)
        now = time.time()
        # 3 bougies 5m : prix et volume croissants
        for index, (price, volume) in enumerate([(1.0, 100), (1.2, 200), (1.5, 300)]):
            history.record(
                candidate.with_fields(price_usd=price, volume_5m=volume),
                ts=now - (2 - index) * 300,
            )
        verdict = analyze(candidate, history)
        self.assertFalse(verdict.pending)
        self.assertTrue(verdict.passed, verdict.reasons)

    def test_detecte_chute_de_liquidite(self):
        history = PriceHistory()
        candidate = make_candidate()
        now = time.time()
        history.record(candidate.with_fields(liquidity_usd=50000), ts=now - 60)
        history.record(candidate.with_fields(liquidity_usd=20000), ts=now)
        drop = history.liquidity_drop_pct(candidate.token_address, window_seconds=120)
        self.assertLess(drop, -50)


class TestParamsStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "params.json")
        with open(self.path, "w") as fh:
            fh.write('{"filters": {"min_liquidity_usd": 15000}, "learning": {}}')

    def test_lecture_par_chemin(self):
        store = ParamsStore(self.path)
        self.assertEqual(store.get("filters.min_liquidity_usd"), 15000)
        self.assertIsNone(store.get("filters.inexistant"))
        self.assertEqual(store.get("a.b.c", "defaut"), "defaut")

    def test_ecriture_journalise_lhistorique(self):
        store = ParamsStore(self.path)
        store.set("filters.min_liquidity_usd", 20000, reason="WR bas < 20K", sample_size=10)

        reloaded = ParamsStore(self.path)
        self.assertEqual(reloaded.get("filters.min_liquidity_usd"), 20000)
        history = reloaded.get("learning.parameter_adjustment_history")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["old_value"], 15000)
        self.assertEqual(history[0]["new_value"], 20000)

    def test_data_est_une_copie(self):
        store = ParamsStore(self.path)
        snapshot = store.data
        snapshot["filters"]["min_liquidity_usd"] = 999999
        self.assertEqual(store.get("filters.min_liquidity_usd"), 15000)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestScoringAbsoluVsRelatif(unittest.TestCase):
    """Le score absolu ne doit jamais dépendre des autres candidats du lot."""

    def test_meilleur_dun_lot_mediocre_ne_franchit_pas_le_seuil(self):
        # Trois tokens faibles : le classement en met un premier, mais aucun
        # ne doit atteindre 75 en absolu.
        faibles = [
            make_candidate(f"F{i}", liquidity_usd=16000 + i * 500,
                           volume_liquidity_ratio=0.1 + i * 0.02, rugcheck_score=70)
            for i in range(3)
        ]
        scored = score_candidates(faibles, WEIGHTS)
        self.assertGreater(scored[0].alpha_score, scored[0].alpha_score_absolute)
        self.assertLess(scored[0].alpha_score_absolute, 75)

    def test_score_absolu_stable_quel_que_soit_le_lot(self):
        cible = make_candidate("CIBLE", liquidity_usd=60000,
                               volume_liquidity_ratio=1.5, rugcheck_score=90)
        seul = score_candidates([cible], WEIGHTS)[0]

        avec_concurrents = score_candidates(
            [cible,
             make_candidate("GROS", liquidity_usd=500000, volume_liquidity_ratio=5.0,
                            rugcheck_score=99),
             make_candidate("PETIT", liquidity_usd=15000, volume_liquidity_ratio=0.1,
                            rugcheck_score=70)],
            WEIGHTS,
        )
        meme = next(c for c in avec_concurrents if c.symbol == "CIBLE")
        self.assertAlmostEqual(seul.alpha_score_absolute, meme.alpha_score_absolute, places=2)
        self.assertNotAlmostEqual(seul.alpha_score, meme.alpha_score, places=2)

    def test_penalite_spam_sur_faible_diversite_dauteurs(self):
        spam = make_candidate("SPAM", social_mentions_1h=80, social_unique_authors=3,
                              social_engagement=50, social_sample_size=80,
                              social_velocity_15m=20, rugcheck_score=90)
        organique = make_candidate("ORGA", social_mentions_1h=80, social_unique_authors=60,
                                   social_engagement=50, social_sample_size=80,
                                   social_velocity_15m=20, rugcheck_score=90)
        scored = {c.symbol: c for c in score_candidates([spam, organique], WEIGHTS)}
        self.assertLess(
            scored["SPAM"].sub_scores["social_sentiment"],
            scored["ORGA"].sub_scores["social_sentiment"],
        )


class TestStructureModes(unittest.TestCase):
    """Le mode strict de la spec ne se déclenche jamais — mesuré à 0/43 en live."""

    @staticmethod
    def _candles(closes, highs=None, lows=None, volumes=None):
        from src.analysis.technical import Candle
        n = len(closes)
        highs = highs or [c * 1.01 for c in closes]
        lows = lows or [c * 0.99 for c in closes]
        volumes = volumes or [100] * n
        return [Candle(bucket=i, high=highs[i], low=lows[i], close=closes[i],
                       volume=volumes[i]) for i in range(n)]

    def test_balanced_accepte_une_tendance_en_dents_de_scie(self):
        # Monte globalement mais sans higher highs consécutifs : le cas réel
        # le plus fréquent, rejeté par le mode strict.
        candles = self._candles(
            closes=[1.0, 1.0, 1.0, 1.0, 0.95, 1.10],
            highs=[1.0, 1.0, 1.0, 1.2, 0.96, 1.11],
            volumes=[100, 100, 100, 200, 300, 400],
        )
        candidate = make_candidate(price_change_24h=50)
        verdict = analyze(candidate, None, candles=candles, structure_mode="balanced")
        self.assertTrue(verdict.passed, verdict.reasons)

        strict = analyze(candidate, None, candles=candles, structure_mode="strict")
        self.assertFalse(strict.passed)

    def test_balanced_rejette_une_tendance_baissiere(self):
        candles = self._candles(closes=[1.0, 1.0, 1.0, 1.2, 1.1, 0.9],
                                volumes=[100, 100, 100, 400, 400, 400])
        verdict = analyze(make_candidate(price_change_24h=50), None,
                          candles=candles, structure_mode="balanced")
        self.assertFalse(verdict.passed)
        self.assertFalse(verdict.checks["uptrend"])

    def test_volume_compare_les_moyennes_pas_bougie_a_bougie(self):
        # Volume en dents de scie mais moyenne en hausse -> accepté.
        candles = self._candles(closes=[1.0, 1.0, 1.0, 1.0, 1.1, 1.2],
                                volumes=[100, 100, 100, 400, 150, 500])
        verdict = analyze(make_candidate(price_change_24h=50), None,
                          candles=candles, structure_mode="balanced")
        self.assertTrue(verdict.checks["volume_expanding"], verdict.reasons)

    def test_vraies_bougies_evitent_le_pending(self):
        candles = self._candles(closes=[1.0] * 6)
        verdict = analyze(make_candidate(price_change_24h=10), None, candles=candles)
        self.assertFalse(verdict.pending)
