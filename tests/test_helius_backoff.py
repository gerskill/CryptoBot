"""Helius : cache, limiteur partagé, attente après 429. Hors réseau.

Le plafond d'avant était 9 appels par seconde (10 demandés, marge 0,9).
Le plafond d'après est 2 par seconde, soit 120 par minute au lieu de 540.
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.apis.helius import (  # noqa: E402
    CACHE_TTL_SECONDS,
    DEFAULT_MAX_PER_SECOND,
    HeliusAPI,
    PriorityLimiter,
    use_watch_priority,
)


class Response:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {"result": {"value": {"uiAmount": 1}}}
        self.headers = headers or {}

    def json(self):
        return self._body


def client(responses, clock, sleep, rng=lambda: 0.5):
    helius = HeliusAPI("not-a-real-key", max_per_second=10, clock=clock, sleep=sleep, rng=rng)
    pending = list(responses)

    def post(url, json=None, timeout=None):
        if "api-key=" in url and "not-a-real-key" not in url:
            raise AssertionError("clé inattendue dans l'URL de test")
        if not pending:
            return Response(500, {"result": None})
        return pending.pop(0)

    helius.session.post = post
    return helius, pending


class TestCache(unittest.TestCase):
    def test_supply_et_frais_ne_sont_pas_redemandes_avant_expiration(self):
        now = [0.0]
        posts = []

        def clock():
            return now[0]

        helius = HeliusAPI("not-a-real-key", clock=clock, sleep=lambda _s: None)
        bodies = [
            Response(200, {"result": {"value": {"uiAmount": 1000}}}),
            Response(200, {"result": [{"prioritizationFee": 10}, {"prioritizationFee": 30}]}),
            Response(200, {"result": {"value": {"uiAmount": 1000}}}),
        ]

        def post(url, json=None, timeout=None):
            posts.append(json["method"])
            return bodies.pop(0)

        helius.session.post = post
        self.assertEqual(helius.get_supply("mintA"), 1000.0)
        self.assertEqual(helius.get_supply("mintA"), 1000.0)
        self.assertEqual(posts, ["getTokenSupply"])
        helius.get_recent_prioritization_fee_lamports()
        helius.get_recent_prioritization_fee_lamports()
        self.assertEqual(posts, ["getTokenSupply", "getRecentPrioritizationFees"])
        now[0] = CACHE_TTL_SECONDS["getTokenSupply"]
        self.assertEqual(helius.get_supply("mintA"), 1000.0)
        self.assertEqual(posts.count("getTokenSupply"), 2)

    def test_getTokenAccounts_n_est_pas_mis_en_cache(self):
        posts = []
        helius = HeliusAPI("not-a-real-key", sleep=lambda _s: None)

        def post(url, json=None, timeout=None):
            posts.append(json["method"])
            return Response(200, {"result": {"token_accounts": [], "cursor": None}})

        helius.session.post = post
        helius._rpc("getTokenAccounts", {"mint": "m"})
        helius._rpc("getTokenAccounts", {"mint": "m"})
        self.assertEqual(posts, ["getTokenAccounts", "getTokenAccounts"])
        self.assertNotIn("getTokenAccounts", CACHE_TTL_SECONDS)


class TestBackoff(unittest.TestCase):
    def test_429_attend_retry_after_puis_reussit_sans_boucle(self):
        now = [0.0]
        slept = []

        def clock():
            return now[0]

        def sleep(seconds):
            slept.append(seconds)
            now[0] += seconds

        helius, pending = client(
            [
                Response(429, {"error": "slow"}, {"Retry-After": "3"}),
                Response(200, {"result": {"value": {"uiAmount": 5}}}),
            ],
            clock, sleep,
        )
        self.assertEqual(helius.get_supply("mint"), 5.0)
        self.assertEqual(slept, [3.0])
        self.assertEqual(pending, [])
        self.assertEqual(helius.rate_limited, 1)

    def test_trois_429_puis_abandon(self):
        now = [0.0]
        slept = []
        posts = []

        def clock():
            return now[0]

        def sleep(seconds):
            slept.append(round(seconds, 2))
            now[0] += seconds

        helius = HeliusAPI("not-a-real-key", clock=clock, sleep=sleep, rng=lambda: 0.5)

        def post(url, json=None, timeout=None):
            posts.append(1)
            return Response(429, headers={})

        helius.session.post = post
        self.assertIsNone(helius.get_supply("mint"))
        self.assertEqual(len(posts), 3)
        # Gigue 1,0 : 1 s puis 2 s. Pas de troisième attente après le dernier échec.
        self.assertEqual(slept, [1.0, 2.0])


class TestPriorite(unittest.TestCase):
    def test_la_surveillance_passe_devant_le_scan(self):
        limiter = PriorityLimiter(1, 1.0)
        self.assertTrue(limiter.acquire(priority="normal"))
        order = []

        def run(priority):
            ok = limiter.acquire(priority=priority, timeout=2)
            order.append(priority if ok else "echec")

        watch = threading.Thread(target=run, args=("watch",))
        scan = threading.Thread(target=run, args=("normal",))
        watch.start()
        scan.start()
        deadline = time.time() + 2
        while limiter.waiting("watch") < 1 or limiter.waiting("normal") < 1:
            if time.time() > deadline:
                self.fail(f"les deux fils ne se sont pas mis en attente : {order}")
            time.sleep(0.01)
        watch.join(3)
        scan.join(3)
        self.assertEqual(order[0], "watch")
        self.assertIn("normal", order)

    def test_le_fil_de_surveillance_marque_ses_appels(self):
        seen = []
        helius = HeliusAPI("not-a-real-key", sleep=lambda _s: None)

        def acquire(timeout=None, priority=None):
            seen.append(priority)
            return True

        def post(url, json=None, timeout=None):
            return Response(200, {"result": {"value": {"uiAmount": 1}}})

        helius.rate_limiter.acquire = acquire
        helius.session.post = post
        helius.get_supply("m")
        with use_watch_priority():
            helius.get_supply("autre")
        self.assertEqual(seen, ["normal", "watch"])


class TestBackoffFile(unittest.TestCase):
    def test_un_appelant_deja_en_file_attend_aussi_le_429(self):
        limiter = PriorityLimiter(1, 0.1)
        self.assertTrue(limiter.acquire(priority="normal"))
        got = {}

        def run():
            got["ok"] = limiter.acquire(priority="normal", timeout=2)
            got["at"] = time.monotonic()

        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.time() + 1
        while limiter.waiting("normal") < 1:
            if time.time() > deadline:
                self.fail("l'appelant ne s'est pas mis en file")
            time.sleep(0.01)
        marked = time.monotonic()
        limiter.note_backoff(0.6)
        thread.join(2)
        self.assertTrue(got.get("ok"))
        self.assertGreaterEqual(got["at"] - marked, 0.5)


class TestBudget(unittest.TestCase):
    def test_plafond_par_defaut(self):
        self.assertEqual(DEFAULT_MAX_PER_SECOND, 2)
        self.assertEqual(DEFAULT_MAX_PER_SECOND * 60, 120)
        # Ancien limiteur : RateLimiter(10, 1) garde 90 % → 9/s → 540/min.
        self.assertEqual(9 * 60, 540)


if __name__ == "__main__":
    unittest.main()
