"""Tests unitaires du noyau : rate limiter, cache, scoring, technique."""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.technical import Candle, PriceHistory, analyze  # noqa: E402
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
        limiter = RateLimiter(max_calls=2, period=1.0)
        start = time.monotonic()
        for _ in range(3):
            limiter.acquire()
        self.assertGreater(time.monotonic() - start, 0.9)

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
        # Sans ça l'historique de prix ne se remplit jamais -> analyse bloquée.
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
        for candidate in score_candidates(candidates, WEIGHTS):
            self.assertGreaterEqual(candidate.alpha_score, 0)
            self.assertLessEqual(candidate.alpha_score, 100)

    def test_meilleur_token_classe_premier(self):
        candidates = [
            make_candidate("FAIBLE", liquidity_usd=15000, volume_liquidity_ratio=0.1, rugcheck_score=70),
            make_candidate("FORT", liquidity_usd=120000, volume_liquidity_ratio=2.8, rugcheck_score=98),
        ]
        self.assertEqual(score_candidates(candidates, WEIGHTS)[0].symbol, "FORT")

    def test_poids_redistribues_si_donnee_absente(self):
        # Sans social ni smart money : seuls 3 composants sur 5 sont utilisés.
        scored = score_candidates([make_candidate(rugcheck_score=80)], WEIGHTS)
        used = scored[0].sub_scores
        self.assertNotIn("social_sentiment", used)
        self.assertAlmostEqual(used["_weights_used"], 0.60, places=3)

    def test_liste_vide(self):
        self.assertEqual(score_candidates([], WEIGHTS), [])

    def test_immuabilite_des_candidats(self):
        original = make_candidate(rugcheck_score=80)
        score_candidates([original], WEIGHTS)
        self.assertEqual(original.alpha_score, 0.0)


class TestScoringAbsoluVsRelatif(unittest.TestCase):
    """Le score absolu ne doit jamais dépendre des autres candidats du lot."""

    def test_meilleur_dun_lot_mediocre_ne_franchit_pas_le_seuil(self):
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


class TestTechnical(unittest.TestCase):
    def test_pending_si_historique_insuffisant(self):
        verdict = analyze(make_candidate(price_change_24h=20), PriceHistory())
        self.assertTrue(verdict.pending)
        self.assertFalse(verdict.passed)

    def test_rejette_dump_initial(self):
        verdict = analyze(make_candidate(price_change_24h=-70), PriceHistory())
        self.assertFalse(verdict.checks["no_initial_dump"])

    def test_valide_structure_haussiere(self):
        history = PriceHistory()
        candidate = make_candidate(price_change_24h=50)
        now = time.time()
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
        self.assertLess(history.liquidity_drop_pct(candidate.token_address, 120), -50)


class TestStructureModes(unittest.TestCase):
    """Le mode strict de la spec ne se déclenche jamais — mesuré à 0/43 en live."""

    @staticmethod
    def _candles(closes, highs=None, lows=None, volumes=None):
        n = len(closes)
        highs = highs or [c * 1.01 for c in closes]
        lows = lows or [c * 0.99 for c in closes]
        volumes = volumes or [100] * n
        return [Candle(bucket=i, high=highs[i], low=lows[i], close=closes[i],
                       volume=volumes[i]) for i in range(n)]

    def test_balanced_accepte_une_tendance_en_dents_de_scie(self):
        candles = self._candles(
            closes=[1.0, 1.0, 1.0, 1.0, 0.95, 1.10],
            highs=[1.0, 1.0, 1.0, 1.2, 0.96, 1.11],
            volumes=[100, 100, 100, 200, 300, 400],
        )
        candidate = make_candidate(price_change_24h=50)
        self.assertTrue(
            analyze(candidate, None, candles=candles, structure_mode="balanced").passed
        )
        self.assertFalse(
            analyze(candidate, None, candles=candles, structure_mode="strict").passed
        )

    def test_balanced_rejette_une_tendance_baissiere(self):
        candles = self._candles(closes=[1.0, 1.0, 1.0, 1.2, 1.1, 0.9],
                                volumes=[100, 100, 100, 400, 400, 400])
        verdict = analyze(make_candidate(price_change_24h=50), None,
                          candles=candles, structure_mode="balanced")
        self.assertFalse(verdict.passed)
        self.assertFalse(verdict.checks["uptrend"])

    def test_volume_compare_les_moyennes_pas_bougie_a_bougie(self):
        candles = self._candles(closes=[1.0, 1.0, 1.0, 1.0, 1.1, 1.2],
                                volumes=[100, 100, 100, 400, 150, 500])
        verdict = analyze(make_candidate(price_change_24h=50), None,
                          candles=candles, structure_mode="balanced")
        self.assertTrue(verdict.checks["volume_expanding"], verdict.reasons)

    def test_vraies_bougies_evitent_le_pending(self):
        candles = self._candles(closes=[1.0] * 6)
        self.assertFalse(analyze(make_candidate(price_change_24h=10), None, candles=candles).pending)


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

    def test_log_false_ne_pollue_pas_lhistorique(self):
        store = ParamsStore(self.path)
        store.set("filters.min_liquidity_usd", 20000, log=False)
        self.assertEqual(store.get("learning.parameter_adjustment_history", []), [])

    def test_data_est_une_copie(self):
        store = ParamsStore(self.path)
        snapshot = store.data
        snapshot["filters"]["min_liquidity_usd"] = 999999
        self.assertEqual(store.get("filters.min_liquidity_usd"), 15000)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestInstanceLock(unittest.TestCase):
    """Deux boucles en parallèle corrompent state.json, le journal et le cache."""

    def setUp(self):
        from src.core.lock import InstanceLock
        self.LockClass = InstanceLock
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "loop.pid")

    def test_premiere_acquisition_reussit(self):
        lock = self.LockClass(self.path)
        self.assertIsNone(lock.acquire())
        self.assertTrue(lock.acquired)
        self.assertTrue(os.path.exists(self.path))

    def test_deuxieme_instance_est_refusee(self):
        first = self.LockClass(self.path)
        first.acquire()

        second = self.LockClass(self.path)
        holder = second.acquire()
        self.assertEqual(holder, os.getpid())
        self.assertFalse(second.acquired)

    def test_verrou_perime_est_repris(self):
        # PID inexistant : cas du kill -9 ou du crash.
        with open(self.path, "w") as fh:
            fh.write("999999")
        lock = self.LockClass(self.path)
        self.assertIsNone(lock.acquire())
        self.assertTrue(lock.acquired)

    def test_fichier_corrompu_est_repris(self):
        with open(self.path, "w") as fh:
            fh.write("pas un pid")
        self.assertIsNone(self.LockClass(self.path).acquire())

    def test_release_supprime_le_verrou(self):
        lock = self.LockClass(self.path)
        lock.acquire()
        lock.release()
        self.assertFalse(os.path.exists(self.path))


class TestPortfolioReprise(unittest.TestCase):
    """Le capital doit survivre à un redémarrage : sinon le P&L repart à zéro."""

    def setUp(self):
        from src.core.journal import TradeJournal
        from src.core.portfolio import PaperPortfolio
        self.Journal, self.Portfolio = TradeJournal, PaperPortfolio
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "trades.jsonl")

    def _log(self, pnl_usd):
        import json as _json
        with open(self.path, "a") as fh:
            fh.write(_json.dumps({
                "is_final_exit": True, "pnl_usd": pnl_usd, "pnl_pct": pnl_usd,
                "exit_reason": "STOP_LOSS" if pnl_usd < 0 else "TAKE_PROFIT_1",
            }) + "\n")

    def test_capital_vierge_sans_journal(self):
        portfolio = self.Portfolio(1000.0, self.Journal(self.path))
        self.assertEqual(portfolio.capital, 1000.0)
        self.assertEqual(portfolio.stats()["total_pnl_usd"], 0.0)

    def test_capital_reconstruit_depuis_le_journal(self):
        self._log(-8.16)
        self._log(-29.73)
        portfolio = self.Portfolio(1000.0, self.Journal(self.path))
        self.assertAlmostEqual(portfolio.capital, 962.11, places=2)
        self.assertAlmostEqual(portfolio.stats()["total_pnl_usd"], -37.89, places=2)
        self.assertEqual(portfolio.stats()["baseline"], 1000.0)

    def test_pertes_consecutives_reprises(self):
        # Le cooldown doit tenir compte des pertes d'avant le redémarrage.
        self._log(+50)
        self._log(-10)
        self._log(-12)
        portfolio = self.Portfolio(1000.0, self.Journal(self.path))
        self.assertEqual(portfolio.consecutive_losses, 2)

    def test_serie_de_pertes_coupee_par_un_gain(self):
        self._log(-10)
        self._log(+30)
        portfolio = self.Portfolio(1000.0, self.Journal(self.path))
        self.assertEqual(portfolio.consecutive_losses, 0)

    def test_gain_augmente_le_capital(self):
        self._log(+120)
        portfolio = self.Portfolio(1000.0, self.Journal(self.path))
        self.assertAlmostEqual(portfolio.capital, 1120.0, places=2)


class TestPortfolioPersistance(unittest.TestCase):
    """Un redémarrage ne doit rien perdre : positions, drawdown, cooldown."""

    def setUp(self):
        from src.core.journal import TradeJournal
        from src.core.portfolio import PaperPortfolio
        self.Journal, self.Portfolio = TradeJournal, PaperPortfolio
        self.tmp = tempfile.mkdtemp()
        self.trades = os.path.join(self.tmp, "trades.jsonl")
        self.open_path = os.path.join(self.tmp, "open.json")
        self.params = {"version": "2.0", "exit_rules": {"stop_loss_pct": -25}}

    def _portfolio(self):
        return self.Portfolio(1000.0, self.Journal(self.trades),
                              positions_path=self.open_path, cooldown_hours=2)

    def _log(self, pnl_usd, iso=None):
        import json as _json
        from datetime import datetime, timezone
        with open(self.trades, "a") as fh:
            fh.write(_json.dumps({
                "is_final_exit": True, "pnl_usd": pnl_usd, "pnl_pct": pnl_usd,
                "exit_reason": "STOP_LOSS" if pnl_usd < 0 else "TAKE_PROFIT_1",
                "timestamp_exit": iso or datetime.now(timezone.utc).isoformat(),
            }) + "\n")

    def test_position_survit_au_redemarrage(self):
        first = self._portfolio()
        first.open(make_candidate("YETI", price_usd=0.0002), self.params, 30.0)
        self.assertAlmostEqual(first.capital, 970.0)

        restarted = self._portfolio()
        self.assertEqual(len(restarted.positions), 1)
        restored = next(iter(restarted.positions.values()))
        self.assertEqual(restored.symbol, "YETI")
        # La mise reste engagée : elle ne doit pas être remboursée en douce
        self.assertAlmostEqual(restarted.capital, 970.0)

    def test_etat_de_sortie_partielle_conserve(self):
        first = self._portfolio()
        position = first.open(make_candidate("PART", price_usd=1.0), self.params, 30.0)
        first.update(position.id, 2.5)  # +150% -> TP1 franchi
        self.assertTrue(first.positions[position.id].tp1_hit)

        restored = next(iter(self._portfolio().positions.values()))
        self.assertTrue(restored.tp1_hit)
        self.assertTrue(restored.breakeven_moved)
        self.assertLess(restored.remaining_fraction, 1.0)

    def test_cloture_vide_le_fichier_de_positions(self):
        first = self._portfolio()
        position = first.open(make_candidate("SL", price_usd=1.0), self.params, 30.0)
        first.update(position.id, 0.5)  # -50% -> stop loss
        self.assertEqual(len(first.positions), 0)
        self.assertEqual(len(self._portfolio().positions), 0)

    def test_fichier_de_positions_corrompu(self):
        with open(self.open_path, "w") as fh:
            fh.write("{{{ pas du json")
        portfolio = self._portfolio()
        self.assertEqual(len(portfolio.positions), 0)
        self.assertEqual(portfolio.capital, 1000.0)

    def test_drawdown_reconstruit(self):
        # +100 puis -200 : sommet à 1100, creux à 900 -> 18.18%
        self._log(+100)
        self._log(-200)
        self.assertAlmostEqual(self._portfolio().max_drawdown_pct, 18.18, places=1)

    def test_cooldown_survit_au_redemarrage(self):
        # Relancer le bot ne doit pas contourner la pause après 3 pertes.
        for _ in range(3):
            self._log(-10)
        portfolio = self._portfolio()
        self.assertTrue(portfolio.in_cooldown)
        self.assertGreater(portfolio.cooldown_remaining_min(), 100)

    def test_cooldown_desactivable(self):
        # En PAPER on veut collecter les 20 trades sans pause forcée.
        for _ in range(3):
            self._log(-10)
        sans = self.Portfolio(1000.0, self.Journal(self.trades),
                              positions_path=self.open_path, cooldown_hours=0)
        self.assertFalse(sans.in_cooldown)
        self.assertEqual(sans.consecutive_losses, 3)  # la série reste comptée

    def test_cooldown_expire_apres_deux_heures(self):
        from datetime import datetime, timedelta, timezone
        vieux = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        for _ in range(3):
            self._log(-10, iso=vieux)
        self.assertFalse(self._portfolio().in_cooldown)


class TestGmgnLectureSeule(unittest.TestCase):
    """Le module GMGN ne doit JAMAIS pouvoir exécuter une transaction."""

    def setUp(self):
        from src.apis.gmgn import ForbiddenCommand, GmgnAPI
        self.GmgnAPI, self.Forbidden = GmgnAPI, ForbiddenCommand
        self.api = GmgnAPI(enabled=False)

    def test_commandes_de_trading_refusees(self):
        # gmgn-cli sait acheter, vendre et créer des tokens : la liste
        # blanche doit rejeter avant même d'atteindre le sous-processus.
        for interdite in [
            ("swap", "--token"), ("multi-swap", "--token"),
            ("order", "create"), ("cooking", "create"), ("config", "--apply"),
        ]:
            with self.assertRaises(self.Forbidden, msg=f"{interdite} non bloquée"):
                self.api._run(*interdite)

    def test_commandes_de_lecture_autorisees(self):
        # Autorisées par la liste blanche : retournent None car désactivé,
        # mais ne lèvent pas.
        for permise in [
            ("track", "smartmoney"), ("token", "security"),
            ("token", "holders"), ("market", "trending"),
        ]:
            self.assertIsNone(self.api._run(*permise))

    def test_desactive_sans_cle(self):
        self.assertFalse(self.api.enabled)
        self.assertEqual(self.api.fetch_smart_money_trades(), [])
        self.assertEqual(self.api.activity_by_token(), {})


class TestGmgnParsing(unittest.TestCase):
    """Le format du CLI varie entre versions : le parsing doit être tolérant."""

    def setUp(self):
        from src.apis.gmgn import GmgnAPI
        self.api = GmgnAPI(enabled=False)

    def _snapshot(self, trades):
        self.api._smart_money_snapshot = (time.time(), trades)
        self.api.enabled = True

    def test_agrege_achats_et_ventes_par_token(self):
        now = time.time()
        self._snapshot([
            {"token_address": "AAA", "side": "buy", "wallet_address": "w1",
             "timestamp": now, "volume_usd": 500},
            {"token_address": "AAA", "side": "buy", "wallet_address": "w2",
             "timestamp": now, "volume_usd": 300},
            {"token_address": "AAA", "side": "sell", "wallet_address": "w1",
             "timestamp": now, "volume_usd": 100},
            {"token_address": "BBB", "side": "buy", "wallet_address": "w3",
             "timestamp": now, "volume_usd": 50},
        ])
        activity = self.api.activity_by_token()
        self.assertEqual(activity["AAA"].buys, 2)
        self.assertEqual(activity["AAA"].sells, 1)
        self.assertEqual(activity["AAA"].unique_wallets, 2)
        self.assertEqual(activity["AAA"].volume_usd, 900.0)
        self.assertAlmostEqual(activity["AAA"].net_pressure, 2 / 3)

    def test_ignore_les_trades_hors_fenetre(self):
        now = time.time()
        self._snapshot([
            {"token_address": "AAA", "side": "buy", "timestamp": now},
            {"token_address": "AAA", "side": "buy", "timestamp": now - 3600},
        ])
        self.assertEqual(self.api.activity_by_token(window_minutes=30)["AAA"].buys, 1)

    def test_timestamp_en_millisecondes(self):
        self._snapshot([
            {"token_address": "AAA", "side": "buy", "timestamp": time.time() * 1000},
        ])
        self.assertEqual(self.api.activity_by_token()["AAA"].buys, 1)

    def test_champ_base_address_du_flux_reel(self):
        """`base_address` est le nom réel côté /v1/user/smartmoney.

        Vérifié en live : ni `token_address` ni `mint` n'existent dans ce
        flux. Sans ce champ, tous les trades étaient silencieusement jetés
        et l'activité smart money ressortait vide.
        """
        now = time.time()
        self._snapshot([
            {"base_address": "AAA", "side": "buy", "maker": "w1",
             "timestamp": now, "amount_usd": 147.77},
            {"base_address": "AAA", "side": "sell", "maker": "w2",
             "timestamp": now, "amount_usd": 50.0},
        ])
        activity = self.api.activity_by_token()
        self.assertIn("AAA", activity)
        self.assertEqual(activity["AAA"].buys, 1)
        self.assertEqual(activity["AAA"].sells, 1)
        self.assertEqual(activity["AAA"].unique_wallets, 2)
        self.assertAlmostEqual(activity["AAA"].volume_usd, 197.77, places=2)

    def test_noms_de_champs_alternatifs(self):
        now = time.time()
        self._snapshot([
            {"token": "AAA", "event": "sell", "maker": "w1", "block_time": now},
            {"mint": "AAA", "direction": "buy", "address": "w2", "trade_time": now},
        ])
        activity = self.api.activity_by_token()
        self.assertEqual(activity["AAA"].buys, 1)
        self.assertEqual(activity["AAA"].sells, 1)


class TestPositionRestaurableEtFermable(unittest.TestCase):
    """Une position restaurée doit pouvoir se fermer.

    JSON n'a pas de tuple : `exit_reasons` revenait en liste et la première
    sortie levait `TypeError: can only concatenate list (not "tuple") to list`.
    L'exception étant avalée par la boucle, la position ne se fermait JAMAIS.
    """

    def setUp(self):
        from src.core.journal import TradeJournal
        from src.core.portfolio import PaperPortfolio
        self.Journal, self.Portfolio = TradeJournal, PaperPortfolio
        self.tmp = tempfile.mkdtemp()
        self.trades = os.path.join(self.tmp, "trades.jsonl")
        self.open_path = os.path.join(self.tmp, "open.json")
        self.params = {"version": "2.0", "exit_rules": {"stop_loss_pct": -25}}

    def _portfolio(self):
        return self.Portfolio(1000.0, self.Journal(self.trades),
                              positions_path=self.open_path, cooldown_hours=0)

    def test_exit_reasons_revient_en_tuple(self):
        first = self._portfolio()
        first.open(make_candidate("REST", price_usd=1.0), self.params, 30.0)
        restored = next(iter(self._portfolio().positions.values()))
        self.assertIsInstance(restored.exit_reasons, tuple)

    def test_position_restauree_peut_etre_fermee(self):
        first = self._portfolio()
        first.open(make_candidate("SL", price_usd=1.0), self.params, 30.0)

        restarted = self._portfolio()
        position_id = next(iter(restarted.positions))
        rows = restarted.update(position_id, 0.5)  # -50% -> stop loss

        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["is_final_exit"])
        self.assertEqual(len(restarted.positions), 0)

    def test_sortie_partielle_puis_restauration_puis_cloture(self):
        first = self._portfolio()
        position = first.open(make_candidate("TP", price_usd=1.0), self.params, 30.0)
        first.update(position.id, 2.5)  # TP1
        self.assertEqual(len(first.positions[position.id].exit_reasons), 1)

        restarted = self._portfolio()
        restored_id = next(iter(restarted.positions))
        restarted.update(restored_id, 0.9)  # repasse sous le breakeven
        self.assertEqual(len(restarted.positions), 0)
