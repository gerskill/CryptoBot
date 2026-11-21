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

    def poster(self, payload, auth=None, entete="1", ctype=JSON):
        return self.server.post_bot_commande(payload, authorization=auth, x_cryptobbot=entete, content_type=ctype)

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

    def test_boite_pleine_refusee(self):
        for _ in range(self.server.MAX_COMMANDES_EN_ATTENTE):
            self.poster({"action": "resume"})
        with self.assertRaises(HTTPException) as ctx:
            self.poster({"action": "stop"})
        self.assertEqual(ctx.exception.status_code, 429)

    def test_lecture_compte_les_commandes_en_attente(self):
        self.assertEqual(self.server.get_bot_commandes()["en_attente"], 0)
        self.poster({"action": "stop"})
        etat = self.server.get_bot_commandes()
        self.assertEqual(etat["en_attente"], 1)
        self.assertEqual(etat["expire_apres_s"], 900)
        self.assertEqual(self.boite(), ["/STOP"], "la lecture ne vide pas la boîte")


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


if __name__ == "__main__":
    unittest.main()
