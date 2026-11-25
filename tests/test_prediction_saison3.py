"""Bras de prédiction — saison 3 (500 $), valorisation au marché, corrections.

Ce que ces tests verrouillent :
  - un pari ouvert est valorisé à ce qu'il rapporterait VENDU au carnet
    acheteur, frais de vente déduits ; le latent reste séparé du réalisé ;
  - le drawdown lisible est rapporté au plus haut et ne dépasse jamais 100 % ;
  - la saison archivée finit ses paris ouverts sans toucher la nouvelle ;
  - une issue corrigée recalcule le P&L comme un règlement normal, avec copie ;
  - avec 500 $, la mise vise ≈ 2 % et les favoris entrent dans la limite du plafond.
Aucun test ne touche le réseau.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from src.prediction.paper import (BetStore, account, equity_drawdown, mark_bet,  # noqa: E402
                                  min_shares, net_pnl, read_json)
from src.prediction.runner import PredictionRunner, load_config, summary  # noqa: E402
from src.prediction.saisons import apply_corrections, archive_season  # noqa: E402
from tests.test_prediction import NOW, TEMOIN, FakeApi, make_config, make_meta  # noqa: E402

SAISON3 = {"capital_fictif_usd": 500.0, "mise_pct": 0.01, "mise_max_pct": 0.10,
           "exposition_max_pct_par_domaine": 0.5, "ordre_min_usd": 10.0,
           "regle_taille_min": "min_cote", "parts_entieres": True, "delai_prudence_heures": 24,
           "frais_vente_pct": 0.009}

BET = {"id": "b1", "coin": "#1000", "shares": 20.0, "stake_usd": 10.0, "fee_entry_usd": 0.09}


def settled_bet(pnl, t, **extra):
    return dict({"id": f"s{t}", "status": "regle", "pnl_net": pnl, "settle_time": f"2026-11-1{t}T00:00:00+00:00",
                 "stake_usd": 10.0, "shares": 20.0}, **extra)


class TestValorisation(unittest.TestCase):
    def test_un_pari_vaut_ce_que_le_carnet_acheteur_en_donne_frais_de_vente_deduits(self):
        mark = mark_bet(BET, {"bids": [(0.40, 15), (0.30, 100)], "asks": []}, 0.45, 0.009)
        # 15 × 0,40 + 5 × 0,30 = 7,50 $, moins 0,90 % de frais de vente.
        self.assertAlmostEqual(mark["valeur"], 7.5 * 0.991, places=6)
        self.assertEqual(mark["methode"], "carnet")
        self.assertAlmostEqual(mark["pnl_latent"], 7.5 * 0.991 - 10.09, places=6)

    def test_les_parts_que_le_carnet_n_absorbe_pas_ne_valent_rien(self):
        mark = mark_bet(BET, {"bids": [(0.5, 5)], "asks": []}, 0.5, 0.0)
        self.assertAlmostEqual(mark["valeur"], 2.5)
        self.assertEqual(mark["methode"], "carnet_partiel")

    def test_sans_acheteur_le_prix_milieu_sert_et_c_est_signale(self):
        mark = mark_bet(BET, {"bids": [], "asks": [(0.6, 10)]}, 0.55, 0.0)
        self.assertAlmostEqual(mark["valeur"], 11.0)
        self.assertEqual(mark["methode"], "milieu")

    def test_sans_aucune_donnee_le_pari_reste_au_cout_et_rien_n_est_devine(self):
        mark = mark_bet(BET, None, None, 0.009)
        self.assertEqual((mark["valeur"], mark["methode"], mark["pnl_latent"]), (10.09, "cout", 0.0))

    def test_le_compte_au_marche_garde_le_realise_a_part(self):
        marks = {"b1": {"valeur": 4.0, "methode": "carnet"}}
        compte = account(500, [settled_bet(-10.0, 7)], [BET], marks)
        self.assertEqual(compte["resultat_regle"], -10.0)        # réalisé inchangé
        self.assertAlmostEqual(compte["pnl_latent"], 4.0 - 10.09, places=4)
        self.assertAlmostEqual(compte["equite_marche"], 490 - 10.09 + 4.0, places=4)
        self.assertEqual(compte["valeur"], 490.0)                # l'ancien champ ne bouge pas
        self.assertEqual(compte["methodes_valorisation"], {"carnet": 1})

    def test_un_pari_pas_encore_valorise_compte_au_cout(self):
        compte = account(500, [], [BET], {})
        self.assertEqual(compte["pnl_latent"], 0.0)
        self.assertEqual(compte["methodes_valorisation"], {"cout": 1})

    def test_sans_valorisation_le_compte_reste_identique_a_avant(self):
        self.assertNotIn("equite_marche", account(500, [], [BET]))


class TestDrawdownLisible(unittest.TestCase):
    def test_la_baisse_est_rapportee_au_plus_haut_et_ne_depasse_jamais_100(self):
        # Cas pm_vol_edge saison 2 : +64 puis −62 sur 46 $ affichait 134 %.
        rows = [settled_bet(64.0, 1), settled_bet(-62.0, 2)]
        dd = equity_drawdown(46, rows)
        self.assertEqual(dd["plus_haut"], 110.0)
        self.assertAlmostEqual(dd["drawdown_pic_pct"], 100 * 62 / 110, places=2)
        self.assertLessEqual(dd["drawdown_pic_pct"], 100)

    def test_le_latent_du_jour_compte_dans_la_baisse(self):
        dd = equity_drawdown(500, [settled_bet(10.0, 1)], final_equity=459.0)
        self.assertAlmostEqual(dd["drawdown_pic_usd"], 51.0)
        self.assertAlmostEqual(dd["drawdown_pic_pct"], 10.0, places=3)


class Saison3Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.clock = [NOW]

    def runner(self, api, arms, **extra):
        config = dict(make_config(arms), **SAISON3)
        config.update(extra)
        return PredictionRunner(api, config, self.tmp, now=lambda: self.clock[0])

    def marche(self, ask, bid=None, size=500):
        meta = make_meta()
        mids = {"#1040": ask, "#1041": 1 - ask}
        books = {"#1040": {"bids": [((bid if bid is not None else ask - 0.01), size)], "asks": [(ask, size)]}}
        return meta, mids, books


class TestSaison3Mises(Saison3Case):
    def test_a_500_dollars_un_pari_a_50_pourcent_coute_le_minimum_soit_2_pourcent(self):
        meta, mids, books = self.marche(0.5)
        result = self.runner(FakeApi(meta, mids, books), [TEMOIN]).cycle()
        self.assertEqual(result["ouverts_ce_cycle"], 1)
        bet = BetStore(self.tmp, "pm_temoin").open_bets()[0]
        self.assertEqual(bet["shares"], 20)
        self.assertAlmostEqual(bet["stake_usd"], 10.0)            # 2 % de 500 $
        self.assertTrue(bet["taille_au_minimum"])

    def test_un_favori_a_0_70_entre_enfin(self):
        meta, mids, books = self.marche(0.70)
        result = self.runner(FakeApi(meta, mids, books), [TEMOIN]).cycle()
        self.assertEqual(result["ouverts_ce_cycle"], 1)
        bet = BetStore(self.tmp, "pm_temoin").open_bets()[0]
        self.assertEqual(bet["shares"], min_shares(0.70, 10))     # 34 parts
        self.assertLessEqual(bet["stake_usd"], 50.0)              # ≤ 10 % du compte

    def test_un_favori_a_0_80_entre_avec_le_plafond_de_10_pourcent(self):
        meta, mids, books = self.marche(0.80)
        result = self.runner(FakeApi(meta, mids, books), [TEMOIN]).cycle()
        self.assertEqual(result["ouverts_ce_cycle"], 1)
        bet = BetStore(self.tmp, "pm_temoin").open_bets()[0]
        self.assertEqual(bet["shares"], 50)                       # 50 × 0,20 = 10 $ ; coûte 40 $
        self.assertLessEqual(bet["stake_usd"], 50.0)              # ≤ 10 % du compte

    def test_un_favori_a_0_85_reste_refuse_par_le_plafond_de_10_pourcent(self):
        meta, mids, books = self.marche(0.85)
        result = self.runner(FakeApi(meta, mids, books), [TEMOIN]).cycle()
        self.assertEqual(result["raisons"]["pm_temoin:sous_taille_minimale"], 1)

    def test_un_quasi_certain_reste_refuse_avec_la_regle_min_cote(self):
        quasi = dict(TEMOIN, name="pm_quasi_certain", prix_min=0.9, prix_max=0.97)
        meta, mids, books = self.marche(0.92)
        result = self.runner(FakeApi(meta, mids, books), [quasi]).cycle()
        self.assertEqual(result["raisons"]["pm_quasi_certain:sous_taille_minimale"], 1)

    def test_le_fichier_de_config_est_bien_en_saison_3(self):
        config = load_config(settings.PREDICTION_ARMS_PATH)
        self.assertEqual(config["capital_fictif_usd"], 500.0)
        self.assertEqual(config["mise_pct"], 0.01)
        self.assertEqual(config["mise_max_pct"], 0.1)
        self.assertEqual(config["saisons_a_terminer"], ["archive/saison2_46usd"])


class TestValorisationDansLeCycle(Saison3Case):
    def test_le_cycle_ecrit_la_valeur_au_carnet_et_le_resume_l_affiche(self):
        meta, mids, books = self.marche(0.5, bid=0.40)
        api = FakeApi(meta, mids, books)
        runner = self.runner(api, [TEMOIN])
        result = runner.cycle()
        self.assertEqual(result["paris_valorises"], 1)
        marks = read_json(os.path.join(self.tmp, "marks.json"), {})
        mark = next(iter(marks["bets"].values()))
        self.assertEqual(mark["methode"], "carnet")
        self.assertAlmostEqual(mark["valeur"], 20 * 0.40 * 0.991, places=5)
        row = summary(runner.base_config, self.tmp)[0]
        self.assertAlmostEqual(row["compte"]["pnl_latent"], 20 * 0.40 * 0.991 - 10 * 1.009, places=3)
        self.assertEqual(row["compte"]["resultat_regle"], 0)
        self.assertLess(row["drawdown"]["drawdown_pic_pct"], 1.0)
        self.assertEqual(row["compte"]["valorise_le"], marks["updated_at_iso"])

    def test_le_nombre_de_carnets_lus_pour_valoriser_est_plafonne(self):
        meta, mids, books = self.marche(0.5)
        runner = self.runner(FakeApi(meta, mids, books), [TEMOIN], valorisation_carnets_max=0)
        store = BetStore(self.tmp, "pm_temoin")
        store.add(dict(BET, id="x", coin="#9990", outcome=999))
        runner._books = {}
        runner._marks([TEMOIN], {"#9990": 0.3}, NOW)
        mark = read_json(os.path.join(self.tmp, "marks.json"), {})["bets"]["x"]
        self.assertEqual(mark["methode"], "milieu")               # pas de lecture de carnet


class TestSaisonArchivee(Saison3Case):
    def _ancien_pari(self, archive):
        BetStore(archive, "pm_vol_edge").add({
            "id": "old", "arm": "pm_vol_edge", "outcome": 104, "coin": "#1040", "side_index": 0,
            "side_name": "Yes", "domain": "crypto_seuil", "underlying": "BTC", "target": 64000.0,
            "expiry": (NOW + timedelta(minutes=15)).isoformat(), "entry_time": NOW.isoformat(),
            "entry_price": 0.5, "shares": 21.0, "stake_usd": 10.5, "fee_entry_usd": 0.0945})

    def test_les_paris_ouverts_de_la_saison_archivee_finissent_dans_leur_saison(self):
        archive = os.path.join(self.tmp, "archive", "saison2_46usd")
        self._ancien_pari(archive)
        meta, mids, books = self.marche(0.98)                     # « Oui » quasi acquis
        self.clock[0] = NOW + timedelta(minutes=16)               # échu
        runner = self.runner(FakeApi(meta, {"#1040": 0.99, "#1041": 0.01}, books), [],
                             saisons_a_terminer=["archive/saison2_46usd"])
        result = runner.cycle()
        self.assertEqual(result["regles_saisons_precedentes"], 1)
        old = BetStore(archive, "pm_vol_edge")
        self.assertEqual(old.open_bets(), [])
        settled = old.settled()[0]
        self.assertTrue(settled["won"])
        self.assertAlmostEqual(settled["pnl_net"], net_pnl(10.5, 21.0, 1.0, 0.009, 0.009), places=5)
        # La saison en cours n'a rien reçu.
        self.assertEqual(BetStore(self.tmp, "pm_vol_edge").settled(), [])

    def test_un_dossier_hors_des_donnees_est_ignore(self):
        runner = self.runner(FakeApi(make_meta(), {}, {}), [], saisons_a_terminer=["../../etc"])
        self.assertEqual(runner._settle_previous_seasons({}, set(), NOW, {}), 0)


class TestArchivage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        BetStore(self.tmp, "pm_temoin").add({"id": "o1", "stake_usd": 10})
        with open(os.path.join(self.tmp, "arms", "pm_temoin", "bets.jsonl"), "w") as fh:
            fh.write(json.dumps({"id": "r1", "status": "regle", "pnl_net": -5}) + "\n")
        os.makedirs(os.path.join(self.tmp, "learning"))
        os.makedirs(os.path.join(self.tmp, "prices"))
        with open(os.path.join(self.tmp, "state.json"), "w") as fh:
            fh.write("{}")

    def test_la_simulation_ne_deplace_rien(self):
        result = archive_season(self.tmp, "saison2_46usd", 46.0, NOW)
        self.assertFalse(result["applique"])
        self.assertEqual(result["bras"]["pm_temoin"], {"regles": 1, "ouverts": 1, "resultat_regle": -5.0})
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "archive")))

    def test_l_archivage_conserve_tout_et_repart_de_comptes_neufs(self):
        archive_season(self.tmp, "saison2_46usd", 46.0, NOW, apply=True)
        target = os.path.join(self.tmp, "archive", "saison2_46usd")
        self.assertEqual(len(BetStore(target, "pm_temoin").open_bets()), 1)
        self.assertEqual(len(BetStore(target, "pm_temoin").settled()), 1)
        self.assertTrue(os.path.isdir(os.path.join(target, "learning")))
        self.assertTrue(os.path.isfile(os.path.join(target, "LISEZMOI.json")))
        self.assertEqual(os.listdir(os.path.join(self.tmp, "arms")), [])
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "prices")))   # données de marché communes

    def test_une_saison_deja_archivee_n_est_jamais_ecrasee(self):
        archive_season(self.tmp, "saison2_46usd", 46.0, NOW, apply=True)
        with self.assertRaises(FileExistsError):
            archive_season(self.tmp, "saison2_46usd", 46.0, NOW, apply=True)

    def test_un_nom_qui_sort_du_dossier_est_refuse(self):
        with self.assertRaises(ValueError):
            archive_season(self.tmp, "../x", 46.0, NOW)


class TestCorrections(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        folder = os.path.join(self.tmp, "arms", "pm_temoin")
        os.makedirs(folder)
        self.path = os.path.join(folder, "bets.jsonl")
        loss = net_pnl(10.99938, 21.0, 0.0, 0.009, 0.009)
        with open(self.path, "w") as fh:
            fh.write(json.dumps({"id": "nyk", "status": "regle", "stake_usd": 10.99938, "shares": 21.0,
                                 "settle_value": 0.0, "pnl_net": loss, "won": False, "side_name": "NYK",
                                 "regle_par_prudence": True, "motif_reglement": "prudence"}) + "\n")
            fh.write(json.dumps({"id": "autre", "status": "regle", "pnl_net": 3.0}) + "\n")

    def test_la_simulation_n_ecrit_rien(self):
        before = open(self.path).read()
        report = apply_corrections(self.tmp, [{"id": "nyk", "valeur_cote": 1, "source": "NBA"}], 0.009, 0.009)
        self.assertEqual(open(self.path).read(), before)
        self.assertAlmostEqual(report[0]["pnl_apres"], 21 * 0.991 - 10.99938 * 1.009, places=3)

    def test_une_issue_verifiee_recalcule_le_pnl_comme_un_reglement_et_garde_une_copie(self):
        apply_corrections(self.tmp, [{"id": "nyk", "valeur_cote": 1, "source": "NBA"}], 0.009, 0.009, NOW, apply=True)
        rows = [json.loads(line) for line in open(self.path)]
        fixed = rows[0]
        self.assertTrue(fixed["won"])
        self.assertEqual(fixed["settle_value"], 1.0)
        self.assertFalse(fixed["regle_par_prudence"])
        self.assertTrue(fixed["corrige_apres_prudence"])
        self.assertEqual(fixed["issue_verifiee"], "NBA")
        self.assertAlmostEqual(fixed["pnl_net"], net_pnl(10.99938, 21.0, 1.0, 0.009, 0.009), places=5)
        self.assertEqual(rows[1], {"id": "autre", "status": "regle", "pnl_net": 3.0})   # intact
        backups = [f for f in os.listdir(os.path.dirname(self.path)) if "avant-correction" in f]
        self.assertEqual(len(backups), 1)

    def test_un_id_introuvable_est_signale_jamais_invente(self):
        report = apply_corrections(self.tmp, [{"id": "absent", "valeur_cote": 1, "source": "?"}], 0.009, 0.009)
        self.assertEqual(report, [{"id": "absent", "introuvable": True}])

    def test_le_fichier_de_corrections_livre_est_coherent(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = json.load(open(os.path.join(here, "scripts", "corrections_prudence_2026-11-22.json")))
        ids = [c["id"] for c in spec["certaines"] + spec["probables"]]
        self.assertEqual(len(ids), len(set(ids)))
        for fix in spec["certaines"] + spec["probables"]:
            self.assertIn(fix["valeur_cote"], (0, 1))
            self.assertTrue(fix["source"])


if __name__ == "__main__":
    unittest.main()
