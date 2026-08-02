"""Volatilité réalisée — normalisation temporelle et cas dégénérés.

CE QUI EST VERROUILLÉ ICI, et pourquoi ça compte plus que la formule.

La boucle échantillonne à DEUX cadences : 90 s en scan, 5 s dès qu'une
position est ouverte. Un écart-type brut de rendements donnerait donc à deux
tokens de volatilité réelle identique des valeurs dans un rapport de √18 selon
qu'ils sont surveillés ou non. Comparer ces deux nombres reviendrait à
comparer des cadences d'échantillonnage. La normalisation en √t est la seule
chose qui rend cet agent utilisable — et la seule qu'un test doit protéger.
"""

import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents import _journal  # noqa: E402
from src.agents.volatility_agent import (  # noqa: E402
    MIN_RETURNS,
    VolatilityAgent,
    log_returns,
)
from src.analysis.technical import Snapshot  # noqa: E402


def _snaps(prices, interval=90.0, start=1_000_000.0):
    return [
        Snapshot(ts=start + i * interval, price=p, volume_5m=500.0, liquidity=20000.0)
        for i, p in enumerate(prices)
    ]


def _alternance(n, amplitude=0.02, base=1.0):
    """Prix qui alternent de ±amplitude — volatilité réelle constante."""
    return [base * (1 + amplitude * (1 if i % 2 else -1)) for i in range(n)]


class TestRendements(unittest.TestCase):
    def test_les_prix_nuls_sont_ecartes_sans_lever(self):
        """`log(0)` ferait tomber le cycle, ce que l'invariant « la boucle ne
        meurt jamais » interdit de laisser remonter jusque-là."""
        snaps = _snaps([1.0, 0.0, 1.1, -1.0, 1.2])
        for rendement, delta in log_returns(snaps):
            self.assertTrue(math.isfinite(rendement))
            self.assertGreater(delta, 0)

    def test_les_intervalles_nuls_sont_ecartes(self):
        doublon = [
            Snapshot(ts=1000.0, price=1.0, volume_5m=0.0, liquidity=1.0),
            Snapshot(ts=1000.0, price=2.0, volume_5m=0.0, liquidity=1.0),
        ]
        self.assertEqual(log_returns(doublon), [])

    def test_les_snapshots_sont_remis_dans_lordre(self):
        desordre = list(reversed(_snaps([1.0, 1.1, 1.2])))
        self.assertEqual(len(log_returns(desordre)), 2)


class TestNormalisation(unittest.TestCase):
    def test_deux_cadences_rendent_la_meme_volatilite_horaire(self):
        """LE TEST QUI COMPTE. Même processus, échantillonné à 90 s et à 5 s :
        sans la mise à l'échelle en √t les deux valeurs seraient dans un
        rapport de √18, et un token surveillé paraîtrait 4× plus volatil qu'un
        token seulement scanné."""
        agent = VolatilityAgent()
        # Même nombre de pas, même amplitude par pas : la volatilité PAR PAS
        # est identique, donc l'horaire doit différer d'exactement √(90/5).
        lent = agent.read(_snaps(_alternance(30), interval=90.0))
        rapide = agent.read(_snaps(_alternance(30), interval=5.0))

        self.assertTrue(lent.known and rapide.known)
        rapport = rapide.hourly_pct / lent.hourly_pct
        self.assertAlmostEqual(rapport, math.sqrt(90.0 / 5.0), places=4)

    def test_une_amplitude_double_double_la_volatilite(self):
        agent = VolatilityAgent()
        simple = agent.read(_snaps(_alternance(30, amplitude=0.02)))
        double = agent.read(_snaps(_alternance(30, amplitude=0.04)))
        self.assertAlmostEqual(double.hourly_pct / simple.hourly_pct, 2.0, delta=0.1)

    def test_un_prix_immobile_a_une_volatilite_nulle(self):
        lecture = VolatilityAgent().read(_snaps([1.0] * 20))
        self.assertTrue(lecture.known)
        self.assertAlmostEqual(lecture.hourly_pct, 0.0, places=6)

    def test_lintervalle_median_est_rendu(self):
        lecture = VolatilityAgent().read(_snaps(_alternance(20), interval=90.0))
        self.assertAlmostEqual(lecture.median_interval_sec, 90.0)


class TestGardeFous(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "volatility_log.jsonl")

    def test_trop_peu_de_points_rend_inconnu_et_pas_zero(self):
        """Zéro se lirait « prix immobile », l'inverse de « pas mesuré »."""
        lecture = VolatilityAgent().read(_snaps([1.0, 1.1, 1.2]))
        self.assertFalse(lecture.known)
        self.assertIn("rendements exploitables", lecture.reason)

    def test_exactement_le_minimum_suffit(self):
        lecture = VolatilityAgent().read(_snaps(_alternance(MIN_RETURNS + 1)))
        self.assertTrue(lecture.known, lecture.reason)

    def test_une_volatilite_extreme_est_signalee_pas_ecretee(self):
        """L'écrêter masquerait le cas au lieu de le montrer."""
        lecture = VolatilityAgent().read(
            _snaps(_alternance(20, amplitude=0.5), interval=5.0)
        )
        self.assertTrue(lecture.extreme)
        self.assertIsNotNone(lecture.hourly_pct)

    def test_une_lecture_inconnue_est_journalisee(self):
        agent = VolatilityAgent(log_path=self.log)
        agent.observe("addr", "TOK", _snaps([1.0, 1.1]))
        rows = _journal.read(self.log)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["volatility_hourly_pct"])


if __name__ == "__main__":
    unittest.main()
