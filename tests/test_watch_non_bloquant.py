"""Le tick de surveillance ne doit jamais attendre le RPC des wallets manuels,
et les contrôles d'entrée lisent les positions sous le verrou des livres."""
import os
import sys
import threading
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.main import AlphaLoop  # noqa: E402


class _WatcherLent:
    def __init__(self):
        self.poll_sec = 60.0
        self._last_poll = 0.0
        self.appels = []
        self.fini = threading.Event()

    def trades(self, wallets, force=False):
        wallets = list(wallets)
        self.appels.append(wallets)
        if wallets:
            self._last_poll = time.time()
            time.sleep(0.5)  # RPC lent
            self.fini.set()
        return []


class TestReleveManuelleHorsTick(unittest.TestCase):
    def test_le_tick_ne_bloque_pas_et_une_seule_releve_a_la_fois(self):
        watcher = _WatcherLent()
        loop = SimpleNamespace(copy_watcher=watcher, copy_manual={"W1": {}})
        debut = time.monotonic()
        AlphaLoop._poll_manual_wallets(loop)
        AlphaLoop._poll_manual_wallets(loop)  # relève en cours : rien de plus
        self.assertLess(time.monotonic() - debut, 0.2)
        self.assertTrue(watcher.fini.wait(2.0))
        loop._manual_poll_thread.join(2.0)
        self.assertEqual(watcher.appels, [["W1"]])
        # Throttle 60 s respecté une fois la relève finie.
        AlphaLoop._poll_manual_wallets(loop)
        self.assertEqual(watcher.appels, [["W1"]])


class TestExpositionSousVerrou(unittest.TestCase):
    def test_fleet_exposure_prend_le_verrou(self):
        pris = []

        class Verrou:
            def __enter__(self):
                pris.append(True)

            def __exit__(self, *exc):
                return False

        arm = SimpleNamespace(is_test=False, portfolio=SimpleNamespace(positions={}))
        loop = SimpleNamespace(_books=Verrou(), arms=[arm])
        AlphaLoop._fleet_exposure(loop)
        self.assertEqual(pris, [True])


class _GmgnLent:
    def __init__(self, snapshot):
        self._smart_money_snapshot = snapshot
        self.appels = 0
        self.fini = threading.Event()

    def fetch_smart_money_trades(self, limit=200):
        self.appels += 1
        time.sleep(0.5)  # CLI lent
        self._smart_money_snapshot = (time.time(), [{"tx": "neuf"}])
        self.fini.set()
        return self._smart_money_snapshot[1]


class TestGmgnHorsTick(unittest.TestCase):
    def test_cache_perime_lu_sans_attendre_et_relance_en_fond(self):
        gmgn = _GmgnLent((time.time() - 999, [{"tx": "vieux"}]))
        loop = SimpleNamespace(pipeline=SimpleNamespace(gmgn=gmgn))
        debut = time.monotonic()
        self.assertEqual(AlphaLoop._gmgn_trades_cached(loop), [{"tx": "vieux"}])
        AlphaLoop._gmgn_trades_cached(loop)  # relève en cours : pas une 2e
        self.assertLess(time.monotonic() - debut, 0.2)
        self.assertTrue(gmgn.fini.wait(2.0))
        loop._gmgn_refresh_thread.join(2.0)
        self.assertEqual(gmgn.appels, 1)
        self.assertEqual(AlphaLoop._gmgn_trades_cached(loop), [{"tx": "neuf"}])
        self.assertEqual(gmgn.appels, 1)  # cache frais : pas de relève


if __name__ == "__main__":
    unittest.main()
