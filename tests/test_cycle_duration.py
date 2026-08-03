"""Durée de cycle publiée au dashboard — pour un badge de santé honnête.

CE QUE ÇA REMPLACE. `scan.duration_sec` ne mesure que le sous-temps de
collecte (3,3 s mesuré en production) : un badge calé dessus serait
TOUJOURS vert, quel que soit l'état réel du cycle — monitoring, entrées,
notifications compris. `_last_cycle_duration_sec` mesure le `_cycle()`
entier, exactement comme `run()` le fait déjà pour son propre log
d'avertissement (`elapsed > interval`).

LE DÉCALAGE D'UN CYCLE EST ASSUMÉ, PAS UN BUG. `_publish_state` tourne
DANS `_cycle()`, avant que la durée du cycle courant ne soit connue. La
valeur publiée est donc toujours celle du cycle PRÉCÉDENT — préférable à
une valeur inventée ou à zéro.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.main as main_module  # noqa: E402


class FakeLoop:
    """Assez de surface pour dérouler `AlphaLoop.run` sans construire un bot
    complet — mêmes bouts stubés qu'un test réel toucherait (cycle, sommeil,
    Telegram, cache, portefeuille), rien de plus."""

    def __init__(self):
        self.params = type("P", (), {"get": lambda self, k, d=None: 90})()
        self.telegram = type("T", (), {"send": lambda self, m: None})()
        self.mode = "PAPER"
        self.cache = type("C", (), {"save": lambda self: None})()
        self.portfolio = type(
            "Pf", (), {"stats": lambda self: {
                "equity": 1000.0, "total_pnl_usd": 0.0, "total_trades": 0,
                "win_rate": 0.0,
            }}
        )()
        self.cycle_count = 0
        self._last_cycle_duration_sec = 0.0

    def _print_banner(self, interval):
        pass

    def _cycle(self):
        pass

    def _sleep_monitoring(self, duration):
        # Coupe la boucle après le premier tour, comme un vrai SIGTERM le
        # ferait via `_handle_sigint` — sans dépendre d'un vrai signal ici.
        main_module._running = False


class TestDureeDeCycle(unittest.TestCase):
    def setUp(self):
        main_module._running = True
        self.addCleanup(setattr, main_module, "_running", True)

    def test_la_duree_reelle_du_cycle_est_enregistree(self):
        fake = FakeLoop()
        temps = iter([100.0, 103.7])  # started, puis la lecture de fin
        with patch("time.monotonic", side_effect=lambda: next(temps)):
            main_module.AlphaLoop.run(fake)
        self.assertEqual(fake._last_cycle_duration_sec, 3.7)

    def test_une_erreur_de_cycle_nempeche_pas_la_mesure(self):
        """`_cycle` peut lever — la boucle ne meurt jamais dessus, et la
        durée doit quand même être capturée pour ce tour."""
        fake = FakeLoop()
        fake._cycle = lambda: (_ for _ in ()).throw(RuntimeError("boum"))
        temps = iter([200.0, 201.2])
        with patch("time.monotonic", side_effect=lambda: next(temps)):
            main_module.AlphaLoop.run(fake)
        self.assertAlmostEqual(fake._last_cycle_duration_sec, 1.2, places=1)

    def test_la_duree_est_celle_du_cycle_precedent_pas_du_courant(self):
        """Deux tours : après le premier, la valeur publiable reflète tour 1,
        pas un chiffre du tour 2 encore en cours."""
        fake = FakeLoop()
        tours = {"n": 0}
        vue_pendant_tour_2 = {}

        def cycle_qui_observe():
            tours["n"] += 1
            if tours["n"] == 2:
                vue_pendant_tour_2["duree"] = fake._last_cycle_duration_sec

        fake._cycle = cycle_qui_observe

        appels = {"n": 0}

        def sommeil_deux_tours(duration):
            appels["n"] += 1
            if appels["n"] >= 2:
                main_module._running = False

        fake._sleep_monitoring = sommeil_deux_tours

        # started1, elapsed1(=fin tour1), started2, elapsed2(=fin tour2)
        temps = iter([0.0, 5.0, 5.0, 9.0])
        with patch("time.monotonic", side_effect=lambda: next(temps)):
            main_module.AlphaLoop.run(fake)

        self.assertEqual(vue_pendant_tour_2["duree"], 5.0)


if __name__ == "__main__":
    unittest.main()
