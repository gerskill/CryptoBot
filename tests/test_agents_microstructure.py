"""Microstructure AMM — et le refus d'inventer un carnet d'ordres.

CE QUI EST VERROUILLÉ ICI. La spec demandait bid-ask et depth. Solana n'a pas
de carnet central : les memecoins se traitent sur des AMM, et `Snapshot` ne
porte que `(ts, price, volume_5m, liquidity)`. Un spread dérivé de ces champs
serait un nombre plausible et faux.

Les tests protègent donc trois choses :
  1. le devis Jupiter tient lieu de spread — et son absence rend `None`, pas 0 ;
  2. l'impulsion exige liquidité ET prix en hausse, parce qu'un prix qui monte
     sur un pool qui se vide est le profil d'un rug, pas d'un achat ;
  3. une panne réseau du devis ne remonte jamais — « la boucle ne meurt jamais »
     vaut aussi pour un agent de mesure.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents import _journal  # noqa: E402
from src.agents.microstructure_agent import MicrostructureAgent  # noqa: E402
from src.analysis.technical import Snapshot  # noqa: E402
from src.core.economics import MAX_ACCEPTABLE_ROUND_TRIP_PCT  # noqa: E402


def _snaps(series, interval=60.0, start=1_000_000.0):
    """`series` = liste de (prix, liquidité)."""
    return [
        Snapshot(ts=start + i * interval, price=p, volume_5m=1000.0, liquidity=liq)
        for i, (p, liq) in enumerate(series)
    ]


class FakeJupiter:
    enabled = True

    def __init__(self, cost=None, boom=False):
        self.cost = cost
        self.boom = boom
        self.calls = 0

    def round_trip_cost_pct(self, mint, size, sol_price):
        self.calls += 1
        if self.boom:
            raise RuntimeError("réseau")
        return self.cost


class TestDerives(unittest.TestCase):
    def test_liquidite_et_prix_en_hausse(self):
        lecture = MicrostructureAgent().read(
            _snaps([(1.0, 20000.0), (1.1, 22000.0), (1.2, 24000.0)])
        )
        self.assertAlmostEqual(lecture.price_drift_pct, 20.0)
        self.assertAlmostEqual(lecture.liquidity_drift_pct, 20.0)

    def test_depth_est_la_liquidite_la_plus_recente(self):
        lecture = MicrostructureAgent().read(
            _snaps([(1.0, 20000.0), (1.0, 21000.0), (1.0, 25000.0)])
        )
        self.assertEqual(lecture.depth_usd, 25000.0)

    def test_une_liquidite_de_depart_nulle_rend_none_pas_infini(self):
        lecture = MicrostructureAgent().read(
            _snaps([(1.0, 0.0), (1.0, 100.0), (1.0, 200.0)])
        )
        self.assertIsNone(lecture.liquidity_drift_pct)

    def test_trop_peu_de_snapshots_ne_conclut_rien(self):
        lecture = MicrostructureAgent().read(_snaps([(1.0, 20000.0)]))
        self.assertIsNone(lecture.price_drift_pct)
        self.assertIsNone(lecture.impulse)
        self.assertIn("snapshots", lecture.reason)


class TestImpulsion(unittest.TestCase):
    def test_prix_et_liquidite_en_hausse_font_une_impulsion(self):
        lecture = MicrostructureAgent().read(
            _snaps([(1.0, 20000.0), (1.1, 22000.0), (1.2, 24000.0)])
        )
        self.assertTrue(lecture.impulse)

    def test_prix_en_hausse_sur_pool_qui_se_vide_nest_pas_une_impulsion(self):
        """C'est le profil d'un rug en cours. Le confondre avec un achat serait
        la pire erreur que cet agent puisse induire."""
        lecture = MicrostructureAgent().read(
            _snaps([(1.0, 20000.0), (1.3, 12000.0), (1.6, 6000.0)])
        )
        self.assertFalse(lecture.impulse)

    def test_liquidite_en_hausse_prix_en_baisse_non_plus(self):
        lecture = MicrostructureAgent().read(
            _snaps([(1.0, 20000.0), (0.9, 22000.0), (0.8, 24000.0)])
        )
        self.assertFalse(lecture.impulse)

    def test_un_carnet_infranchissable_annule_limpulsion(self):
        agent = MicrostructureAgent(jupiter=FakeJupiter(cost=23.62))
        lecture = agent.read(
            _snaps([(1.0, 20000.0), (1.1, 22000.0), (1.2, 24000.0)]),
            token_address="mint",
        )
        self.assertGreater(lecture.round_trip_pct, MAX_ACCEPTABLE_ROUND_TRIP_PCT)
        self.assertFalse(lecture.tradable)
        self.assertFalse(lecture.impulse)


class TestDevis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "microstructure_log.jsonl")
        self.hausse = _snaps([(1.0, 20000.0), (1.1, 22000.0), (1.2, 24000.0)])

    def test_sans_jupiter_le_cout_est_inconnu_pas_gratuit(self):
        lecture = MicrostructureAgent().read(self.hausse, token_address="mint")
        self.assertIsNone(lecture.round_trip_pct)
        self.assertIsNone(lecture.tradable)
        self.assertIn("devis indisponible", lecture.reason)

    def test_une_panne_de_devis_ne_remonte_pas(self):
        """Une microstructure inconnue vaut mieux qu'un cycle perdu."""
        agent = MicrostructureAgent(jupiter=FakeJupiter(boom=True))
        lecture = agent.read(self.hausse, token_address="mint")
        self.assertIsNone(lecture.round_trip_pct)

    def test_pas_de_devis_sans_adresse(self):
        jupiter = FakeJupiter(cost=2.0)
        MicrostructureAgent(jupiter=jupiter).read(self.hausse)
        self.assertEqual(jupiter.calls, 0)

    def test_un_carnet_franchissable_est_tradable(self):
        agent = MicrostructureAgent(jupiter=FakeJupiter(cost=2.32))
        lecture = agent.read(self.hausse, token_address="mint")
        self.assertTrue(lecture.tradable)

    def test_observe_journalise_la_lecture(self):
        agent = MicrostructureAgent(jupiter=FakeJupiter(cost=2.0), log_path=self.log)
        agent.observe("mint", "TOK", self.hausse)
        rows = _journal.read(self.log)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "TOK")
        self.assertTrue(rows[0]["impulse"])


if __name__ == "__main__":
    unittest.main()
