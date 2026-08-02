"""Pondération du smart money par la fraîcheur des achats.

Le compte brut traitait un achat vieux de 29 minutes comme un achat d'il y a
30 secondes. Sur un memecoin, une demi-heure est une éternité : le mouvement
est terminé, et suivre revient à acheter la sortie de ceux qu'on croyait
suivre.
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.apis.gmgn import GmgnAPI  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.core.scoring import smart_money_absolute  # noqa: E402


def trade(age_minutes, side="buy", usd=100.0, wallet="w1", token="tok"):
    return {
        "maker": wallet,
        "base_address": token,
        "timestamp": time.time() - age_minutes * 60,
        "side": side,
        "amount_usd": usd,
        "maker_info": {"tags": ["smart_degen"]},
    }


class TestPonderation(unittest.TestCase):
    def setUp(self):
        self.api = GmgnAPI(enabled=False)

    def _activity(self, trades):
        self.api._smart_money_snapshot = (time.time(), trades)
        self.api.enabled = True
        return self.api.activity_by_token()

    def test_un_achat_recent_pese_plus_qu_un_ancien(self):
        recent = self._activity([trade(0.5)])["tok"]
        ancien = self._activity([trade(29, wallet="w2")])["tok"]
        self.assertGreater(recent.weighted_buys, ancien.weighted_buys)
        self.assertEqual(recent.buys, ancien.buys, "le compte brut, lui, est égal")

    def test_le_poids_decroit_lineairement(self):
        milieu = self._activity([trade(15)])["tok"]
        self.assertAlmostEqual(milieu.weighted_buys, 0.5, places=1)

    def test_un_achat_a_l_instant_vaut_un(self):
        self.assertAlmostEqual(self._activity([trade(0)])["tok"].weighted_buys, 1.0, places=1)

    def test_la_fraicheur_resume_le_signal(self):
        chaud = self._activity([trade(1), trade(2, wallet="w2")])["tok"]
        self.assertGreater(chaud.freshness, 0.9)
        froid = self._activity([trade(28, wallet="w3"), trade(29, wallet="w4")])["tok"]
        self.assertLess(froid.freshness, 0.15)

    def test_horodatage_absent_vaut_un_demi(self):
        # Ni rejeté ni privilégié : une donnée absente ne doit pas trancher.
        sans_date = {"maker": "w1", "base_address": "tok", "side": "buy",
                     "amount_usd": 100.0, "maker_info": {"tags": ["smart_degen"]}}
        self.assertAlmostEqual(self._activity([sans_date])["tok"].weighted_buys, 0.5)

    def test_l_age_du_plus_recent_est_expose(self):
        activity = self._activity([trade(20), trade(3, wallet="w2")])["tok"]
        self.assertLess(activity.newest_buy_age_minutes, 4)

    def test_les_ventes_ne_sont_pas_ponderees(self):
        activity = self._activity([trade(1, side="sell")])["tok"]
        self.assertEqual(activity.sells, 1)
        self.assertEqual(activity.weighted_buys, 0.0)


class TestScoring(unittest.TestCase):
    def _candidate(self, buys, weighted=None):
        return Candidate(
            token_address="a", symbol="T", name="T", chain="solana",
            smart_money_buys_30m=buys, smart_money_weighted_buys=weighted,
        )

    def test_le_score_utilise_la_ponderation(self):
        frais = smart_money_absolute(self._candidate(5, weighted=4.8))
        rassis = smart_money_absolute(self._candidate(5, weighted=0.6))
        self.assertGreater(frais, rassis * 5)

    def test_repli_sur_le_compte_brut(self):
        # Journaux anciens sans pondération : ne jamais dégrader le score.
        self.assertEqual(
            smart_money_absolute(self._candidate(5, weighted=None)),
            smart_money_absolute(self._candidate(5, weighted=5.0)),
        )

    def test_absence_de_smart_money_reste_none(self):
        self.assertIsNone(smart_money_absolute(self._candidate(None)))

    def test_le_score_reste_borne(self):
        self.assertLessEqual(smart_money_absolute(self._candidate(50, weighted=50.0)), 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
