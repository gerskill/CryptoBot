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
from src.prediction import estimators, learning, overlay  # noqa: E402
from src.prediction.markets import parse_expiry, parse_outcome_meta, settled_outcomes  # noqa: E402
from src.prediction.paper import (BetStore, account, buy_shares, compute_stats, min_shares,  # noqa: E402
                                  net_pnl, remaining_asks, simulate_entry)
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
          "domaines": ["crypto_seuil", "sport_match", "evenement"], "prix_min": 0.5, "prix_max": 0.95,
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
        self.assertEqual(by_id[2].domain, "autre")

    def test_une_reponse_vide_ou_mal_formee_donne_zero_marche_sans_planter(self):
        self.assertEqual(parse_outcome_meta(None), [])
        self.assertEqual(parse_outcome_meta({"outcomes": [{"name": "sans identifiant"}]}), [])


FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "outcome_meta_2026-11-16.json")


def _lire(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def vraie_reponse():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


class TestFormesReellesDeLApi(unittest.TestCase):
    """Verrouille la lecture sur un extrait de la réponse réelle du 2026-11-16.

    La première version ne lisait que l'écriture `class:priceBinary` : sur
    l'API réelle elle voyait 4 marchés sur 309.
    """

    def setUp(self):
        self.by_id = {m.outcome: m for m in parse_outcome_meta(vraie_reponse())}

    def test_un_seuil_ecrit_en_modele_binaryPrice_est_lu_avec_son_seuil_et_son_echeance(self):
        market = self.by_id[34367]
        self.assertEqual((market.domain, market.underlying, market.target), ("crypto_seuil", "BTC", 66000))
        self.assertEqual(market.expiry, datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc))
        self.assertEqual(market.venue, "skew")

    def test_une_piece_xyz_n_est_jamais_rangee_en_crypto(self):
        self.assertEqual(self.by_id[36884].domain, "actions_seuil")
        self.assertEqual(self.by_id[36884].underlying, "xyz:NVDA")
        self.assertEqual(self.by_id[36884].symbol, "NVDA")
        self.assertEqual(self.by_id[23733].domain, "matieres_seuil")

    def test_un_marche_touche_est_une_barriere(self):
        self.assertEqual((self.by_id[22093].domain, self.by_id[22093].target), ("barriere", 100))

    def test_les_issues_d_un_match_portent_le_match_le_coup_d_envoi_et_la_meme_question(self):
        ireland, nul = self.by_id[35431], self.by_id[35432]
        self.assertEqual(ireland.domain, "sport_match")
        self.assertEqual(ireland.title, "Ireland vs Kosovo — UEFA Nations League — Ireland")
        self.assertEqual(nul.label, "Nul")
        self.assertEqual(ireland.question, nul.question)
        self.assertEqual(ireland.start, datetime(2026, 11, 17, 19, 45, tzinfo=timezone.utc))

    def test_deux_questions_de_meme_modele_ne_sont_pas_confondues(self):
        self.assertNotEqual(self.by_id[35431].question, self.by_id[1473].question)

    def test_un_match_a_deux_cotes_nomme_ses_cotes_par_les_equipes(self):
        self.assertEqual([s.name for s in self.by_id[35446].sides], ["DEN", "PHX"])
        self.assertEqual(self.by_id[35446].domain, "sport_match")

    def test_tournoi_taux_et_introduction_en_bourse_ont_chacun_leur_domaine(self):
        self.assertEqual(self.by_id[1473].domain, "sport_tournoi")
        self.assertEqual(self.by_id[19989].domain, "evenement")
        self.assertEqual(self.by_id[19989].label, "baisse")
        self.assertEqual((self.by_id[19821].domain, self.by_id[19821].target), ("evenement", 1500))

    def test_tous_les_replis_sont_marques_meme_sans_passer_par_la_question(self):
        for outcome in (35430, 1472, 19987, 37240):
            self.assertTrue(self.by_id[outcome].is_fallback, outcome)

    def test_aucun_marche_reel_de_l_extrait_ne_reste_sans_domaine(self):
        sans = [m.outcome for m in self.by_id.values() if m.domain == "autre" and not m.is_fallback]
        self.assertEqual(sans, [])

    def test_un_modele_inconnu_est_range_en_autre_et_pas_devine(self):
        meta = {"outcomes": [{"outcome": 9, "name": "template:meteoDemain", "description": "ville:Lausanne"}]}
        self.assertEqual(parse_outcome_meta(meta)[0].domain, "autre")

    def test_sans_reglement_officiel_aucune_issue_n_est_declaree(self):
        self.assertEqual(settled_outcomes(vraie_reponse()), {})


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


class TestBrasParCategorie(PredictionTestCase):
    SPORT = {"name": "pm_sport_favori", "role": "regle", "enabled": True, "domaines": ["sport_match"],
             "cote": "favori", "prix_min": 0.6, "prix_max": 0.9, "min_minutes": 10, "max_jours": 7}

    def test_un_marche_affiche_a_50_avec_un_carnet_vide_au_milieu_n_est_pas_achete(self):
        # Relevé réel : milieu 0,50, carnet 0,0101 / 0,9899.
        api = FakeApi(make_meta(), {"#1040": 0.5, "#1041": 0.5},
                      books={"#1040": {"bids": [(0.0101, 200)], "asks": [(0.9899, 200)]}})
        result = self.runner(api, [TEMOIN]).cycle()
        self.assertEqual(BetStore(self.tmp, "pm_temoin").open_bets(), [])
        self.assertEqual(result["raisons"].get("pm_temoin:carnet_trop_large"), 1)

    def test_le_bras_sport_ne_parie_que_sur_le_sport_et_avant_le_coup_d_envoi(self):
        mids = {"#354310": 0.70, "#354311": 0.30, "#343670": 0.70, "#343671": 0.30}
        books = {"#354310": {"bids": [(0.69, 500)], "asks": [(0.71, 500)]},
                 "#343670": {"bids": [(0.69, 500)], "asks": [(0.71, 500)]}}
        self.runner(FakeApi(vraie_reponse(), mids, books=books), [self.SPORT]).cycle()
        bets = BetStore(self.tmp, "pm_sport_favori").open_bets()
        self.assertEqual([b["domain"] for b in bets], ["sport_match"])
        self.assertEqual(bets[0]["famille"], "sport")
        # Après le coup d'envoi (17 nov. 19:45 UTC) : plus d'entrée.
        self.tmp = tempfile.mkdtemp()
        self.clock[0] = datetime(2026, 11, 17, 20, 0, tzinfo=timezone.utc)
        self.runner(FakeApi(vraie_reponse(), mids, books=books), [self.SPORT]).cycle()
        self.assertEqual(BetStore(self.tmp, "pm_sport_favori").open_bets(), [])

    def test_le_fork_outsider_achete_le_cote_le_moins_cher(self):
        arm = dict(self.SPORT, name="pm_sport_outsider", cote="outsider", prix_min=0.1, prix_max=0.4)
        api = FakeApi(vraie_reponse(), {"#354460": 0.78, "#354461": 0.22},
                      books={"#354461": {"bids": [(0.21, 500)], "asks": [(0.23, 500)]}})
        self.runner(api, [arm]).cycle()
        bet = BetStore(self.tmp, "pm_sport_outsider").open_bets()[0]
        self.assertEqual((bet["coin"], bet["side_name"]), ("#354461", "PHX"))

    def test_une_action_xyz_prend_son_prix_dans_les_bougies_faute_de_prix_milieu(self):
        self.clock[0] = datetime(2026, 11, 16, 20, 0, tzinfo=timezone.utc)
        arm = dict(VOL, name="pm_cloture_actions", domaines=["actions_seuil"])
        api = FakeApi(vraie_reponse(), {"#368840": 0.5, "#368841": 0.5},
                      candles=flat_candles(157.5, 300, end=self.clock[0]))
        result = self.runner(api, [arm]).cycle()
        self.assertNotIn("pm_cloture_actions:prix_sous_jacent_absent", result["raisons"])

    def test_le_catalogue_enregistre_tous_les_marches_sauf_les_replis(self):
        self.runner(FakeApi(vraie_reponse(), {"#343670": 0.4}), []).cycle()
        with open(os.path.join(self.tmp, "catalogue.json"), encoding="utf-8") as fh:
            rows = json.load(fh)["markets"]
        self.assertEqual(len(rows), 27)
        self.assertEqual({r["famille"] for r in rows}, {"prix", "sport", "questions"})
        btc = next(r for r in rows if r["outcome"] == 34367)
        self.assertEqual(btc["sides"][0]["mid"], 0.4)

    def test_l_instantane_des_prix_n_est_ecrit_qu_une_fois_par_intervalle(self):
        runner = self.runner(FakeApi(vraie_reponse(), {"#343670": 0.4}), [])
        self.assertTrue(runner.cycle()["instantane_ecrit"])
        self.clock[0] = NOW + timedelta(minutes=1)
        self.assertFalse(runner.cycle()["instantane_ecrit"])
        self.clock[0] = NOW + timedelta(minutes=16)
        self.assertTrue(runner.cycle()["instantane_ecrit"])


class TestIncoherences(PredictionTestCase):
    OBS = {"name": "pm_coherence", "role": "observateur", "enabled": True, "domaines": []}

    def _lignes(self, mids):
        self.runner(FakeApi(vraie_reponse(), mids), [self.OBS]).cycle()
        path = os.path.join(self.tmp, "coherence.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh]

    def test_un_match_dont_les_trois_issues_somment_a_100_n_est_pas_signale(self):
        self.assertEqual(self._lignes({"#354310": 0.455, "#354320": 0.302, "#354330": 0.243}), [])

    def test_un_match_dont_les_issues_somment_sous_100_est_signale_avec_son_libelle(self):
        rows = self._lignes({"#354310": 0.40, "#354320": 0.25, "#354330": 0.20})
        self.assertEqual([r["type"] for r in rows], ["somme_sous_100"])
        self.assertEqual(rows[0]["libelle"], "Ireland vs Kosovo — UEFA Nations League")

    def test_un_seuil_plus_haut_cote_plus_cher_qu_un_seuil_plus_bas_est_signale(self):
        rows = self._lignes({"#198210": 0.30, "#198230": 0.45})
        self.assertEqual([r["type"] for r in rows], ["echelle_non_monotone"])
        self.assertEqual((rows[0]["seuil_bas"], rows[0]["seuil_haut"]), (1500, 2000))

    def test_un_marche_non_cote_affiche_a_50_pile_ne_cree_pas_d_incoherence(self):
        # Relevé réel : seuil haut « à 49,96 % » faute de cotation, seuil bas à 12 %.
        self.assertEqual(self._lignes({"#198210": 0.1245, "#198230": 0.49958}), [])

    def test_un_tournoi_dont_les_equipes_listees_somment_sous_100_n_est_pas_signale(self):
        # Les autres équipes sont dans le marché de repli.
        self.assertEqual(self._lignes({f"#{o}0": 0.06 for o in range(1473, 1479)}), [])

    def test_un_marche_echu_ne_sert_plus_a_relever_une_incoherence(self):
        self.clock[0] = datetime(2027, 6, 1, tzinfo=timezone.utc)
        self.assertEqual(self._lignes({"#198210": 0.30, "#198230": 0.45}), [])

    def test_un_prix_a_50_sans_cotation_reelle_des_deux_cotes_ne_compte_pas_comme_ecart_de_venue(self):
        # 19821 (skew) et 8573 (out) ont le même seuil mais pas la même échéance.
        self.assertEqual(self._lignes({"#198210": 0.30, "#85730": 0.60}), [])


class TestDemandesDepuisLeSite(PredictionTestCase):
    """Le site demande, la simulation valide et applique. Jamais l'inverse."""

    SPORT = dict(TestBrasParCategorie.SPORT)

    def _demander(self, **request):
        request.setdefault("id", f"d{len(overlay.request_status(self.tmp, 500))}")
        overlay.append_jsonl(overlay.requests_path(self.tmp), request)

    def _cycle(self, arms, mids=None, books=None):
        runner = self.runner(FakeApi(vraie_reponse(), mids or {}, books=books), arms)
        return runner, runner.cycle()

    def test_un_fork_ne_differe_de_son_parent_que_par_la_variable_demandee(self):
        self._demander(action="fork", parent="pm_sport_favori", name="pm_sport_favori_70",
                       variable="fourchette", value={"prix_min": 0.7, "prix_max": 0.9})
        runner, _ = self._cycle([self.SPORT])
        fork = next(a for a in runner.config["arms"] if a["name"] == "pm_sport_favori_70")
        differences = {k for k in set(fork) | set(self.SPORT)
                       if fork.get(k) != self.SPORT.get(k)} - {"name", "parent", "variable", "description",
                                                              "cree_depuis_le_site", "origine"}
        self.assertEqual(differences, {"prix_min"})
        self.assertEqual((fork["parent"], fork["variable"]), ("pm_sport_favori", "prix_min/prix_max"))

    def test_une_valeur_hors_bornes_est_refusee_et_le_refus_est_journalise(self):
        self._demander(action="fork", parent="pm_vol_edge", name="pm_vol_fou",
                       variable="edge_min", value=0.9)
        runner, _ = self._cycle([VOL])
        self.assertEqual([a["name"] for a in runner.config["arms"]], ["pm_vol_edge"])
        status = overlay.request_status(self.tmp)[0]
        self.assertEqual((status["statut"], status["raison"]), ("refusée", "edge_min_hors_bornes"))

    def test_une_variable_qui_n_appartient_pas_au_role_du_parent_est_refusee(self):
        self._demander(action="fork", parent="pm_sport_favori", name="pm_sport_edge",
                       variable="edge_min", value=0.1)
        self._cycle([self.SPORT])
        self.assertEqual(overlay.request_status(self.tmp)[0]["raison"],
                         "variable_non_autorisee_pour_ce_role")

    def test_un_nom_qui_sortirait_du_dossier_des_bras_est_refuse(self):
        self._demander(action="fork", parent="pm_sport_favori", name="pm_../../etc",
                       variable="cote", value="outsider")
        self._cycle([self.SPORT])
        self.assertEqual(overlay.request_status(self.tmp)[0]["raison"], "nom_invalide")

    def test_une_demande_n_est_appliquee_qu_une_fois(self):
        self._demander(action="fork", parent="pm_sport_favori", name="pm_sport_out",
                       variable="cote", value="outsider")
        runner, first = self._cycle([self.SPORT])
        self.assertEqual(first["demandes_traitees"], 1)
        self.assertEqual(runner.cycle()["demandes_traitees"], 0)
        self.assertEqual(len(overlay.read_overlay(self.tmp)["forks"]), 1)

    def test_le_fichier_de_configuration_versionne_n_est_jamais_modifie(self):
        base = make_config([dict(self.SPORT)])
        avant = json.dumps(base, sort_keys=True)
        self._demander(action="toggle", arm="pm_sport_favori", enabled=False)
        PredictionRunner(FakeApi(vraie_reponse(), {}), base, self.tmp, now=lambda: NOW).cycle()
        self.assertEqual(json.dumps(base, sort_keys=True), avant)

    def test_un_bras_desactive_ne_parie_plus_mais_ses_paris_ouverts_sont_encore_regles(self):
        mids = {"#354310": 0.70, "#354311": 0.30}
        books = {"#354310": {"bids": [(0.69, 500)], "asks": [(0.71, 500)]}}
        self._cycle([self.SPORT], mids, books)
        self.assertEqual(len(BetStore(self.tmp, "pm_sport_favori").open_bets()), 1)
        self._demander(action="toggle", arm="pm_sport_favori", enabled=False)
        # Le match est réglé : son issue disparaît de la liste, dernier prix à 99 %.
        meta = vraie_reponse()
        meta["outcomes"] = [o for o in meta["outcomes"] if o["outcome"] != 35431]
        self.clock[0] = NOW + timedelta(days=2)
        store = BetStore(self.tmp, "pm_sport_favori")
        overlay.append_jsonl(os.path.join(self.tmp, "prices", "354310.jsonl"),
                             {"t": "2026-11-18T12:00:00+00:00", "mid": 0.99})
        runner = self.runner(FakeApi(meta, {}, books=books), [self.SPORT])
        result = runner.cycle()
        self.assertEqual(result["regles_ce_cycle"], 1)
        self.assertEqual(store.open_bets(), [])
        self.assertTrue(store.settled()[0]["won"])
        self.assertFalse(next(a for a in runner.config["arms"] if a["name"] == "pm_sport_favori")["enabled"])


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

    def test_la_calibration_range_chaque_pari_dans_la_tranche_de_son_prix_paye(self):
        bets = [dict(self._bet(4, True, 1), entry_price=0.85, domain="sport_match"),
                dict(self._bet(-20, False, 2), entry_price=0.82, domain="sport_match"),
                dict(self._bet(9, True, 3), entry_price=0.55, domain="crypto_seuil")]
        stats = compute_stats(bets, 1000, 30)
        haut = next(r for r in stats["calibration"] if r["tranche"] == "80–100 %")
        self.assertEqual((haut["paris"], haut["part_gagnee"]), (2, 0.5))
        self.assertEqual(stats["par_domaine"]["sport_match"], {"paris": 2, "gagnes": 1, "pnl_net": -16.0})

    def test_aucun_pari_ne_donne_pas_un_win_rate_de_zero(self):
        stats = compute_stats([], 1000, 30)
        self.assertIsNone(stats["win_rate"])
        self.assertIsNone(stats["profit_factor"])


COMPTE_46 = {"capital_fictif_usd": 46.0, "mise_pct": 0.22, "mise_max_pct": 0.25,
             "exposition_max_pct_par_domaine": 1.0, "ordre_min_usd": 10.0,
             "regle_taille_min": "min_cote", "parts_entieres": True, "delai_prudence_heures": 24}


class TestCompteReel(PredictionTestCase):
    """Le papier se comporte comme le vrai compte de 46 $ (ADR 018)."""

    def runner(self, api, arms, **extra):
        config = dict(make_config(arms), **COMPTE_46)
        config.update(extra)
        return PredictionRunner(api, config, self.tmp, now=lambda: self.clock[0])

    def _marches(self, n, ask=0.5, size=500):
        meta = {"outcomes": [dict(make_meta()["outcomes"][0], outcome=100 + i) for i in range(n)],
                "questions": []}
        mids, books = {}, {}
        for i in range(n):
            mids[f"#{(100 + i) * 10}"], mids[f"#{(100 + i) * 10 + 1}"] = ask, 1 - ask
            books[f"#{(100 + i) * 10}"] = {"bids": [(ask - 0.01, size)], "asks": [(ask, size)]}
        return meta, mids, books

    def test_la_taille_minimale_suit_le_cote_le_moins_cher_et_arrondit_au_dessus(self):
        self.assertEqual(min_shares(0.5, 10), 20)
        self.assertEqual(min_shares(0.9, 10), 100)   # 100 × 0,10 = 10 $ : coûte 90 $
        self.assertEqual(min_shares(0.9, 10, "notionnel"), 12)
        self.assertEqual(min_shares(0.5, 0), 0)

    def test_un_favori_a_090_est_refuse_parce_que_le_minimum_coute_90_dollars(self):
        meta, mids, books = self._marches(1, ask=0.9)
        result = self.runner(FakeApi(meta, mids, books), [TEMOIN]).cycle()
        self.assertEqual(BetStore(self.tmp, "pm_temoin").open_bets(), [])
        self.assertEqual(result["raisons"]["pm_temoin:sous_taille_minimale"], 1)

    def test_un_pari_a_050_achete_des_parts_entieres_au_moins_le_minimum(self):
        meta, mids, books = self._marches(1, ask=0.5)
        self.runner(FakeApi(meta, mids, books), [TEMOIN]).cycle()
        bet = BetStore(self.tmp, "pm_temoin").open_bets()[0]
        # 46 × 0,22 = 10,12 $ → 20 parts entières à 0,50 = 10 $ ; minimum 20 parts.
        self.assertEqual((bet["shares"], bet["stake_usd"], bet["entry_price"]), (20, 10.0, 0.5))
        self.assertAlmostEqual(bet["fee_entry_usd"], 0.09)

    def test_le_solde_refuse_un_pari_que_le_compte_ne_peut_plus_payer(self):
        meta, mids, books = self._marches(6, ask=0.5)
        # Plafond d'exposition relevé : c'est le SOLDE qui doit refuser ici.
        result = self.runner(FakeApi(meta, mids, books), [TEMOIN],
                             exposition_max_pct_par_domaine=2.0).cycle()
        # 4 paris × 10,09 $ = 40,36 $ engagés ; le 5e demande 10,09 $ pour 5,64 $ de solde.
        self.assertEqual(len(BetStore(self.tmp, "pm_temoin").open_bets()), 4)
        self.assertEqual(result["raisons"]["pm_temoin:solde_insuffisant"], 2)
        compte = account(46.0, [], BetStore(self.tmp, "pm_temoin").open_bets())
        self.assertAlmostEqual(compte["solde"], 46 - 4 * 10.09, places=6)

    def test_deux_bras_ne_peuvent_pas_acheter_les_memes_parts_du_carnet(self):
        meta, mids, _ = self._marches(1, ask=0.5)
        books = {"#1000": {"bids": [(0.49, 500)], "asks": [(0.5, 25), (0.52, 500)]}}
        autre = dict(TEMOIN, name="pm_temoin_bis")
        self.runner(FakeApi(meta, mids, books), [TEMOIN, autre]).cycle()
        premier = BetStore(self.tmp, "pm_temoin").open_bets()[0]
        second = BetStore(self.tmp, "pm_temoin_bis").open_bets()[0]
        self.assertEqual(premier["entry_price"], 0.5)
        # Il ne restait que 5 parts à 0,50 : les 15 autres à 0,52.
        self.assertAlmostEqual(second["entry_price"], (5 * 0.5 + 15 * 0.52) / 20)

    def test_le_carnet_retire_d_abord_les_meilleurs_prix_deja_pris(self):
        self.assertEqual(remaining_asks([(0.5, 25), (0.52, 10)], 30), [(0.52, 5)])
        self.assertEqual(buy_shares([(0.5, 5), (0.6, 5)], 8)["cost"], 2.5 + 1.8)
        self.assertIsNone(buy_shares([(0.5, 5)], 8))

    def test_une_issue_illisible_reste_ouverte_puis_est_comptee_perdue_par_prudence(self):
        meta, mids, books = self._marches(1, ask=0.5)
        api = FakeApi(meta, mids, books)
        runner = self.runner(api, [TEMOIN])
        runner.cycle()
        # Marché disparu, aucune bougie, dernier prix connu à 0,50 : illisible.
        api.meta, api.mids = {"outcomes": [], "questions": []}, {}
        self.clock[0] = NOW + timedelta(hours=2)
        runner.cycle()
        store = BetStore(self.tmp, "pm_temoin")
        self.assertEqual(store.settled(), [])
        self.assertIsNotNone(store.open_bets()[0]["a_regler_depuis"])
        self.clock[0] = NOW + timedelta(hours=27)
        runner.cycle()
        bet = store.settled()[0]
        self.assertEqual((bet["status"], bet["won"], bet["regle_par_prudence"]), ("regle", False, True))
        self.assertAlmostEqual(bet["pnl_net"], -10.0 * 1.009, places=6)
        self.assertEqual(compute_stats(store.settled(), 46, 30)["regles_par_prudence"], 1)

    def test_le_compte_rend_la_recette_du_reglement_au_solde(self):
        settled = [{"status": "regle", "pnl_net": 9.5}, {"status": "regle", "pnl_net": -10.09}]
        compte = account(46.0, settled, [{"stake_usd": 10.0, "fee_entry_usd": 0.09}])
        self.assertAlmostEqual(compte["valeur"], 45.41)
        self.assertAlmostEqual(compte["solde"], 35.32)


class TestOrdreEtGagnesNets(unittest.TestCase):
    def test_le_drawdown_ne_depend_pas_de_l_ordre_rendu_par_l_api(self):
        bets = [{"id": c, "status": "regle", "pnl_net": p, "won": p > 0, "entry_price": 0.5,
                 "settle_time": "2026-11-17T05:00:00+00:00", "entry_time": f"2026-11-17T04:0{i}:00+00:00"}
                for i, (c, p) in enumerate([("a", 10), ("b", -20), ("c", -20), ("d", 30)])]
        premier = compute_stats(bets, 46, 30)["drawdown_max_pct"]
        self.assertEqual(compute_stats(list(reversed(bets)), 46, 30)["drawdown_max_pct"], premier)

    def test_un_pari_gagne_mais_negatif_apres_frais_n_est_pas_un_gain_net(self):
        bets = [{"status": "regle", "pnl_net": -0.08, "won": True, "settle_time": "1", "entry_price": 0.99},
                {"status": "regle", "pnl_net": 5.0, "won": True, "settle_time": "2", "entry_price": 0.6}]
        stats = compute_stats(bets, 46, 30)
        self.assertEqual((stats["gagnes"], stats["gagnes_net"], stats["pertes_reelles"]), (2, 1, 0))


class TestAgentApprentissage(PredictionTestCase):
    """L'agent mesure chaque bras et ne propose que des forks resserrés."""

    REGLAGE = {"actif": True, "intervalle_minutes": 60, "min_segment": 10, "min_bras": 20,
               "max_forks_par_bras_par_jour": 1, "max_forks_total": 10}

    def _config(self, arms):
        return dict(make_config(arms), apprentissage=dict(self.REGLAGE))

    def _paris(self, arm, rows):
        path = BetStore(self.tmp, arm).settled_path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for i, (price, pnl, extra) in enumerate(rows):
                fh.write(json.dumps(dict({"id": f"{arm}-{i}-{price}", "status": "regle", "entry_price": price,
                                          "pnl_net": pnl, "won": pnl > 0, "domain": "crypto_seuil",
                                          "settle_time": f"2026-11-16T{i % 24:02d}:00:00+00:00"},
                                         **extra)) + "\n")

    def _demandes(self):
        return [r for r in overlay.read_jsonl(overlay.requests_path(self.tmp))
                if r.get("origine") == "apprentissage"]

    def test_sans_configuration_l_agent_ne_fait_rien(self):
        self.assertEqual(learning.run(make_config([TEMOIN]), self.tmp, NOW), {"actif": False})

    def test_sous_vingt_paris_l_agent_mesure_mais_ne_propose_rien(self):
        self._paris("pm_temoin", [(0.85, -10.0, {})] * 15)
        learning.run(self._config([TEMOIN]), self.tmp, NOW)
        diag = learning.read_diagnostic(self.tmp, "pm_temoin")
        self.assertEqual(diag["paris_regles"], 15)
        self.assertIn("pas assez de données", diag["decision"])
        self.assertEqual(self._demandes(), [])

    def test_une_tranche_perdante_au_bord_haut_donne_un_fork_qui_resserre_la_fourchette(self):
        self._paris("pm_temoin", [(0.85, -10.0, {})] * 12 + [(0.65, 4.0, {})] * 12)
        learning.run(self._config([TEMOIN]), self.tmp, NOW)
        demande = self._demandes()[0]
        self.assertEqual((demande["parent"], demande["variable"]), ("pm_temoin", "fourchette"))
        self.assertEqual(demande["value"], {"prix_min": 0.5, "prix_max": 0.8})
        # Appliquée au cycle suivant : le parent est intact, le fork ne change qu'une borne.
        runner = PredictionRunner(FakeApi(make_meta(), {}), self._config([TEMOIN]), self.tmp,
                                  now=lambda: self.clock[0])
        runner.cycle()
        parent = next(a for a in runner.config["arms"] if a["name"] == "pm_temoin")
        fork = next(a for a in runner.config["arms"] if a["name"] == demande["name"])
        self.assertEqual((parent["prix_min"], parent["prix_max"]), (0.5, 0.95))
        self.assertEqual((fork["prix_min"], fork["prix_max"], fork["origine"]), (0.5, 0.8, "apprentissage"))

    def test_une_tranche_perdante_sous_dix_paris_ne_justifie_rien(self):
        self._paris("pm_temoin", [(0.85, -10.0, {})] * 9 + [(0.65, 4.0, {})] * 15)
        learning.run(self._config([TEMOIN]), self.tmp, NOW)
        self.assertEqual(self._demandes(), [])

    def test_un_parieur_qui_perd_sur_les_faibles_esperances_recoit_un_seuil_plus_haut(self):
        self._paris("pm_vol_edge", [(0.4, -10.0, {"edge_au_carnet": 0.06})] * 12
                    + [(0.4, 8.0, {"edge_au_carnet": 0.12})] * 12)
        learning.run(self._config([VOL]), self.tmp, NOW)
        demande = self._demandes()[0]
        self.assertEqual((demande["variable"], demande["value"]), ("edge_min", 0.08))

    def test_la_meme_proposition_n_est_pas_refaite_le_lendemain(self):
        self._paris("pm_temoin", [(0.85, -10.0, {})] * 12 + [(0.65, 4.0, {})] * 12)
        learning.run(self._config([TEMOIN]), self.tmp, NOW)
        learning.run(self._config([TEMOIN]), self.tmp, NOW + timedelta(days=1))
        self.assertEqual(len(self._demandes()), 1)

    def test_l_agent_ne_repasse_pas_avant_son_intervalle(self):
        learning.run(self._config([TEMOIN]), self.tmp, NOW)
        resultat = learning.run(self._config([TEMOIN]), self.tmp, NOW + timedelta(minutes=10))
        self.assertIn("prochain_passage_dans_min", resultat)


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
        # Le jeton du site vient de `.env` : sans ceci, ces tests dépendaient de
        # la machine (401 dès que DASHBOARD_TOKEN est défini, le 2026-11-17).
        server_module.API_TOKEN = None
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

    def test_une_demande_sans_l_en_tete_du_site_est_refusee(self):
        with self.assertRaises(self.server.HTTPException) as ctx:
            self.server.post_prediction_request(
                payload={"action": "toggle", "arm": "pm_temoin", "enabled": False},
                authorization=None, x_cryptobbot=None, content_type="text/plain")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_une_demande_valide_est_deposee_en_attente_sans_toucher_a_la_configuration(self):
        avant = _lire(settings.PREDICTION_ARMS_PATH)
        body = self.server.post_prediction_request(
            payload={"action": "toggle", "arm": "pm_temoin", "enabled": False},
            authorization=None, x_cryptobbot="1", content_type="application/json")
        self.assertEqual(body["statut"], "en attente")
        self.assertEqual(self.server.get_prediction_requests()["requests"][0]["statut"], "en attente")
        self.assertEqual(_lire(settings.PREDICTION_ARMS_PATH), avant)

    def test_un_fork_hors_bornes_est_refuse_des_le_depot(self):
        with self.assertRaises(self.server.HTTPException) as ctx:
            self.server.post_prediction_request(
                payload={"action": "fork", "parent": "pm_vol_edge", "name": "pm_vol_x",
                         "variable": "edge_min", "value": 5},
                authorization=None, x_cryptobbot="1", content_type="application/json")
        self.assertEqual(ctx.exception.status_code, 422)

    def test_le_catalogue_absent_donne_une_liste_vide_pas_une_erreur(self):
        body = self.server.get_prediction_markets()
        self.assertEqual((body["total"], body["markets"], body["updated_at_iso"]), (0, [], None))
        self.assertEqual(self.server.get_liquid_perps()["total"], 0)

    def test_le_resume_donne_une_ligne_par_bras_et_dit_que_le_processus_est_arrete(self):
        body = self.server.get_prediction_summary()
        self.assertEqual([a["arm"] for a in body["arms"]], ["pm_temoin", "pm_vol_edge"])
        self.assertFalse(body["runner_online"])
        self.assertEqual(body["mode"], "PAPER")

    def test_le_resume_donne_le_compte_de_chaque_bras(self):
        body = self.server.get_prediction_summary()
        self.assertEqual(body["arms"][0]["compte"]["valeur"], 1000)

    def test_les_diagnostics_de_l_agent_portent_chacun_leur_bras(self):
        learning.run(dict(make_config([TEMOIN, VOL]), apprentissage={"actif": True}),
                     settings.PREDICTION_DATA_DIR, NOW)
        body = self.server.get_prediction_learning()
        self.assertEqual(sorted(d["arm"] for d in body["diagnostics"]), ["pm_temoin", "pm_vol_edge"])
        with self.assertRaises(self.server.HTTPException):
            self.server.get_prediction_learning(arm="pm_inconnu")


if __name__ == "__main__":
    unittest.main()
