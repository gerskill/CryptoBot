"""RSI — bornes, cas dégénérés, et refus de conclure sans historique.

CE QUI EST VERROUILLÉ ICI. Le RSI est trivial à écrire et facile à écrire
faux : la division par zéro quand il n'y a que des hausses, le lissage de
Wilder confondu avec une moyenne simple, et surtout le retour d'une valeur
plausible sur trois bougies. La dernière est la plus dangereuse — un RSI
calculé sur un historique famélique ne ressemble pas à une erreur.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents import _journal  # noqa: E402
from src.agents.rsi_agent import (  # noqa: E402
    DEFAULT_PERIOD,
    OVERBOUGHT,
    OVERSOLD,
    RSIAgent,
    compute_rsi,
)
from src.analysis.technical import Candle  # noqa: E402


def _candles(closes):
    return [Candle(bucket=i, high=c, low=c, close=c, volume=100.0)
            for i, c in enumerate(closes)]


class TestCalcul(unittest.TestCase):
    def test_hausse_monotone_sature_a_cent(self):
        """Aucune perte -> perte moyenne nulle. Le cas qui divise par zéro."""
        self.assertEqual(compute_rsi([float(i) for i in range(1, 20)]), 100.0)

    def test_baisse_monotone_sature_a_zero(self):
        self.assertEqual(compute_rsi([float(i) for i in range(20, 1, -1)]), 0.0)

    def test_prix_plat_rend_cinquante_et_non_cent(self):
        """Gain nul ET perte nulle : le rapport est indéterminé, pas infini.
        Rendre 100 ferait passer un token immobile pour un surachat."""
        self.assertEqual(compute_rsi([10.0] * 20), 50.0)

    def test_toujours_borne_entre_zero_et_cent(self):
        series = [
            [1.0, 5.0, 2.0, 9.0, 3.0, 7.0, 1.5, 8.0, 2.5, 6.0,
             1.2, 9.5, 3.3, 7.7, 2.2, 8.8],
            [100.0, 99.0, 101.0, 98.0, 102.0, 97.0, 103.0, 96.0,
             104.0, 95.0, 105.0, 94.0, 106.0, 93.0, 107.0, 92.0],
        ]
        for closes in series:
            valeur = compute_rsi(closes)
            self.assertIsNotNone(valeur)
            self.assertGreaterEqual(valeur, 0.0)
            self.assertLessEqual(valeur, 100.0)

    def test_sans_assez_de_clotures_rend_none_et_pas_zero(self):
        """Zéro serait lu comme « survente extrême » — l'inverse d'inconnu."""
        self.assertIsNone(compute_rsi([1.0, 2.0, 3.0]))
        self.assertIsNone(compute_rsi([1.0] * DEFAULT_PERIOD))

    def test_exactement_periode_plus_un_suffit(self):
        self.assertIsNotNone(compute_rsi([float(i) for i in range(DEFAULT_PERIOD + 1)]))

    def test_linfluence_du_passe_decroit_lissage_de_wilder(self):
        """LA PROPRIÉTÉ QUI DISTINGUE WILDER D'UNE MOYENNE SIMPLE.

        Avec une moyenne mobile simple sur `period`, seules les `period`
        dernières variations comptent et tout ce qui précède pèse zéro. Avec le
        lissage de Wilder, la graine influence TOUT l'historique — mais son
        poids décroît en (1-1/period)^n. Deux préfixes opposés suivis d'une
        longue queue identique doivent donc converger.

        Un préfixe court ne converge PAS : c'est le cas qui avait fait échouer
        la première version de ce test, où trois pas de lissage ne suffisaient
        pas à absorber une chute de -29.
        """
        queue = [100.0 + (2.0 if i % 2 else -1.0) * (i % 5) for i in range(120)]
        montant = [float(i) for i in range(1, 20)]
        chutant = [float(i) for i in range(20, 1, -1)]

        ecart_long = abs(compute_rsi(montant + queue) - compute_rsi(chutant + queue))
        ecart_court = abs(
            compute_rsi(montant + queue[:5]) - compute_rsi(chutant + queue[:5])
        )

        self.assertLess(ecart_long, 1.0, "la queue longue doit faire converger")
        self.assertGreater(ecart_court, ecart_long, "le passé récent pèse encore")


class TestAgent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "rsi_log.jsonl")

    def test_les_zones_correspondent_aux_seuils(self):
        agent = RSIAgent()
        haut = agent.read(_candles([float(i) for i in range(1, 20)]))
        bas = agent.read(_candles([float(i) for i in range(20, 1, -1)]))
        self.assertEqual(haut.zone, "surachat")
        self.assertGreaterEqual(haut.value, OVERBOUGHT)
        self.assertEqual(bas.zone, "survente")
        self.assertLessEqual(bas.value, OVERSOLD)

    def test_historique_court_rend_inconnu_et_le_dit(self):
        lecture = RSIAgent().read(_candles([1.0, 2.0, 3.0]))
        self.assertFalse(lecture.known)
        self.assertEqual(lecture.zone, "inconnu")
        self.assertIn("clôtures", lecture.reason)

    def test_les_clotures_nulles_sont_ecartees(self):
        """Un prix à zéro vient d'un flux dégradé, pas d'un marché."""
        closes = [0.0, 0.0] + [float(i) for i in range(1, 20)]
        self.assertTrue(RSIAgent().read(_candles(closes)).known)

    def test_une_lecture_inconnue_est_journalisee_aussi(self):
        """Sans ça on ne saurait plus distinguer « jamais en surachat » de
        « jamais assez de bougies pour le savoir »."""
        agent = RSIAgent(log_path=self.log)
        agent.observe("addr", "TOK", _candles([1.0, 2.0]))
        rows = _journal.read(self.log)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["rsi"])
        self.assertEqual(rows[0]["zone"], "inconnu")

    def test_sans_chemin_de_log_rien_nest_ecrit(self):
        RSIAgent().observe("addr", "TOK", _candles([1.0] * 20))
        self.assertFalse(os.path.exists(self.log))


if __name__ == "__main__":
    unittest.main()
