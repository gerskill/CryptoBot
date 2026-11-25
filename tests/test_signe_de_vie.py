"""Signe de vie extérieur (préparé le 2026-11-22). Hors réseau : l'envoi est simulé."""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.notify.signe_de_vie import SigneDeVie, url_configuree  # noqa: E402

FAUSSE_URL = "https://hc-ping.com/00000000-fake-fake-fake-000000000000"


class Horloge:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Params(dict):
    def get(self, cle, defaut=None):
        return super().get(cle, defaut)


class TestConfiguration(unittest.TestCase):
    def test_desactive_sans_url(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(url_configuree(Params()))
        s = SigneDeVie(None, envoyer=lambda *a: self.fail("aucun envoi"))
        self.assertFalse(s.actif)
        self.assertFalse(s.pointer(force=True))
        self.assertIn("désactivé", s.description())

    def test_env_prioritaire_puis_params(self):
        with mock.patch.dict(os.environ, {"SIGNE_DE_VIE_URL": FAUSSE_URL}, clear=True):
            self.assertEqual(url_configuree(Params()), FAUSSE_URL)
        with mock.patch.dict(os.environ, {}, clear=True):
            p = Params({"monitoring.signe_de_vie_url": FAUSSE_URL})
            self.assertEqual(url_configuree(p), FAUSSE_URL)

    def test_url_non_https_refusee(self):
        with mock.patch.dict(os.environ, {"SIGNE_DE_VIE_URL": "http://exemple"}, clear=True):
            self.assertIsNone(url_configuree(Params()))

    def test_description_ne_montre_jamais_l_url(self):
        s = SigneDeVie(FAUSSE_URL)
        self.assertNotIn("fake", s.description())
        self.assertIn("hc-ping.com", s.description())


class TestEnvoi(unittest.TestCase):
    def test_un_envoi_par_intervalle(self):
        h, envois = Horloge(), []
        s = SigneDeVie(FAUSSE_URL, envoyer=lambda u, d: envois.append(u), horloge=h,
                       intervalle=300, en_fond=False)
        self.assertTrue(s.pointer())
        h.t += 100
        self.assertFalse(s.pointer())
        h.t += 200
        self.assertTrue(s.pointer())
        self.assertEqual(len(envois), 2)

    def test_echec_n_affiche_pas_l_url_et_ne_leve_pas(self):
        def plante(url, delai):
            raise ConnectionError(f"Failed to reach {url}")

        s = SigneDeVie(FAUSSE_URL, envoyer=plante, en_fond=False)
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            s.pointer()
        self.assertEqual(s.echecs, 1)
        self.assertNotIn("fake", sortie.getvalue())
        self.assertIn("ConnectionError", sortie.getvalue())


if __name__ == "__main__":
    unittest.main()
