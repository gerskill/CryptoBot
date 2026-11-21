"""Pilotage depuis le site : arrêt d'urgence et reprise (ADR 021).

Le bug évité : un chemin d'écriture du site qui contourne la validation, qui
accepte autre chose que stop/resume, qui ignore le jeton, ou une commande
restée dans la boîte pendant des heures (boucle arrêtée) puis appliquée au
réveil.
"""

import importlib
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

from src import settings  # noqa: E402
from src.core import commandes  # noqa: E402

JSON = "application/json"


class CommandesSiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "data"))
        patch = mock.patch.object(settings, "BASE_DIR", self.tmp)
        patch.start()
        self.addCleanup(patch.stop)
        import api.server as server
        importlib.reload(server)
        self.server = server

    def poster(self, payload, auth=None, entete="1", ctype=JSON, host="localhost:8000"):
        return self.server.post_bot_commande(payload, authorization=auth, x_cryptobbot=entete,
                                             content_type=ctype, host=host)

    def boite(self):
        return commandes.lire_et_vider(os.path.join(self.tmp, "data"))

    def test_stop_depose_dans_la_boite_de_la_boucle(self):
        reponse = self.poster({"action": "stop"})
        self.assertTrue(reponse["ok"])
        self.assertEqual(self.boite(), ["/STOP"])

    def test_resume_depose_dans_la_boite_de_la_boucle(self):
        self.poster({"action": "resume"})
        self.assertEqual(self.boite(), ["/RESUME"])

    def test_action_inconnue_refusee_et_rien_depose(self):
        for action in ("buy", "sell", "pm_toggle", "", None, 3):
            with self.assertRaises(HTTPException) as ctx:
                self.poster({"action": action})
            self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(self.boite(), [])

    def test_payload_illisible_refuse(self):
        with self.assertRaises(HTTPException) as ctx:
            self.poster(["stop"])
        self.assertEqual(ctx.exception.status_code, 422)

    def test_entete_et_type_json_obligatoires(self):
        for kwargs in ({"entete": None}, {"ctype": "text/plain"}, {"ctype": None}):
            with self.assertRaises(HTTPException) as ctx:
                self.poster({"action": "stop"}, **kwargs)
            self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self.boite(), [])

    def test_jeton_exige_quand_defini(self):
        with mock.patch.object(self.server, "API_TOKEN", "secret"):
            for auth in (None, "Bearer faux"):
                with self.assertRaises(HTTPException) as ctx:
                    self.poster({"action": "stop"}, auth=auth)
                self.assertEqual(ctx.exception.status_code, 401)
            self.assertEqual(self.boite(), [])
            self.poster({"action": "stop"}, auth="Bearer secret")
            self.assertEqual(self.boite(), ["/STOP"])

    def test_sans_jeton_configure_acces_local_inchange(self):
        with mock.patch.object(self.server, "API_TOKEN", None):
            self.poster({"action": "resume"})
        self.assertEqual(self.boite(), ["/RESUME"])

    def test_stop_jamais_bloque_par_une_boite_pleine(self):
        data = os.path.join(self.tmp, "data")
        for i in range(25):
            commandes.deposer(data, "resume", f"0000000000{i:03d}-x")
        reponse = self.poster({"action": "stop"})
        self.assertTrue(reponse["ok"])
        self.assertIn("/STOP", self.boite())

    def test_doublon_en_attente_repond_200_deja_en_attente(self):
        premiere = self.poster({"action": "stop"})
        seconde = self.poster({"action": "stop"})
        self.assertTrue(seconde["ok"])
        self.assertIn("déjà en attente", seconde["note"])
        self.assertEqual(seconde["id"], premiere["id"])
        self.assertEqual(self.boite(), ["/STOP"], "une seule commande déposée")

    def test_doublon_ignore_une_commande_perimee(self):
        data = os.path.join(self.tmp, "data")
        commandes.deposer(data, "stop", "0000000000001-vieux", maintenant=1.0)
        reponse = self.poster({"action": "stop"})
        self.assertNotIn("déjà", reponse["note"])
        self.assertEqual(commandes.en_attente(data), 2)

    def test_jeton_non_ascii_donne_401_pas_500(self):
        with mock.patch.object(self.server, "API_TOKEN", "secret"):
            with self.assertRaises(HTTPException) as ctx:
                self.poster({"action": "stop"}, auth="Bearer jetón-é")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_sans_jeton_host_non_local_refuse_403(self):
        with mock.patch.object(self.server, "API_TOKEN", None):
            for host in ("monsite.trycloudflare.com", "", None, "localhost.evil.com", "127.0.0.1.evil.com", "[::1]:8000"):
                with self.assertRaises(HTTPException) as ctx:
                    self.poster({"action": "stop"}, host=host)
                self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(self.boite(), [])

    def test_sans_jeton_host_local_accepte(self):
        with mock.patch.object(self.server, "API_TOKEN", None):
            for host in ("localhost", "localhost:8000", "127.0.0.1:5173"):
                self.poster({"action": "resume"}, host=host)
                self.boite()

    def test_avec_jeton_host_tunnel_accepte(self):
        with mock.patch.object(self.server, "API_TOKEN", "secret"):
            self.poster({"action": "stop"}, auth="Bearer secret", host="monsite.trycloudflare.com")
        self.assertEqual(self.boite(), ["/STOP"])

    def test_lecture_compte_les_commandes_en_attente(self):
        self.assertEqual(self.server.get_bot_commandes()["en_attente"], 0)
        self.poster({"action": "stop"})
        etat = self.server.get_bot_commandes()
        self.assertEqual(etat["en_attente"], 1)
        self.assertEqual(etat["expire_apres_s"], 900)
        self.assertEqual(self.boite(), ["/STOP"], "la lecture ne vide pas la boîte")


class HotesAutorisesTest(unittest.TestCase):
    def setUp(self):
        import api.server as server
        self.server = server

    def test_defaut_local_seulement(self):
        self.assertEqual(self.server.parse_allowed_hosts(None), ["localhost", "127.0.0.1"])
        self.assertEqual(self.server.parse_allowed_hosts("  "), ["localhost", "127.0.0.1"])

    def test_extension_par_variable_d_environnement(self):
        hotes = self.server.parse_allowed_hosts(" a.example.com , b.example.com,,")
        self.assertEqual(hotes, ["localhost", "127.0.0.1", "a.example.com", "b.example.com"])

    def test_jokers_refuses(self):
        hotes = self.server.parse_allowed_hosts("*, *.example.com, ok.example.com")
        self.assertEqual(hotes[2:], ["ok.example.com"])

    def test_middleware_rejette_un_host_inconnu_et_garde_le_local(self):
        from fastapi.testclient import TestClient
        client = TestClient(self.server.app)
        self.assertEqual(client.get("/api/health", headers={"Host": "evil.example.com"}).status_code, 400)
        self.assertNotEqual(client.get("/api/health", headers={"Host": "localhost:8000"}).status_code, 400)
        self.assertNotEqual(client.get("/api/health", headers={"Host": "127.0.0.1"}).status_code, 400)


class BoucleSignalePeriimeeTest(unittest.TestCase):
    def test_commande_perimee_journalisee_et_notifiee(self):
        from src import main as boucle
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "data"))
        commandes.deposer(os.path.join(tmp, "data"), "stop", "0000000000001-1", maintenant=1.0)
        faux = mock.MagicMock()
        faux.telegram.poll_commands.return_value = []
        faux.paused = False
        with mock.patch.object(boucle.settings, "BASE_DIR", tmp):
            boucle.AlphaLoop._handle_commands(faux)
        messages = [c.args[0] for c in faux.telegram.send.call_args_list]
        self.assertTrue(any("/STOP" in m and "périmée" in m and "15 min" in m for m in messages), messages)
        faux._panic_exit.assert_not_called()

    def test_rien_de_perime_aucun_message(self):
        from src import main as boucle
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "data"))
        faux = mock.MagicMock()
        faux.telegram.poll_commands.return_value = []
        with mock.patch.object(boucle.settings, "BASE_DIR", tmp):
            boucle.AlphaLoop._handle_commands(faux)
        faux.telegram.send.assert_not_called()


class ExpirationTest(unittest.TestCase):
    def test_commande_de_plus_de_15_min_ignoree_et_supprimee(self):
        tmp = tempfile.mkdtemp()
        commandes.deposer(tmp, "stop", "0000000000001-1", maintenant=1000.0)
        self.assertEqual(commandes.lire_et_vider(tmp, maintenant=1000.0 + 901), [])
        self.assertEqual(os.listdir(commandes.dossier(tmp)), [], "fichier périmé supprimé")

    def test_commande_recente_appliquee(self):
        tmp = tempfile.mkdtemp()
        commandes.deposer(tmp, "stop", "0000000000001-1", maintenant=1000.0)
        self.assertEqual(commandes.lire_et_vider(tmp, maintenant=1000.0 + 600), ["/STOP"])

    def test_lire_expose_les_perimees(self):
        tmp = tempfile.mkdtemp()
        commandes.deposer(tmp, "stop", "0000000000001-1", maintenant=1000.0)
        commandes.deposer(tmp, "resume", "0000000000002-1", maintenant=1900.0)
        lecture = commandes.lire(tmp, maintenant=2000.0)
        self.assertEqual(lecture.commandes, ["/RESUME"])
        self.assertEqual(lecture.perimees, ["/STOP"])

    def _ecrire(self, tmp, contenu, mtime):
        racine = commandes.dossier(tmp)
        os.makedirs(racine, exist_ok=True)
        chemin = os.path.join(racine, "0000000000001-1.json")
        with open(chemin, "w", encoding="utf-8") as fh:
            fh.write(contenu)
        os.utime(chemin, (mtime, mtime))

    def test_t_invalide_se_rabat_sur_mtime_recent(self):
        for t in ("absent", '"abc"', "true", "null", "NaN", "Infinity", "1e999"):
            tmp = tempfile.mkdtemp()
            corps = '{"action": "stop"}' if t == "absent" else '{"action": "stop", "t": %s}' % t
            self._ecrire(tmp, corps, mtime=5000.0)
            self.assertEqual(commandes.lire(tmp, maintenant=5100.0).commandes, ["/STOP"], t)

    def test_t_invalide_se_rabat_sur_mtime_ancien(self):
        for t in ("absent", '"abc"', "true", "NaN"):
            tmp = tempfile.mkdtemp()
            corps = '{"action": "stop"}' if t == "absent" else '{"action": "stop", "t": %s}' % t
            self._ecrire(tmp, corps, mtime=1000.0)
            lecture = commandes.lire(tmp, maintenant=5000.0)
            self.assertEqual((lecture.commandes, lecture.perimees), ([], ["/STOP"]), t)

    def test_t_dans_le_futur_est_invalide(self):
        tmp = tempfile.mkdtemp()
        self._ecrire(tmp, '{"action": "stop", "t": 99999999}', mtime=1000.0)
        lecture = commandes.lire(tmp, maintenant=5000.0)
        self.assertEqual(lecture.perimees, ["/STOP"], "t futur : mtime ancien fait foi")

    def test_t_leger_decalage_d_horloge_tolere(self):
        tmp = tempfile.mkdtemp()
        commandes.deposer(tmp, "stop", "0000000000001-1", maintenant=5030.0)
        self.assertEqual(commandes.lire(tmp, maintenant=5000.0).commandes, ["/STOP"])


if __name__ == "__main__":
    unittest.main()
