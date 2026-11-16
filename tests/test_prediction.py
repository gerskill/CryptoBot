"""Marchés de prédiction Liquid — simulation papier.

Ce que ces tests verrouillent : un pari simulé doit coûter ce qu'il coûterait
vraiment (prix vendeur du carnet, frais), ne jamais s'appuyer sur une donnée
absente, ne jamais être réglé au hasard, et rester attribué à son bras.
Aucun test ne touche le réseau : l'API Hyperliquid est doublée.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from src.prediction import estimators  # noqa: E402
from src.prediction.markets import parse_expiry, parse_outcome_meta  # noqa: E402
from src.prediction.paper import BetStore, compute_stats, net_pnl, simulate_entry  # noqa: E402
from src.prediction.runner import PredictionRunner  # noqa: E402

NOW = datetime(2026, 11, 16, 13, 0, tzinfo=timezone.utc)


def make_meta(target=64000, expiry="20261116-1315", klass="priceBinary", underlying="BTC"):
    return {
        "outcomes": [
            {"outcome": 104, "name": "Recurring",
             "description": f"class:{klass}|underlying:{underlying}|expiry:{expiry}|targetPrice:{target}|period:15m",
             "sideSpecs": [{"name": "Yes"}, {"name": "No"}]},
        ],
        "questions": [],
    }


def make_config(arms):
    return {"capital_fictif_usd": 1000, "mise_pct": 0.02, "exposition_max_pct_par_domaine": 0.10,
            "frais_pct": 0.009, "frais_reglement_pct": 0.009, "min_paris_verdict": 30, "arms": arms}


TEMOIN = {"name": "pm_temoin", "role": "temoin", "enabled": True,
          "domaines": ["crypto_seuil", "sport_evenement"], "prix_min": 0.5, "prix_max": 0.95,
          "min_minutes": 3, "max_jours": 7}
VOL = {"name": "pm_vol_edge", "role": "parieur", "enabled": True, "domaines": ["crypto_seuil"],
       "estimateur": "empirique", "intervalle": "1m", "bougies": 300, "edge_min": 0.05,
       "min_minutes": 3, "max_jours": 1}


class FakeApi:
    """Double de l'API : renvoie ce qu'on lui donne, compte les lectures de carnet."""

    def __init__(self, meta, mids, books=None, candles=None):
        self.meta, self.mids = meta, mids
        self.books = books or {}
        self.candle_rows = candles or []
        self.book_calls = 0

    def outcome_meta(self):
        return self.meta

    def all_mids(self):
        return dict(self.mids)

    def l2_book(self, coin):
        self.book_calls += 1
        return self.books.get(coin)

    def candles(self, coin, interval, start_ms, end_ms):
        return [r for r in self.candle_rows if start_ms <= r["t"] <= end_ms]


def flat_candles(price, count, end=NOW):
    """Bougies 1 min sans mouvement : le prix ne franchit jamais un seuil éloigné."""
    start = end - timedelta(minutes=count)
    return [{"t": (start + timedelta(minutes=i)).timestamp() * 1000,
             "T": (start + timedelta(minutes=i + 1)).timestamp() * 1000, "c": price}
            for i in range(count + 1)]


class PredictionTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.clock = [NOW]

    def runner(self, api, arms):
        return PredictionRunner(api, make_config(arms), self.tmp, now=lambda: self.clock[0])


class TestLectureDesMarches(unittest.TestCase):
    def test_la_piece_d_un_cote_vaut_dix_fois_le_marche_plus_le_cote(self):
        market = parse_outcome_meta(make_meta())[0]
        self.assertEqual([s.coin for s in market.sides], ["#1040", "#1041"])
        self.assertEqual(market.domain, "crypto_seuil")
        self.assertEqual(market.expiry, datetime(2026, 11, 16, 13, 15, tzinfo=timezone.utc))

    def test_une_echeance_illisible_reste_inconnue_au_lieu_d_etre_devinee(self):
        self.assertIsNone(parse_expiry("demain"))
        self.assertIsNone(parse_expiry(None))

    def test_un_sous_jacent_non_crypto_est_range_en_actions(self):
        market = parse_outcome_meta(make_meta(underlying="NVDA"))[0]
        self.assertEqual(market.domain, "actions_seuil")

    def test_le_marche_de_repli_d_une_question_est_marque_pour_ne_pas_etre_traite(self):
        meta = {"outcomes": [{"outcome": 1, "name": "Draw", "description": "Bosnia vs Romania"},
                             {"outcome": 2, "name": "Other", "description": "Bosnia vs Romania"}],
                "questions": [{"name": "Bosnia vs Romania", "namedOutcomes": [1], "fallbackOutcome": 2}]}
        by_id = {m.outcome: m for m in parse_outcome_meta(meta)}
        self.assertFalse(by_id[1].is_fallback)
        self.assertTrue(by_id[2].is_fallback)
        self.assertEqual(by_id[1].domain, "sport_evenement")

    def test_une_reponse_vide_ou_mal_formee_donne_zero_marche_sans_planter(self):
        self.assertEqual(parse_outcome_meta(None), [])
        self.assertEqual(parse_outcome_meta({"outcomes": [{"name": "sans identifiant"}]}), [])


class TestEntreeAuCarnet(unittest.TestCase):
    def test_le_prix_paye_est_la_moyenne_du_carnet_vendeur_pas_le_meilleur_prix(self):
        fill = simulate_entry([(0.50, 20), (0.60, 100)], 20)
        # 10 $ à 0,50 (20 parts) puis 10 $ à 0,60 (16,67 parts).
        self.assertAlmostEqual(fill["shares"], 20 + 10 / 0.6)
        self.assertAlmostEqual(fill["price"], 20 / (20 + 10 / 0.6))
        self.assertGreater(fill["price"], 0.50)

    def test_un_carnet_trop_mince_ne_donne_pas_d_entree(self):
        self.assertIsNone(simulate_entry([(0.50, 10)], 20))
        self.assertIsNone(simulate_entry([], 20))

    def test_un_pari_gagnant_paye_les_frais_des_deux_cotes(self):
        # 20 $ à 0,50 = 40 parts. Gagné : 40 × (1 − 0,9 %) − 20 × (1 + 0,9 %).
        self.assertAlmostEqual(net_pnl(20, 40, 1.0, 0.009, 0.009), 40 * 0.991 - 20.18)

    def test_un_pari_perdant_coute_la_mise_plus_les_frais_d_achat(self):
        self.assertAlmostEqual(net_pnl(20, 40, 0.0, 0.009, 0.009), -20.18)


class TestEstimateurs(unittest.TestCase):
    def test_pas_assez_d_historique_ne_donne_aucune_probabilite(self):
        self.assertIsNone(estimators.prob_above_empirical(100, 101, [100.0] * 20, 5))
        self.assertIsNone(estimators.prob_above_lognormal(100, 101, [100.0] * 20, 5))

    def test_un_prix_immobile_ne_franchit_jamais_un_seuil_plus_haut(self):
        self.assertEqual(estimators.prob_above_empirical(100, 101, [100.0] * 200, 5), 0.0)
        self.assertEqual(estimators.prob_above_empirical(100, 99, [100.0] * 200, 5), 1.0)

    def test_toucher_est_au_moins_aussi_probable_que_finir_au_dela(self):
        closes = [100 * (1.001 if i % 2 else 0.999) ** (i % 7) for i in range(400)]
        above = estimators.prob_above_lognormal(100, 100.5, closes, 30)
        touch = estimators.prob_touch(100, 100.5, closes, 30)
        self.assertGreaterEqual(touch, above)
        self.assertLessEqual(touch, 1.0)


class TestCycle(PredictionTestCase):
    def test_le_temoin_achete_le_favori_au_prix_vendeur_et_pas_au_prix_affiche(self):
        api = FakeApi(make_meta(), {"#1040": 0.80, "#1041": 0.20, "BTC": 64100},
                      books={"#1040": {"bids": [(0.79, 500)], "asks": [(0.82, 500)]}})
        self.runner(api, [TEMOIN]).cycle()
        bet = BetStore(self.tmp, "pm_temoin").open_bets()[0]
        self.assertEqual(bet["coin"], "#1040")
        self.assertEqual(bet["entry_price"], 0.82)
        self.assertEqual(bet["entry_time"], "2026-11-16T13:00:00+00:00")
        self.assertEqual(bet["arm"], "pm_temoin")

    def test_un_meme_marche_n_est_parie_qu_une_fois_par_bras(self):
        api = FakeApi(make_meta(), {"#1040": 0.80, "#1041": 0.20},
                      books={"#1040": {"bids": [(0.79, 500)], "asks": [(0.82, 500)]}})
        runner = self.runner(api, [TEMOIN])
        runner.cycle()
        self.clock[0] = NOW + timedelta(minutes=1)
        runner.cycle()
        self.assertEqual(len(BetStore(self.tmp, "pm_temoin").open_bets()), 1)

    def test_sans_prix_du_sous_jacent_le_bras_parieur_ne_parie_pas(self):
        api = FakeApi(make_meta(), {"#1040": 0.30, "#1041": 0.70},
                      books={"#1040": {"bids": [(0.29, 500)], "asks": [(0.31, 500)]}},
                      candles=flat_candles(64100, 300))
        result = self.runner(api, [VOL]).cycle()
        self.assertEqual(BetStore(self.tmp, "pm_vol_edge").open_bets(), [])
        self.assertEqual(result["raisons"]["pm_vol_edge:prix_sous_jacent_absent"], 1)

    def test_sans_bougies_le_bras_parieur_ne_parie_pas(self):
        api = FakeApi(make_meta(), {"#1040": 0.30, "#1041": 0.70, "BTC": 64100})
        result = self.runner(api, [VOL]).cycle()
        self.assertEqual(result["raisons"]["pm_vol_edge:historique_insuffisant"], 1)
        self.assertEqual(api.book_calls, 0)

    def test_le_bras_parieur_entre_quand_son_estimation_depasse_le_prix_frais_compris(self):
        # Prix immobile à 64 100, seuil 64 000 : « Oui » estimé à 100 %, vendu 0,60.
        api = FakeApi(make_meta(), {"#1040": 0.58, "#1041": 0.42, "BTC": 64100},
                      books={"#1040": {"bids": [(0.56, 500)], "asks": [(0.60, 500)]}},
                      candles=flat_candles(64100, 300))
        self.runner(api, [VOL]).cycle()
        bet = BetStore(self.tmp, "pm_vol_edge").open_bets()[0]
        self.assertEqual((bet["side_name"], bet["entry_price"], bet["prob_estimee"]), ("Yes", 0.6, 1.0))

    def test_une_esperance_qui_disparait_au_prix_vendeur_reel_annule_l_entree(self):
        # Le milieu (0,58) laisse une marge, le carnet vendeur (0,99) non.
        api = FakeApi(make_meta(), {"#1040": 0.58, "#1041": 0.42, "BTC": 64100},
                      books={"#1040": {"bids": [(0.17, 500)], "asks": [(0.99, 500)]}},
                      candles=flat_candles(64100, 300))
        self.runner(api, [VOL]).cycle()
        self.assertEqual(BetStore(self.tmp, "pm_vol_edge").open_bets(), [])

    def test_l_exposition_par_domaine_plafonne_le_nombre_de_paris_ouverts(self):
        meta = {"outcomes": [dict(make_meta()["outcomes"][0], outcome=100 + i) for i in range(8)],
                "questions": []}
        mids, books = {}, {}
        for i in range(8):
            mids[f"#{(100 + i) * 10}"], mids[f"#{(100 + i) * 10 + 1}"] = 0.8, 0.2
            books[f"#{(100 + i) * 10}"] = {"bids": [(0.79, 500)], "asks": [(0.81, 500)]}
        self.runner(FakeApi(meta, mids, books), [TEMOIN]).cycle()
        # 2 % par pari, plafond 10 % : cinq paris, pas huit.
        self.assertEqual(len(BetStore(self.tmp, "pm_temoin").open_bets()), 5)

    def test_api_indisponible_ne_regle_aucun_pari(self):
        api = FakeApi(make_meta(), {"#1040": 0.80, "#1041": 0.20},
                      books={"#1040": {"bids": [(0.79, 500)], "asks": [(0.82, 500)]}})
        runner = self.runner(api, [TEMOIN])
        runner.cycle()
        api.meta = None
        self.clock[0] = NOW + timedelta(hours=3)
        self.assertFalse(runner.cycle()["ok"])
        self.assertEqual(len(BetStore(self.tmp, "pm_temoin").open_bets()), 1)


class TestReglement(PredictionTestCase):
    def _pari_ouvert(self, candles=None):
        api = FakeApi(make_meta(), {"#1040": 0.80, "#1041": 0.20, "BTC": 64100},
                      books={"#1040": {"bids": [(0.79, 500)], "asks": [(0.80, 500)]}},
                      candles=candles)
        runner = self.runner(api, [TEMOIN])
        runner.cycle()
        return api, runner

    def test_un_pari_gagne_est_regle_a_un_avec_son_pnl_net_de_frais(self):
        expiry = datetime(2026, 11, 16, 13, 15, tzinfo=timezone.utc)
        api, runner = self._pari_ouvert(flat_candles(64500, 30, end=expiry))
        api.meta = {"outcomes": [], "questions": []}
        api.mids = {"BTC": 64500}
        self.clock[0] = expiry + timedelta(minutes=1)
        runner.cycle()
        store = BetStore(self.tmp, "pm_temoin")
        self.assertEqual(store.open_bets(), [])
        bet = store.settled()[0]
        self.assertEqual((bet["status"], bet["settle_value"], bet["won"]), ("regle", 1.0, True))
        self.assertAlmostEqual(bet["pnl_net"], 25 * 0.991 - 20.18, places=5)

    def test_un_prix_colle_au_seuil_donne_un_pari_indetermine_pas_un_resultat_devine(self):
        expiry = datetime(2026, 11, 16, 13, 15, tzinfo=timezone.utc)
        api, runner = self._pari_ouvert(flat_candles(64001, 30, end=expiry))
        api.meta = {"outcomes": [], "questions": []}
        api.mids = {"BTC": 64001}
        self.clock[0] = expiry + timedelta(minutes=1)
        runner.cycle()
        bet = BetStore(self.tmp, "pm_temoin").settled()[0]
        self.assertEqual(bet["status"], "indetermine")
        self.assertIsNone(bet["pnl_net"])

    def test_un_pari_non_echu_reste_ouvert(self):
        api, runner = self._pari_ouvert()
        self.clock[0] = NOW + timedelta(minutes=5)
        runner.cycle()
        self.assertEqual(len(BetStore(self.tmp, "pm_temoin").open_bets()), 1)


class TestStatistiques(unittest.TestCase):
    def _bet(self, pnl, won, minute, status="regle"):
        return {"status": status, "pnl_net": pnl, "won": won, "entry_price": 0.6,
                "prob_estimee": 0.7, "settle_time": f"2026-11-16T13:{minute:02d}:00+00:00"}

    def test_les_paris_indetermines_n_entrent_dans_aucun_chiffre(self):
        stats = compute_stats([self._bet(10, True, 1), self._bet(None, None, 2, "indetermine")], 1000, 30)
        self.assertEqual((stats["paris_regles"], stats["paris_indetermines"]), (1, 1))
        self.assertEqual(stats["pnl_net"], 10)

    def test_sous_le_seuil_le_verdict_est_a_suivre(self):
        stats = compute_stats([self._bet(10, True, 1)], 1000, 30)
        self.assertEqual(stats["verdict"], "à suivre")

    def test_le_drawdown_est_mesure_du_plus_haut_au_plus_bas_dans_l_ordre_du_temps(self):
        bets = [self._bet(30, True, 1), self._bet(-20, False, 2), self._bet(-20, False, 3), self._bet(5, True, 4)]
        stats = compute_stats(bets, 1000, 30)
        self.assertEqual(stats["drawdown_max_pct"], 4.0)
        self.assertAlmostEqual(stats["profit_factor"], 35 / 40)

    def test_aucun_pari_ne_donne_pas_un_win_rate_de_zero(self):
        stats = compute_stats([], 1000, 30)
        self.assertIsNone(stats["win_rate"])
        self.assertIsNone(stats["profit_factor"])


class TestApiPrediction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = {k: getattr(settings, k) for k in
                       ("PREDICTION_ARMS_PATH", "PREDICTION_DATA_DIR", "PREDICTION_DEMO_DIR")}
        settings.PREDICTION_ARMS_PATH = os.path.join(self.tmp, "prediction_arms.json")
        settings.PREDICTION_DATA_DIR = os.path.join(self.tmp, "reel")
        settings.PREDICTION_DEMO_DIR = os.path.join(self.tmp, "demo")
        with open(settings.PREDICTION_ARMS_PATH, "w", encoding="utf-8") as fh:
            json.dump(make_config([TEMOIN, VOL]), fh)
        import importlib

        import api.server as server_module
        importlib.reload(server_module)
        self.server = server_module

    def tearDown(self):
        for key, value in self._saved.items():
            setattr(settings, key, value)

    def _ecrire(self, directory, arm, bet):
        path = BetStore(directory, arm).settled_path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(bet) + "\n")

    def test_chaque_pari_servi_porte_son_bras(self):
        self._ecrire(settings.PREDICTION_DATA_DIR, "pm_temoin",
                     {"id": "a", "entry_time": "2026-11-16T13:00:00+00:00", "status": "regle"})
        self._ecrire(settings.PREDICTION_DATA_DIR, "pm_vol_edge",
                     {"id": "b", "entry_time": "2026-11-16T12:00:00+00:00", "status": "regle"})
        body = self.server.get_prediction_bets(arm="all")
        self.assertEqual([(b["id"], b["arm"]) for b in body["bets"]],
                         [("b", "pm_vol_edge"), ("a", "pm_temoin")])

    def test_l_exemple_fictif_ne_se_melange_jamais_au_reel(self):
        self._ecrire(settings.PREDICTION_DEMO_DIR, "pm_temoin",
                     {"id": "fictif", "entry_time": "2026-11-16T13:00:00+00:00", "status": "regle"})
        self.assertEqual(self.server.get_prediction_bets(arm="all")["bets"], [])
        demo = self.server.get_prediction_bets(arm="all", demo=True)
        self.assertTrue(demo["exemple_fictif"])
        self.assertTrue(all(b["exemple_fictif"] for b in demo["bets"]))

    def test_un_bras_inconnu_renvoie_404_plutot_que_les_paris_d_un_autre(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            self.server.get_prediction_bets(arm="../baseline")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_un_nom_de_piece_ne_peut_pas_sortir_du_dossier_des_prix(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            self.server.get_prediction_prices(coin="../../state")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_le_resume_donne_une_ligne_par_bras_et_dit_que_le_processus_est_arrete(self):
        body = self.server.get_prediction_summary()
        self.assertEqual([a["arm"] for a in body["arms"]], ["pm_temoin", "pm_vol_edge"])
        self.assertFalse(body["runner_online"])
        self.assertEqual(body["mode"], "PAPER")


if __name__ == "__main__":
    unittest.main()
