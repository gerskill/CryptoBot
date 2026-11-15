"""Diagnostic de reprise : dater l'arrêt sans l'effacer.

Ce script est ce qui répond à « la boucle tourne-t-elle encore, et depuis
quand est-elle arrêtée ». Deux propriétés le rendent utilisable, et les deux
sont faciles à casser sans s'en rendre compte :

1. il est en LECTURE SEULE — une relance ou une prise de verrou durable
   effacerait précisément l'indice qu'on vient chercher ;
2. il lit la dernière ligne d'un .jsonl PAR LA FIN — ces journaux font
   plusieurs Mo et un `readlines()` complet rendrait le diagnostic inutilisable
   sur la machine où il compte.
"""

import fcntl
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from scripts.diagnostic_reprise import (  # noqa: E402
    _age,
    _derniere_ligne_ts,
    gardes,
    verrou,
)


class TestDerniereLigne(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "journal.jsonl")

    def _ecrire(self, rows):
        with open(self.path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def test_rend_lhorodatage_de_la_derniere_ligne(self):
        self._ecrire([{"ts": 1000.0}, {"ts": 2000.0}, {"ts": 3000.0}])
        self.assertEqual(_derniere_ligne_ts(self.path), 3000.0)

    def test_accepte_les_differentes_cles_dhorodatage(self):
        """`ts` dans l'entonnoir, `timestamp` dans le shadow, `timestamp_exit`
        dans le journal de trades — trois journaux, trois conventions."""
        for cle in ("ts", "timestamp", "timestamp_exit"):
            self._ecrire([{cle: 4242.0}])
            self.assertEqual(_derniere_ligne_ts(self.path), 4242.0, cle)

    def test_accepte_un_horodatage_iso(self):
        self._ecrire([{"timestamp_exit": "2026-08-17T15:10:39+00:00"}])
        self.assertGreater(_derniere_ligne_ts(self.path), 1_700_000_000)

    def test_ignore_une_derniere_ligne_tronquee(self):
        """Un arrêt brutal coupe la dernière ligne en plein milieu. C'est
        exactement le cas qu'on diagnostique : il ne doit pas faire échouer
        la lecture, l'avant-dernière ligne date l'arrêt aussi bien."""
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": 1000.0}) + "\n")
            fh.write('{"ts": 2000.0, "sym')
        self.assertEqual(_derniere_ligne_ts(self.path), 1000.0)

    def test_fichier_absent_ou_vide_rend_zero(self):
        self.assertEqual(_derniere_ligne_ts(self.path), 0.0)
        open(self.path, "w").close()
        self.assertEqual(_derniere_ligne_ts(self.path), 0.0)

    def test_ne_lit_pas_tout_le_fichier(self):
        """Sur un journal de plusieurs Mo, la lecture par la fin est ce qui
        rend le script utilisable. Un fichier de 5 Mo doit rester instantané
        et rendre la vraie dernière ligne."""
        with open(self.path, "w", encoding="utf-8") as fh:
            for i in range(60000):
                fh.write(json.dumps({"ts": float(i), "bourrage": "x" * 80}) + "\n")
        self.assertGreater(os.path.getsize(self.path), 5_000_000)
        debut = time.monotonic()
        self.assertEqual(_derniere_ligne_ts(self.path), 59999.0)
        self.assertLess(time.monotonic() - debut, 1.0)


class TestVerrou(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._saved = settings.LOCK_PATH
        settings.LOCK_PATH = os.path.join(self.dir, "alpha_loop.pid")

    def tearDown(self):
        settings.LOCK_PATH = self._saved

    def test_sans_fichier_de_verrou_la_boucle_est_arretee(self):
        etat, _ = verrou()
        self.assertEqual(etat, "ARRÊTÉE")

    def test_un_verrou_tenu_signale_une_boucle_en_cours(self):
        fd = os.open(settings.LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o644)
        os.write(fd, b"4242")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            etat, detail = verrou()
            self.assertEqual(etat, "EN COURS")
            self.assertIn("4242", detail)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_un_fichier_orphelin_signale_un_process_mort(self):
        """`flock` est libéré par le noyau à la mort du détenteur : un fichier
        présent mais libre veut dire que la boucle est morte sans nettoyer."""
        with open(settings.LOCK_PATH, "w", encoding="utf-8") as fh:
            fh.write("1234")
        etat, detail = verrou()
        self.assertEqual(etat, "ARRÊTÉE")
        self.assertIn("1234", detail)

    def test_le_diagnostic_ne_garde_pas_le_verrou(self):
        """LECTURE SEULE. S'il gardait le verrou, la relance qui suit
        échouerait — le diagnostic empêcherait la reprise qu'il recommande."""
        with open(settings.LOCK_PATH, "w", encoding="utf-8") as fh:
            fh.write("1234")
        verrou()
        fd = os.open(settings.LOCK_PATH, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # ne doit pas lever
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def test_le_diagnostic_ne_supprime_pas_le_fichier(self):
        """Le PID du dernier détenteur est une donnée du diagnostic."""
        with open(settings.LOCK_PATH, "w", encoding="utf-8") as fh:
            fh.write("1234")
        verrou()
        self.assertTrue(os.path.exists(settings.LOCK_PATH))


class TestQualificationDesGardes(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._saved = settings.SHADOW_LOG_PATH
        settings.SHADOW_LOG_PATH = os.path.join(self.dir, "shadow_log.jsonl")

    def tearDown(self):
        settings.SHADOW_LOG_PATH = self._saved

    def _shadow(self, rows):
        with open(settings.SHADOW_LOG_PATH, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def test_compte_les_rejets_de_la_garde_lp(self):
        self._shadow([
            {"timestamp": 2000.0, "reason": "LP verrouillée 10% < 50%",
             "would_have_won": True},
            {"timestamp": 2000.0, "reason": "LP verrouillée 0% < 50%",
             "would_have_won": False},
            {"timestamp": 2000.0, "reason": "liquidité 3000$ < 5000$",
             "would_have_won": True},
        ])
        compte = gardes([{"name": "baseline"}], depuis=1000.0)
        self.assertEqual(compte["lp_lock"]["n"], 2)
        self.assertEqual(compte["lp_lock"]["gagnants"], 1)

    def test_ignore_ce_qui_precede_la_livraison_de_la_garde(self):
        """Compter des rejets antérieurs au 17/08 attribuerait à la garde des
        refus qu'elle n'a pas prononcés."""
        self._shadow([
            {"timestamp": 500.0, "reason": "LP verrouillée 10% < 50%"},
            {"timestamp": 2000.0, "reason": "LP verrouillée 10% < 50%"},
        ])
        compte = gardes([{"name": "baseline"}], depuis=1000.0)
        self.assertEqual(compte["lp_lock"]["n"], 1)

    def test_un_journal_absent_ne_fait_pas_echouer_le_diagnostic(self):
        compte = gardes([{"name": "inexistant"}], depuis=0.0)
        self.assertEqual(compte["lp_lock"]["n"], 0)


class TestLisibilite(unittest.TestCase):
    def test_lunite_de_duree_saute_aux_yeux(self):
        """Un trou de 13 jours et un trou de 13 minutes n'appellent pas la
        même conclusion : l'affichage doit les rendre indiscutables."""
        self.assertIn("s", _age(30))
        self.assertIn("min", _age(600))
        self.assertIn("h", _age(7200))
        self.assertIn("jours", _age(13 * 86400))


if __name__ == "__main__":
    unittest.main()
