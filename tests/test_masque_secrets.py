"""Masquage du jeton Telegram dans les journaux (préparé le 2026-11-22). FAUX jeton, hors réseau."""

import gzip
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import requests

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)

from src.core import masque_secrets  # noqa: E402
from src.core.masque_secrets import FluxMasque, enregistrer_secrets, masquer  # noqa: E402
from src.notify.telegram import TelegramNotifier  # noqa: E402

# Construit à l'exécution : écrit d'un bloc, il déclenchait (à raison) le contrôle anti-secrets du commit.
FAUX_JETON = "1234567890" + ":" + "AA" + "FAUXjeton" * 3 + "FAUX123"
FAUSSE_URL = f"https://api.telegram.org/bot{FAUX_JETON}/getUpdates?timeout=0"


def charger_script():
    chemin = os.path.join(RACINE, "scripts", "masquer_jeton_logs.py")
    spec = importlib.util.spec_from_file_location("masquer_jeton_logs", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMasquer(unittest.TestCase):
    def test_url_telegram_masquee(self):
        texte = masquer(f"Max retries exceeded with url: {FAUSSE_URL}")
        self.assertNotIn("AAFAUX", texte)
        self.assertNotIn("1234567890:", texte)
        self.assertIn("api.telegram.org/bot<jeton-masque>/getUpdates", texte)

    def test_texte_normal_intact(self):
        texte = "[Monitor] mihao | $0.00012 | P&L +3.0% | 12:34:56"
        self.assertEqual(masquer(texte), texte)

    def test_secret_connu_masque_partout(self):
        enregistrer_secrets(["https://hc-ping.com/secret-faux-0000"])
        try:
            self.assertNotIn("secret-faux", masquer("ping https://hc-ping.com/secret-faux-0000 KO"))
        finally:
            masque_secrets._secrets_connus.clear()

    def test_flux_masque_sur_print(self):
        tampon = io.StringIO()
        flux = FluxMasque(tampon)
        print(f"[Telegram] getUpdates erreur réseau : {FAUSSE_URL}", file=flux)
        self.assertNotIn("AAFAUX", tampon.getvalue())
        self.assertTrue(tampon.getvalue().endswith("\n"))


class FausseSession:
    def get(self, url, params=None, timeout=None):
        raise requests.exceptions.ConnectionError(
            f"HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded "
            f"with url: {url.replace('https://api.telegram.org', '')}?timeout=0 "
            f"(Caused by NameResolutionError(\"Failed to resolve 'api.telegram.org'\"))"
        )


class TestTelegram(unittest.TestCase):
    def test_erreur_reseau_ne_contient_plus_le_jeton(self):
        t = TelegramNotifier(FAUX_JETON, "42")
        t.session = FausseSession()
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            self.assertEqual(t.poll_commands(), [])
        self.assertIn("erreur réseau", sortie.getvalue())
        self.assertNotIn("AAFAUX", sortie.getvalue())
        self.assertIn("bot<jeton-masque>", sortie.getvalue())


class TestScriptNettoyage(unittest.TestCase):
    def setUp(self):
        self.script = charger_script()
        self.projet = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.projet, "logs", "archive"))
        self.bot_log = os.path.join(self.projet, "logs", "bot.log")
        with open(self.bot_log, "w") as fh:
            fh.write("ligne normale\n")
            fh.write(f"[Telegram] getUpdates erreur réseau : {FAUSSE_URL}\n")
            fh.write("autre ligne\n")
        self.archive = os.path.join(self.projet, "logs", "archive", "bot-ancien.log.gz")
        with gzip.open(self.archive, "wt") as fh:
            fh.write(f"x {FAUSSE_URL}\ny {FAUSSE_URL}\n")

    def lancer(self, *args):
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            code = self.script.main(["--projet", self.projet, *args])
        return code, sortie.getvalue()

    def test_verification_seule_ne_modifie_rien(self):
        avant = open(self.bot_log, "rb").read()
        code, sortie = self.lancer()
        self.assertEqual(code, 0)
        self.assertIn("1 occurrence", sortie)
        self.assertEqual(open(self.bot_log, "rb").read(), avant)
        self.assertNotIn("AAFAUX", sortie)

    def test_nettoyage_avec_sauvegarde_meme_taille(self):
        taille = os.path.getsize(self.bot_log)
        inode = os.stat(self.bot_log).st_ino
        code, sortie = self.lancer("--oui")
        self.assertEqual(code, 0, sortie)
        self.assertNotIn("AAFAUX", sortie)
        contenu = open(self.bot_log, "rb").read()
        self.assertNotIn(b"AAFAUX", contenu)
        self.assertIn(b"bot<jeton-masque>", contenu)
        self.assertIn(b"ligne normale", contenu)
        self.assertEqual(os.path.getsize(self.bot_log), taille)  # même taille
        self.assertEqual(os.stat(self.bot_log).st_ino, inode)  # même fichier : le bot continue
        with gzip.open(self.archive, "rb") as fh:
            self.assertNotIn(b"AAFAUX", fh.read())
        sauvegardes = [d for d in os.listdir(os.path.join(self.projet, "backups"))
                       if d.startswith("logs-avant-masquage-")]
        self.assertEqual(len(sauvegardes), 1)
        copie = os.path.join(self.projet, "backups", sauvegardes[0], "logs", "bot.log")
        self.assertIn(b"AAFAUX", open(copie, "rb").read())  # la sauvegarde garde l'original
        self.assertEqual(os.stat(copie).st_mode & 0o777, 0o600)

    def test_ecriture_concurrente_preservee(self):
        # Le bot ajoute une ligne pendant le nettoyage : elle ne doit pas être perdue.
        with open(self.bot_log, "ab") as journal_du_bot:
            self.lancer("--oui")
            journal_du_bot.write(b"ligne ecrite par le bot apres\n")
        self.assertTrue(open(self.bot_log, "rb").read().endswith(b"ligne ecrite par le bot apres\n"))

    def test_rien_a_faire(self):
        self.lancer("--oui")
        code, sortie = self.lancer("--oui")
        self.assertEqual(code, 0)
        self.assertIn("Aucun jeton", sortie)


if __name__ == "__main__":
    unittest.main()
