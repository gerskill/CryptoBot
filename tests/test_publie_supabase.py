"""Publication Supabase pour l'app iPhone (scripts/publie_supabase.py).

CONTEXTE. Le Mac publie toutes les 2 min sept résumés en lecture seule dans la
table `snapshots` ; l'app iPhone ne lit que ça. Ce qui peut mal tourner ici n'est
pas le calcul (il réutilise api.server) mais ce qui SORT : un paramètre de
stratégie, une adresse de token, un mot de passe dans un message d'erreur, ou un
service launchd qui boucle en erreur parce qu'un `.env` n'est pas rempli.

Aucun réseau : `requests.post` est simulé partout.
"""

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from scripts import publie_supabase as pub  # noqa: E402

CLES = {"etat", "positions", "perf_7d", "perf_30d", "perf_all", "liquid", "alertes"}
MOT_DE_PASSE = "mdp-ultra-secret-123"
ENV_COMPLET = {
    "SUPABASE_URL": "https://exemple.supabase.co",
    "SUPABASE_ANON_KEY": "cle-anon-publique",
    "SUPABASE_EMAIL": "killian@example.com",
    "SUPABASE_PASSWORD": MOT_DE_PASSE,
}
MAINTENANT = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
INTERDITS = ("stop_loss", "take_profit", "reglages", "params", "token_address", "api_key", "password")


class Reponse:
    def __init__(self, statut=200, corps=None):
        self.status_code = statut
        self._corps = corps if corps is not None else {}
        self.text = json.dumps(self._corps)

    def json(self):
        return self._corps


def _parcourir(valeur, chemin=""):
    """Toutes les clés et chaînes d'un JSON, avec leur chemin."""
    if isinstance(valeur, dict):
        for cle, sous in valeur.items():
            yield chemin + "/" + str(cle), str(cle)
            yield from _parcourir(sous, chemin + "/" + str(cle))
    elif isinstance(valeur, list):
        for i, sous in enumerate(valeur):
            yield from _parcourir(sous, f"{chemin}[{i}]")
    elif isinstance(valeur, str):
        yield chemin, valeur


class DonneesTestCase(unittest.TestCase):
    """Redirige tous les chemins de données vers un dossier jetable."""

    CHEMINS = ("STATE_PATH", "PARAMS_PATH", "TRADES_LOG_PATH", "SHADOW_LOG_PATH",
               "STRATEGIES_PATH", "ARMS_CONFIG_DIR", "ARMS_DATA_DIR", "PREDICTION_DATA_DIR")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._sauve = {k: getattr(settings, k) for k in self.CHEMINS}
        self._veille = pub.VEILLE_ETAT_PATH
        settings.STATE_PATH = os.path.join(self.tmp, "state.json")
        settings.PARAMS_PATH = os.path.join(self.tmp, "params.json")
        settings.TRADES_LOG_PATH = os.path.join(self.tmp, "trades_log.jsonl")
        settings.SHADOW_LOG_PATH = os.path.join(self.tmp, "shadow_log.jsonl")
        settings.STRATEGIES_PATH = os.path.join(self.tmp, "strategies.json")
        settings.ARMS_CONFIG_DIR = os.path.join(self.tmp, "arms_config")
        settings.ARMS_DATA_DIR = os.path.join(self.tmp, "arms_data")
        settings.PREDICTION_DATA_DIR = os.path.join(self.tmp, "prediction")
        pub.VEILLE_ETAT_PATH = os.path.join(self.tmp, "veille_etat.json")

    def tearDown(self):
        for cle, valeur in self._sauve.items():
            setattr(settings, cle, valeur)
        pub.VEILLE_ETAT_PATH = self._veille

    def _ecrire(self, nom, contenu):
        chemin = os.path.join(self.tmp, nom)
        with open(chemin, "w", encoding="utf-8") as fh:
            json.dump(contenu, fh)
        return chemin

    def _manifeste(self, *noms):
        self._ecrire("strategies.json", {"arms": [
            {"name": n, "role": "voter", "capital_pct": 0.1} for n in noms]})

    def _trades(self, arm, lignes):
        chemin = settings.arm_paths(arm)["trades"]
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        with open(chemin, "w", encoding="utf-8") as fh:
            for ligne in lignes:
                fh.write(json.dumps(ligne) + "\n")

    def _sortie(self, pid, raison, quand, token="PEPE", pnl=-5.0):
        return {"position_id": pid, "token": token, "pnl_usd": pnl, "pnl_pct": pnl,
                "timestamp_exit": quand.isoformat(), "is_final_exit": True,
                "exit_reason": raison, "fraction_of_initial": 1.0}


class TestDonneesAbsentes(DonneesTestCase):
    """`data/` vide (bot jamais lancé) : un état « aucune donnée », pas une exception."""

    def test_data_vide_donne_les_sept_cles_sans_exception(self):
        snaps = pub.construire_snapshots(MAINTENANT)
        self.assertEqual(set(snaps), CLES)

    def test_data_vide_dit_que_le_bot_na_jamais_tourne(self):
        snaps = pub.construire_snapshots(MAINTENANT)
        self.assertIsNone(snaps["etat"]["bot"]["dernier_cycle"])
        self.assertEqual(snaps["etat"]["bot"]["mode"], "PAPER")
        self.assertEqual(snaps["positions"]["lignes"], [])
        self.assertEqual(snaps["alertes"]["lignes"], [])

    def test_data_vide_chaque_valeur_porte_publie_a_en_utc(self):
        for cle, valeur in pub.construire_snapshots(MAINTENANT).items():
            self.assertEqual(valeur["publie_a"], MAINTENANT.isoformat(), cle)

    def test_un_constructeur_qui_plante_ne_fait_pas_tomber_les_autres(self):
        with mock.patch.object(pub.server, "get_arms_performance", side_effect=RuntimeError("boum")):
            snaps = pub.construire_snapshots(MAINTENANT)
        self.assertEqual(set(snaps), CLES)
        self.assertEqual(snaps["perf_7d"]["bras"], [])


class TestContrat(DonneesTestCase):
    """Chaque clé a EXACTEMENT les champs du contrat : l'app consomme ça, rien d'autre ne part."""

    def _jeu_complet(self):
        self._manifeste("baseline", "sniper")
        maintenant_ts = MAINTENANT.timestamp()
        self._ecrire("state.json", {
            "updated_at": maintenant_ts - 30, "mode": "PAPER",
            "positions": [{
                "arm": "sniper", "symbol": "PEPE", "pnl_pct": 12.5, "size_usd": 50.0,
                "duration_min": 14.2, "tp1_hit": True,
                # Ces champs-là ne doivent JAMAIS sortir :
                "token_address": "So1111adresse", "stop_loss_pct": -20, "take_profit_1": 100,
                "take_profit_2": 200, "entry_price": 0.1, "alpha_score": 88,
            }],
            "params": {"stop_loss": -20},
        })
        self._trades("baseline", [self._sortie("a", "TAKE_PROFIT_1", MAINTENANT - timedelta(days=1), pnl=10)])
        self._trades("sniper", [self._sortie("b", "STOP_LOSS", MAINTENANT - timedelta(days=2), pnl=-4)])
        arm_liquid = {
            "arm": "pm_temoin", "famille": "transverse", "enabled": True, "ouverts": 2,
            "compte": {"solde": 41.5, "capital": 46.0},
            "stats": {"paris_regles": 5, "gagnes_net": 3, "pnl_net": -1.0},
            "reglages": {"prix_min": 0.5}, "variables_forkables": ["x"], "apprentissage": {"a": 1},
            "description": "stratégie détaillée",
        }
        patch = mock.patch.object(pub.server, "get_prediction_summary", return_value={
            "exemple_fictif": False, "updated_at_iso": "2026-11-18T11:59:30+00:00",
            "config": {"mise_pct": 0.22}, "arms": [arm_liquid]})
        patch.start()
        self.addCleanup(patch.stop)
        return pub.construire_snapshots(MAINTENANT)

    def test_etat_a_exactement_les_champs_du_contrat(self):
        etat = self._jeu_complet()["etat"]
        self.assertEqual(set(etat), {"publie_a", "bot", "liquid"})
        self.assertEqual(set(etat["bot"]), {"dernier_cycle", "mode"})
        self.assertEqual(set(etat["liquid"]), {"dernier_cycle"})
        self.assertEqual(etat["bot"]["dernier_cycle"],
                         datetime.fromtimestamp(MAINTENANT.timestamp() - 30, tz=timezone.utc).isoformat())
        self.assertEqual(etat["liquid"]["dernier_cycle"], "2026-11-18T11:59:30+00:00")

    def test_positions_a_exactement_les_champs_du_contrat(self):
        positions = self._jeu_complet()["positions"]
        self.assertEqual(set(positions), {"publie_a", "lignes"})
        self.assertEqual(len(positions["lignes"]), 1)
        ligne = positions["lignes"][0]
        self.assertEqual(set(ligne), {"bras", "symbole", "pnl_pct", "taille_usd", "duree_min", "tp1_atteint"})
        self.assertEqual((ligne["bras"], ligne["symbole"], ligne["tp1_atteint"]), ("sniper", "PEPE", True))

    def test_perf_a_exactement_les_champs_du_contrat(self):
        snaps = self._jeu_complet()
        for cle, fenetre in (("perf_7d", "7d"), ("perf_30d", "30d"), ("perf_all", "all")):
            perf = snaps[cle]
            self.assertEqual(set(perf), {"publie_a", "fenetre", "capital_par_bras", "bras", "flotte"}, cle)
            self.assertEqual(perf["fenetre"], fenetre)
            self.assertEqual(set(perf["flotte"]), {"trades", "win_rate", "pnl_usd", "profit_factor"})
            self.assertEqual({b["bras"] for b in perf["bras"]}, {"baseline", "sniper"})
            for bras in perf["bras"]:
                self.assertEqual(set(bras), {"bras", "trades", "win_rate", "ic_bas", "ic_haut",
                                             "profit_factor", "pnl_usd", "max_drawdown_pct", "verdict"}, cle)
        self.assertEqual(snaps["perf_all"]["flotte"]["trades"], 2)

    def test_perf_verdict_est_un_texte_pas_un_objet(self):
        for bras in self._jeu_complet()["perf_all"]["bras"]:
            self.assertTrue(bras["verdict"] is None or isinstance(bras["verdict"], str))

    def test_liquid_a_exactement_les_champs_du_contrat(self):
        liquid = self._jeu_complet()["liquid"]
        self.assertEqual(set(liquid), {"publie_a", "bras"})
        self.assertEqual(len(liquid["bras"]), 1)
        bras = liquid["bras"][0]
        self.assertEqual(set(bras), {"bras", "famille", "actif", "ouverts", "solde_usd", "regles", "gagnes_net"})
        self.assertEqual((bras["bras"], bras["solde_usd"], bras["regles"], bras["gagnes_net"], bras["ouverts"]),
                         ("pm_temoin", 41.5, 5, 3, 2))

    def test_alertes_a_exactement_les_champs_du_contrat(self):
        self._manifeste("baseline")
        self._trades("baseline", [self._sortie("z", "ZOMBIE (sans prix)", MAINTENANT - timedelta(hours=1))])
        alertes = pub.construire_snapshots(MAINTENANT)["alertes"]
        self.assertEqual(set(alertes), {"publie_a", "lignes"})
        self.assertEqual(set(alertes["lignes"][0]), {"quand", "type", "bras", "texte"})

    def test_aucun_parametre_de_strategie_ne_sort(self):
        snaps = self._jeu_complet()
        brut = json.dumps(snaps).lower()
        for chemin, texte in _parcourir(snaps):
            for mot in INTERDITS:
                self.assertNotIn(mot, texte.lower(), f"{mot!r} trouvé en {chemin}")
        for mot in ("so1111adresse", "stratégie détaillée", "variables_forkables", "apprentissage"):
            self.assertNotIn(mot.lower(), brut)

    def test_aucune_valeur_de_reglage_de_la_position_ne_sort(self):
        brut = json.dumps(self._jeu_complet()["positions"])
        for fuite in ("-20", "0.1", "88", "200"):
            self.assertNotIn(f": {fuite}", brut)

    def test_le_resultat_est_serialisable_en_json(self):
        json.dumps(self._jeu_complet())


class TestAlertes(DonneesTestCase):
    """ZOMBIE et PANIC_STOP des 24 dernières heures, au plus 20, les plus récentes d'abord."""

    def test_seules_les_sorties_zombie_et_panic_stop_des_24h_sont_gardees(self):
        self._manifeste("baseline")
        self._trades("baseline", [
            self._sortie("1", "ZOMBIE (sans prix depuis le time stop)", MAINTENANT - timedelta(hours=2)),
            self._sortie("2", "PANIC_STOP (sans prix)", MAINTENANT - timedelta(hours=3)),
            self._sortie("3", "STOP_LOSS", MAINTENANT - timedelta(hours=1)),
            self._sortie("4", "ZOMBIE", MAINTENANT - timedelta(hours=25)),
        ])
        lignes = pub.construire_snapshots(MAINTENANT)["alertes"]["lignes"]
        self.assertEqual([ligne["type"] for ligne in lignes], ["ZOMBIE", "PANIC_STOP"])
        self.assertEqual({ligne["bras"] for ligne in lignes}, {"baseline"})

    def test_plus_recentes_d_abord_et_vingt_au_plus(self):
        self._manifeste("baseline")
        self._trades("baseline", [
            self._sortie(str(i), "ZOMBIE", MAINTENANT - timedelta(minutes=10 * i + 1)) for i in range(30)])
        lignes = pub.construire_snapshots(MAINTENANT)["alertes"]["lignes"]
        self.assertEqual(len(lignes), 20)
        quand = [ligne["quand"] for ligne in lignes]
        self.assertEqual(quand, sorted(quand, reverse=True))

    def test_un_service_en_panne_devient_une_alerte_panne(self):
        depuis = (MAINTENANT - timedelta(minutes=30)).timestamp()
        self._ecrire("veille_etat.json", {
            "bot": {"ok": False, "depuis": depuis, "dernier_message": depuis},
            "ui": {"ok": True, "depuis": depuis, "dernier_message": None},
        })
        lignes = pub.construire_snapshots(MAINTENANT)["alertes"]["lignes"]
        self.assertEqual(len(lignes), 1)
        self.assertEqual(lignes[0]["type"], "PANNE")
        self.assertIsNone(lignes[0]["bras"])
        self.assertIn("bot", lignes[0]["texte"])

    def test_pannes_et_sorties_sont_triees_ensemble(self):
        self._manifeste("baseline")
        self._trades("baseline", [self._sortie("1", "ZOMBIE", MAINTENANT - timedelta(hours=5))])
        depuis = (MAINTENANT - timedelta(minutes=5)).timestamp()
        self._ecrire("veille_etat.json", {"ui": {"ok": False, "depuis": depuis}})
        types = [ligne["type"] for ligne in pub.construire_snapshots(MAINTENANT)["alertes"]["lignes"]]
        self.assertEqual(types, ["PANNE", "ZOMBIE"])


class TestLiquid(DonneesTestCase):
    """L'exemple fictif (`demo`) ne doit jamais se mélanger au réel."""

    def test_exemple_fictif_jamais_publie(self):
        resume = {"exemple_fictif": True, "updated_at_iso": None, "arms": [
            {"arm": "pm_demo", "famille": "x", "enabled": True, "ouverts": 1,
             "compte": {"solde": 10.0}, "stats": {"paris_regles": 1, "gagnes_net": 1}}]}
        with mock.patch.object(pub.server, "get_prediction_summary", return_value=resume):
            liquid = pub.construire_snapshots(MAINTENANT)["liquid"]
        self.assertEqual(liquid["bras"], [])

    def test_le_constructeur_appelle_le_reel_pas_la_demo(self):
        appel = mock.Mock(return_value={"exemple_fictif": False, "arms": []})
        with mock.patch.object(pub.server, "get_prediction_summary", appel):
            pub.construire_snapshots(MAINTENANT)
        appel.assert_called_once_with(demo=False)

    def test_config_liquid_illisible_donne_une_liste_vide(self):
        from fastapi import HTTPException
        with mock.patch.object(pub.server, "get_prediction_summary",
                               side_effect=HTTPException(status_code=503, detail="illisible")):
            liquid = pub.construire_snapshots(MAINTENANT)["liquid"]
        self.assertEqual(liquid["bras"], [])


class TestEnv(unittest.TestCase):
    """Variables absentes : le script le dit et sort 0, sinon launchd le relance en boucle."""

    def test_variables_absentes_sortie_zero_et_message(self):
        sortie, erreur = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(sortie), redirect_stderr(erreur):
            code = pub.main([])
        self.assertEqual(code, 0)
        texte = sortie.getvalue() + erreur.getvalue()
        for nom in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_EMAIL", "SUPABASE_PASSWORD"):
            self.assertIn(nom, texte)

    def test_variable_partielle_est_traitee_comme_absente(self):
        partiel = dict(ENV_COMPLET)
        del partiel["SUPABASE_PASSWORD"]
        with mock.patch.dict(os.environ, partiel, clear=True), \
                mock.patch.object(pub.requests, "post") as post, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = pub.main([])
        self.assertEqual(code, 0)
        post.assert_not_called()

    def test_sans_envoi_ne_demande_ni_env_ni_reseau(self):
        sortie = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(pub.requests, "post") as post, redirect_stdout(sortie):
            code = pub.main(["--sans-envoi"])
        self.assertEqual(code, 0)
        post.assert_not_called()
        self.assertIn('"etat"', sortie.getvalue())


class TestReseau(DonneesTestCase):
    """Supabase injoignable ou 401 : aucune exception, sortie non nulle, aucun mot de passe affiché."""

    def setUp(self):
        super().setUp()
        self.session_path = os.path.join(self.tmp, "supabase_session.json")
        self._ancien = pub.SESSION_PATH
        pub.SESSION_PATH = self.session_path

    def tearDown(self):
        pub.SESSION_PATH = self._ancien
        super().tearDown()

    def _lancer(self, post):
        sortie, erreur = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, ENV_COMPLET, clear=True), \
                mock.patch.object(pub.requests, "post", post), \
                redirect_stdout(sortie), redirect_stderr(erreur):
            code = pub.main([])
        return code, sortie.getvalue() + erreur.getvalue()

    @staticmethod
    def _jeton(access="acces-1", refresh="refresh-1"):
        return Reponse(200, {"access_token": access, "refresh_token": refresh})

    def test_premiere_connexion_par_mot_de_passe_puis_envoi(self):
        appels = []

        def post(url, **kw):
            appels.append((url, kw))
            return self._jeton() if "/auth/v1/token" in url else Reponse(201)

        code, _ = self._lancer(post)
        self.assertEqual(code, 0)
        self.assertIn("grant_type=password", appels[0][0])
        self.assertEqual(appels[0][1]["json"]["password"], MOT_DE_PASSE)
        envoi = appels[-1]
        self.assertIn("/rest/v1/snapshots", envoi[0])
        self.assertEqual(envoi[1]["headers"]["Authorization"], "Bearer acces-1")
        self.assertEqual(envoi[1]["headers"]["apikey"], "cle-anon-publique")
        self.assertIn("merge-duplicates", envoi[1]["headers"]["Prefer"])
        self.assertEqual({ligne["key"] for ligne in envoi[1]["json"]}, CLES)

    def test_la_session_est_gardee_en_600_sans_le_mot_de_passe(self):
        self._lancer(lambda url, **kw: self._jeton() if "/auth/v1/token" in url else Reponse(201))
        mode = stat.S_IMODE(os.stat(self.session_path).st_mode)
        self.assertEqual(mode, 0o600)
        with open(self.session_path, encoding="utf-8") as fh:
            contenu = fh.read()
        self.assertIn("refresh-1", contenu)
        self.assertNotIn(MOT_DE_PASSE, contenu)

    def test_session_valide_pas_de_mot_de_passe(self):
        with open(self.session_path, "w", encoding="utf-8") as fh:
            json.dump({"refresh_token": "ancien-refresh"}, fh)
        appels = []

        def post(url, **kw):
            appels.append((url, kw))
            return self._jeton("acces-2", "refresh-2") if "/auth/v1/token" in url else Reponse(201)

        code, _ = self._lancer(post)
        self.assertEqual(code, 0)
        self.assertIn("grant_type=refresh_token", appels[0][0])
        self.assertNotIn("password", json.dumps([a[1].get("json") for a in appels if "/auth/" in a[0]]))
        with open(self.session_path, encoding="utf-8") as fh:
            self.assertIn("refresh-2", fh.read())

    def test_401_puis_reconnexion_par_mot_de_passe_puis_envoi(self):
        with open(self.session_path, "w", encoding="utf-8") as fh:
            json.dump({"refresh_token": "perime"}, fh)
        urls = []

        def post(url, **kw):
            urls.append(url)
            if "grant_type=refresh_token" in url:
                return Reponse(400, {"error": "invalid_grant"})
            if "grant_type=password" in url:
                return self._jeton("acces-3", "refresh-3")
            return Reponse(201)

        code, _ = self._lancer(post)
        self.assertEqual(code, 0)
        self.assertEqual([u.split("grant_type=")[-1] if "grant_type" in u else "envoi" for u in urls],
                         ["refresh_token", "password", "envoi"])

    def test_jeton_d_acces_refuse_a_l_envoi_reconnecte_une_fois(self):
        envois = []

        def post(url, **kw):
            if "/auth/v1/token" in url:
                return self._jeton(f"acces-{len(envois)}", "r")
            envois.append(kw["headers"]["Authorization"])
            return Reponse(401, {"message": "JWT expired"}) if len(envois) == 1 else Reponse(201)

        code, _ = self._lancer(post)
        self.assertEqual(code, 0)
        self.assertEqual(len(envois), 2)
        self.assertNotEqual(envois[0], envois[1])

    def test_identifiants_refuses_sortie_non_nulle_sans_mot_de_passe(self):
        code, texte = self._lancer(lambda url, **kw: Reponse(400, {"error": "invalid_login"}))
        self.assertNotEqual(code, 0)
        self.assertNotIn(MOT_DE_PASSE, texte)
        self.assertFalse(os.path.exists(self.session_path))

    def test_injoignable_sortie_non_nulle_sans_mot_de_passe_ni_exception(self):
        import requests

        def post(url, **kw):
            # Une vraie exception requests cite parfois l'URL ou le corps.
            raise requests.exceptions.ConnectionError(f"echec {url} corps={kw.get('json')}")

        code, texte = self._lancer(post)
        self.assertNotEqual(code, 0)
        self.assertNotIn(MOT_DE_PASSE, texte)
        self.assertNotIn("cle-anon-publique", texte)

    def test_envoi_refuse_en_500_sortie_non_nulle(self):
        def post(url, **kw):
            return self._jeton() if "/auth/v1/token" in url else Reponse(500, {"message": MOT_DE_PASSE})

        code, texte = self._lancer(post)
        self.assertNotEqual(code, 0)
        self.assertNotIn(MOT_DE_PASSE, texte)

    def test_aucune_cle_service_role_dans_les_en_tetes(self):
        vus = []

        def post(url, **kw):
            vus.append(json.dumps(kw.get("headers", {})))
            return self._jeton() if "/auth/v1/token" in url else Reponse(201)

        self._lancer(post)
        self.assertTrue(vus)
        for entetes in vus:
            self.assertNotIn("service_role", entetes)


class TestSchemaSql(unittest.TestCase):
    """Le schéma est relançable et ferme l'accès anonyme."""

    def setUp(self):
        chemin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "supabase", "schema.sql")
        with open(chemin, encoding="utf-8") as fh:
            self.sql = fh.read().lower()

    def test_table_snapshots_et_rls(self):
        self.assertIn("create table if not exists public.snapshots", self.sql)
        self.assertIn("enable row level security", self.sql)
        self.assertIn("primary key (user_id, key)", self.sql)
        self.assertIn("revoke all on public.snapshots from anon", self.sql)

    def test_pas_de_suppression_ni_de_service_role(self):
        self.assertNotIn("service_role", self.sql)
        self.assertNotIn("for delete", self.sql)

    def test_politiques_relancables(self):
        self.assertEqual(self.sql.count("drop policy if exists"), self.sql.count("create policy"))


if __name__ == "__main__":
    unittest.main()
