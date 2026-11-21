"""Veille (`scripts/veille.py`) et modèles launchd (`scripts/launchd/`).

Ce qui est verrouillé :
- on prévient d'une PANNE et d'un RÉTABLISSEMENT, pas à chaque passage de
  5 minutes : une alerte qui sonne tout le temps finit coupée ;
- un state.json absent est une panne, pas une donnée manquante à ignorer ;
- les modèles de service restent des .plist valides une fois remplis.
"""

import glob
import json
import os
import plistlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from src import settings  # noqa: E402
from scripts import veille  # noqa: E402

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T0 = 1_800_000_000.0


class TestMessages(unittest.TestCase):
    def test_premier_passage_ok_ne_dit_rien(self):
        messages, memoire = veille.messages_a_envoyer({"bot": (True, "ok")}, {}, T0)
        self.assertEqual(messages, [])
        self.assertTrue(memoire["bot"]["ok"])

    def test_premier_passage_en_panne_previent(self):
        messages, _ = veille.messages_a_envoyer({"bot": (False, "aucun state.json")}, {}, T0)
        self.assertEqual(len(messages), 1)
        self.assertIn("EN PANNE", messages[0])

    def test_une_panne_qui_dure_ne_previent_pas_a_chaque_passage(self):
        _, memoire = veille.messages_a_envoyer({"bot": (False, "x")}, {}, T0)
        messages, memoire = veille.messages_a_envoyer({"bot": (False, "x")}, memoire, T0 + 300)
        self.assertEqual(messages, [])

    def test_rappel_apres_six_heures_de_panne(self):
        _, memoire = veille.messages_a_envoyer({"bot": (False, "x")}, {}, T0)
        messages, _ = veille.messages_a_envoyer(
            {"bot": (False, "x")}, memoire, T0 + veille.RAPPEL_S + 1
        )
        self.assertEqual(len(messages), 1)
        self.assertIn("toujours en panne", messages[0])

    def test_retablissement_annonce_avec_sa_duree(self):
        _, memoire = veille.messages_a_envoyer({"ui": (False, "x")}, {}, T0)
        messages, memoire = veille.messages_a_envoyer({"ui": (True, "HTTP 200")}, memoire, T0 + 1800)
        self.assertEqual(len(messages), 1)
        self.assertIn("rétabli après 30 min", messages[0])
        self.assertTrue(memoire["ui"]["ok"])


class TestDiagnostic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (settings.STATE_PATH, settings.PREDICTION_DATA_DIR)
        settings.STATE_PATH = os.path.join(self.tmp, "state.json")
        settings.PREDICTION_DATA_DIR = os.path.join(self.tmp, "prediction")

    def tearDown(self):
        settings.STATE_PATH, settings.PREDICTION_DATA_DIR = self._saved

    def test_state_absent_est_une_panne(self):
        resultat = veille.diagnostiquer(["bot"], time.time())
        self.assertFalse(resultat["bot"][0])

    def test_state_recent_est_ok_et_ancien_est_une_panne(self):
        with open(settings.STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump({"updated_at": T0}, fh)
        self.assertTrue(veille.diagnostiquer(["bot"], T0 + 60)["bot"][0])
        self.assertFalse(veille.diagnostiquer(["bot"], T0 + veille.SEUIL_BOT_S + 1)["bot"][0])

    def test_site_injoignable_est_une_panne_sans_lever(self):
        def get_qui_echoue(*_args, **_kwargs):
            raise requests.exceptions.ConnectionError("refusé")

        resultat = veille.diagnostiquer(["ui"], T0, get=get_qui_echoue)
        self.assertFalse(resultat["ui"][0])


class TestDiagnosticDonnees(unittest.TestCase):
    """La veille prévient quand une source de données est COUPÉE (Helius en erreur sur presque
    toutes les entrées, par exemple), pas pour un simple repli ni un échantillon trop court."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = (settings.STATE_PATH, settings.TRADES_LOG_PATH)
        settings.STATE_PATH = os.path.join(self.tmp, "state.json")
        settings.TRADES_LOG_PATH = os.path.join(self.tmp, "trades_log.jsonl")

    def tearDown(self):
        settings.STATE_PATH, settings.TRADES_LOG_PATH = self._saved

    def _entrees(self, n, onchain):
        from datetime import datetime, timedelta, timezone
        maintenant = datetime.fromtimestamp(T0, tz=timezone.utc)
        with open(settings.TRADES_LOG_PATH, "w", encoding="utf-8") as fh:
            for i in range(n):
                fh.write(json.dumps({
                    "position_id": str(i), "is_final_exit": True, "onchain_status": onchain,
                    "timestamp_entry": (maintenant - timedelta(hours=2)).isoformat(),
                    "entry_bundler_rate": 0.1, "entry_insider_rate": 0.1,
                    "entry_sniper_count": 1, "entry_entrapment_ratio": 0.1,
                }) + "\n")

    def test_rien_a_juger_n_est_pas_une_panne(self):
        ok, detail = veille.diagnostiquer(["donnees"], T0)["donnees"]
        self.assertTrue(ok, detail)

    def test_onchain_en_erreur_sur_presque_toutes_les_entrees_previent(self):
        self._entrees(15, "error")
        ok, detail = veille.diagnostiquer(["donnees"], T0)["donnees"]
        self.assertFalse(ok)
        self.assertIn("on-chain", detail)

    def test_onchain_sain_ne_previent_pas(self):
        self._entrees(15, "ok")
        self.assertTrue(veille.diagnostiquer(["donnees"], T0)["donnees"][0])

    def test_un_state_illisible_ne_leve_pas(self):
        with open(settings.STATE_PATH, "w", encoding="utf-8") as fh:
            fh.write("{pas du json")
        self.assertTrue(veille.diagnostiquer(["donnees"], T0)["donnees"][0])

    def test_les_donnees_n_entrent_pas_dans_le_ping_externe(self):
        # Le ping healthchecks.io dit « le bot vit » : une source coupée ne doit pas le faire échouer.
        self.assertTrue(veille.tout_ok({"bot": (True, "ok"), "donnees": (False, "on-chain coupé")}))
        self.assertFalse(veille.tout_ok({"bot": (False, "arrêté"), "donnees": (True, "ok")}))

    def test_donnees_fait_partie_des_verifications_par_defaut(self):
        self.assertIn("donnees", veille.VERIFICATIONS)


class TestModelesLaunchd(unittest.TestCase):
    def test_chaque_modele_rempli_est_un_plist_valide(self):
        modeles = glob.glob(os.path.join(RACINE, "scripts", "launchd", "*.plist.modele"))
        self.assertEqual(len(modeles), 5)
        for chemin in modeles:
            texte = open(chemin, encoding="utf-8").read()
            for cle in ("__PROJET__", "__PYTHON__", "__PATH__"):
                texte = texte.replace(cle, "/x")
            document = plistlib.loads(texte.encode("utf-8"))
            self.assertTrue(document["Label"].startswith("com.cryptobbot."))
            # Un service permanent sans limite de relance boucle s'il plante au démarrage.
            if document.get("KeepAlive"):
                self.assertGreaterEqual(document["ThrottleInterval"], 30)


if __name__ == "__main__":
    unittest.main()
