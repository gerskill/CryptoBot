"""B1 : positions « zombies » (aucune source ne donne de prix). Hors réseau.

Sans prix, `_monitor_arm` sautait la position avant le time stop et la
détection de rug, et `/STOP` la laissait ouverte : elle sortait de tous les
chiffres. Décision de Killian : fermeture d'office à -100 % de la part restante.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.technical import PriceHistory  # noqa: E402
from src.core.journal import TradeJournal  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.core.portfolio import PaperPortfolio  # noqa: E402
from src.core.stats import position_stats  # noqa: E402
from src.main import ZOMBIE_GRACE_MINUTES, AlphaLoop  # noqa: E402

PARAMS = {
    "version": "2.0",
    "risk_per_trade": 0.03,
    "exit_rules": {
        "stop_loss_pct": -25,
        "take_profit_1": 100,
        "take_profit_2": 300,
        "take_profit_3": 500,
        "partial_sell_tp1_pct": 0.5,
        "partial_sell_tp2_pct": 0.5,
        "trailing_stop_activation": 200,
        "trailing_stop_distance_pct": 50,
        "max_hold_time_minutes": 240,
    },
    "filters": {},
    "scoring_weights": {},
    "learning": {},
}

RAISON_ZOMBIE = "ZOMBIE (sans prix depuis le time stop)"


class FauxMarche:
    """Cache de prix du tick : `price` pilotable, jamais de réseau."""

    def __init__(self, price=None):
        self.price_value = price

    def get(self, chain, pair, token):
        return self.price_value, None

    def price(self, token):
        return self.price_value

    def reset(self):
        pass

    def put(self, *args):
        pass


class FauxEntonnoir:
    def __init__(self):
        self.sorties = []

    def record_exit(self, *args):
        self.sorties.append(args)

    def record_liquidity_alert(self, *args):
        pass


class FauxBras:
    name = "test"
    label = "[test]"
    learning = None

    def __init__(self, portfolio):
        self.portfolio = portfolio


class FauxTelegram:
    def send(self, *args, **kwargs):
        pass


def bougie_candidate():
    return Candidate(
        token_address="mint1", symbol="ZOMB", name="ZOMB", chain="solana",
        price_usd=1.0, liquidity_usd=30000, volume_1h=20000,
    )


class TestPositionsSansPrix(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.journal = TradeJournal(os.path.join(tmp, "journal.jsonl"))
        self.portfolio = PaperPortfolio(capital=1000.0, journal=self.journal)
        self.position = self.portfolio.open(bougie_candidate(), PARAMS, 100.0)
        self.arm = FauxBras(self.portfolio)
        self.marche = FauxMarche(price=None)
        self.loop = object.__new__(AlphaLoop)
        self.loop.arms = [self.arm]
        self.loop.market = self.marche
        self.loop.funnel = FauxEntonnoir()
        self.loop.telegram = FauxTelegram()
        self.loop.paused = False
        self.loop.history = PriceHistory()
        self.loop._monitor_sources = {}
        self.loop._milestones_sent = {}
        self.loop._measure_hold = lambda *args, **kwargs: None
        self.loop._current_price = lambda *args, **kwargs: None

    def _vieillir(self, minutes):
        """Recule l'entrée : la règle ne dépend que de `entry_time` (persisté)."""
        position = self.portfolio.positions[self.position.id]
        self.portfolio.positions[self.position.id] = replace(
            position, entry_time=time.time() - minutes * 60
        )

    def _age_zombie(self):
        return self.position.max_hold_time_minutes + ZOMBIE_GRACE_MINUTES + 1

    def test_sans_prix_avant_le_delai_la_position_est_gardee(self):
        # Juste après le time stop mais dans le délai de grâce : comme avant.
        self._vieillir(self.position.max_hold_time_minutes + ZOMBIE_GRACE_MINUTES - 5)
        self.loop._monitor_arm(self.arm, verbose=False)
        self.assertIn(self.position.id, self.portfolio.positions)
        self.assertEqual(self.journal.read_all(), [])

    def test_sans_prix_apres_time_stop_et_delai_la_position_est_fermee_a_moins_100(self):
        self._vieillir(self._age_zombie())
        capital_avant = self.portfolio.capital
        self.loop._monitor_arm(self.arm, verbose=False)

        self.assertNotIn(self.position.id, self.portfolio.positions)
        lignes = self.journal.read_all()
        self.assertEqual(len(lignes), 1)
        ligne = lignes[0]
        self.assertTrue(ligne["is_final_exit"])
        self.assertEqual(ligne["exit_reason"], RAISON_ZOMBIE)
        self.assertLessEqual(ligne["pnl_pct"], -100)
        # Rien n'est revendu : aucun capital recrédité, aucun coût de sortie.
        self.assertEqual(self.portfolio.capital, capital_avant)
        self.assertIsNone(ligne.get("exit_cost_pct"))

    def test_un_tp1_deja_encaisse_reste_compte_dans_la_position(self):
        rows = self.portfolio.update(self.position.id, 2.0)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["is_final_exit"])
        gain_tp1 = rows[0]["pnl_usd"]
        restant = self.portfolio.positions[self.position.id]
        self.assertAlmostEqual(restant.remaining_fraction, 0.5)

        self._vieillir(self._age_zombie())
        self.loop._monitor_arm(self.arm, verbose=False)

        positions = self.journal.read_positions()
        self.assertEqual(len(positions), 1)
        perte_restante = -restant.size_usd * restant.remaining_fraction
        self.assertAlmostEqual(
            positions[0]["pnl_usd"], gain_tp1 + perte_restante, delta=0.5
        )
        # Le gain TP1 est compensé en partie : jamais présenté comme -100 % total.
        self.assertGreater(positions[0]["pnl_usd"], -restant.size_usd)

    def test_le_prix_revenu_rend_la_main_aux_regles_normales(self):
        self._vieillir(self._age_zombie())
        self.marche.price_value = 1.0  # le prix est revenu : le time stop normal s'applique
        self.loop._monitor_arm(self.arm, verbose=False)
        lignes = self.journal.read_all()
        self.assertEqual(len(lignes), 1)
        self.assertTrue(lignes[0]["exit_reason"].startswith("TIME_STOP"))

    def test_stop_ferme_aussi_une_position_sans_prix(self):
        self.loop._panic_exit()
        self.assertNotIn(self.position.id, self.portfolio.positions)
        ligne = self.journal.read_all()[0]
        self.assertEqual(ligne["exit_reason"], "PANIC_STOP (sans prix)")
        self.assertLessEqual(ligne["pnl_pct"], -100)
        self.assertTrue(ligne["is_final_exit"])

    def test_zombie_compte_comme_sortie_automatique(self):
        self._vieillir(self._age_zombie())
        self.loop._monitor_arm(self.arm, verbose=False)
        stats = position_stats(self.journal.read_positions())
        self.assertEqual(stats["trades"], 1)
        self.assertEqual(stats["manual_closes"], 0)

    def test_position_inconnue_ne_leve_pas(self):
        self.assertIsNone(self.portfolio.close_worthless("inexistante", "ZOMBIE"))

    def test_perte_comptee_une_fois_dans_les_pertes_consecutives(self):
        self._vieillir(self._age_zombie())
        self.loop._monitor_arm(self.arm, verbose=False)
        self.assertEqual(self.portfolio.consecutive_losses, 1)
        self.assertEqual(self.portfolio.closed_count, 1)

    def test_frais_d_entree_deja_payes_comptes_dans_la_jambe_finale(self):
        position = self.portfolio.positions[self.position.id]
        self.portfolio.positions[self.position.id] = replace(
            position, entry_cost_version="buy-leg-v1", entry_cost_pct=3.0,
            entry_time=time.time() - self._age_zombie() * 60,
        )
        self.loop._monitor_arm(self.arm, verbose=False)
        # `read_all` plafonne à -100 à la lecture : on lit la ligne brute.
        with open(self.journal.path, encoding="utf-8") as handle:
            brute = json.loads(handle.readline())
        self.assertAlmostEqual(brute["pnl_pct"], -103.0, places=1)

    def test_une_erreur_de_fermeture_ne_tue_pas_la_boucle(self):
        self._vieillir(self._age_zombie())

        def boum(*args, **kwargs):
            raise RuntimeError("disque plein")

        self.portfolio.close_worthless = boum
        self.loop._monitor_arm(self.arm, verbose=False)  # ne doit pas lever


if __name__ == "__main__":
    unittest.main()
