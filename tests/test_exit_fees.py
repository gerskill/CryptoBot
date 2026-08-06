"""Tests du coût réel à la sortie — src/core/exit_fees.py + garde RPC Helius."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.apis.helius import ALLOWED_RPC_METHODS, ForbiddenRpcMethod, HeliusAPI  # noqa: E402
from src.core.exit_fees import MIN_SOL_PRICE_USD, measure_exit_cost  # noqa: E402


class FakeJupiter:
    def __init__(self, cost_pct=None, enabled=True, raises=False):
        self.enabled = enabled
        self._cost_pct = cost_pct
        self._raises = raises

    def round_trip_cost_pct(self, token_address, size_usd, sol_price_usd):
        if self._raises:
            raise RuntimeError("devis indisponible")
        return self._cost_pct


class FakeHelius:
    def __init__(self, lamports=None, enabled=True, raises=False):
        self.enabled = enabled
        self._lamports = lamports
        self._raises = raises

    def get_recent_prioritization_fee_lamports(self):
        if self._raises:
            raise RuntimeError("RPC indisponible")
        return self._lamports


class TestMeasureExitCost(unittest.TestCase):
    def test_les_deux_sources_mesurees(self):
        jupiter = FakeJupiter(cost_pct=3.0)
        helius = FakeHelius(lamports=1_000_000_000)  # 1 SOL de priority fee
        cost = measure_exit_cost(jupiter, helius, "mint1", size_usd=100.0, sol_price_usd=200.0)
        self.assertIsNotNone(cost)
        self.assertFalse(cost.partial)
        self.assertAlmostEqual(cost.price_impact_pct, 3.0)
        self.assertAlmostEqual(cost.priority_fee_usd, 200.0)
        # 3% impact + (200$/100$ * 100) = 200% de priority fee sur ce montant minuscule
        self.assertAlmostEqual(cost.total_cost_pct, 203.0)

    def test_rien_de_mesurable_retourne_none(self):
        jupiter = FakeJupiter(enabled=False)
        helius = FakeHelius(enabled=False)
        cost = measure_exit_cost(jupiter, helius, "mint1", size_usd=100.0, sol_price_usd=200.0)
        self.assertIsNone(cost)

    def test_seul_jupiter_mesurable_marque_partial(self):
        jupiter = FakeJupiter(cost_pct=3.0)
        helius = FakeHelius(enabled=False)
        cost = measure_exit_cost(jupiter, helius, "mint1", size_usd=100.0, sol_price_usd=200.0)
        self.assertIsNotNone(cost)
        self.assertTrue(cost.partial)
        self.assertAlmostEqual(cost.total_cost_pct, 3.0)
        self.assertIn("priority fee", cost.reason)

    def test_seul_helius_mesurable_marque_partial(self):
        jupiter = FakeJupiter(enabled=False)
        helius = FakeHelius(lamports=500_000_000)
        cost = measure_exit_cost(jupiter, helius, "mint1", size_usd=1000.0, sol_price_usd=100.0)
        self.assertIsNotNone(cost)
        self.assertTrue(cost.partial)
        self.assertIn("impact de prix", cost.reason)

    def test_exception_jupiter_ne_bloque_pas(self):
        jupiter = FakeJupiter(raises=True)
        helius = FakeHelius(lamports=1_000_000)
        cost = measure_exit_cost(jupiter, helius, "mint1", size_usd=100.0, sol_price_usd=200.0)
        self.assertIsNotNone(cost)
        self.assertIsNone(cost.price_impact_pct)

    def test_exception_helius_ne_bloque_pas(self):
        jupiter = FakeJupiter(cost_pct=3.0)
        helius = FakeHelius(raises=True)
        cost = measure_exit_cost(jupiter, helius, "mint1", size_usd=100.0, sol_price_usd=200.0)
        self.assertIsNotNone(cost)
        self.assertIsNone(cost.priority_fee_usd)

    def test_taille_nulle_retourne_none(self):
        cost = measure_exit_cost(
            FakeJupiter(cost_pct=3.0), FakeHelius(lamports=1), "mint1",
            size_usd=0.0, sol_price_usd=200.0,
        )
        self.assertIsNone(cost)

    def test_prix_sol_sous_le_plancher_ignore_le_priority_fee(self):
        jupiter = FakeJupiter(cost_pct=3.0)
        helius = FakeHelius(lamports=1_000_000_000)
        cost = measure_exit_cost(
            jupiter, helius, "mint1", size_usd=100.0,
            sol_price_usd=MIN_SOL_PRICE_USD / 2,
        )
        self.assertIsNotNone(cost)
        self.assertIsNone(cost.priority_fee_usd)
        self.assertAlmostEqual(cost.total_cost_pct, 3.0)

    def test_as_dict_arrondit_et_expose_partial(self):
        jupiter = FakeJupiter(cost_pct=3.14159)
        helius = FakeHelius(enabled=False)
        cost = measure_exit_cost(jupiter, helius, "mint1", size_usd=100.0, sol_price_usd=200.0)
        payload = cost.as_dict()
        self.assertEqual(payload["price_impact_pct"], 3.142)
        self.assertIsNone(payload["priority_fee_usd"])
        self.assertTrue(payload["partial"])


class TestHeliusRpcAllowlist(unittest.TestCase):
    def setUp(self):
        self.helius = HeliusAPI(api_key="fake-key")

    def test_methode_hors_liste_leve(self):
        with self.assertRaises(ForbiddenRpcMethod):
            self.helius._rpc("sendTransaction", [])

    def test_methodes_autorisees_couvrent_les_usages_du_client(self):
        for method in (
            "getAsset", "getTokenAccounts", "getTokenLargestAccounts",
            "getTokenSupply", "getRecentPrioritizationFees",
        ):
            self.assertIn(method, ALLOWED_RPC_METHODS)

    def test_methode_signante_absente_de_la_liste(self):
        self.assertNotIn("sendTransaction", ALLOWED_RPC_METHODS)
        self.assertNotIn("signTransaction", ALLOWED_RPC_METHODS)

    def test_client_sans_cle_retourne_none_sans_appel_reseau(self):
        helius = HeliusAPI(api_key=None)
        self.assertIsNone(helius.get_recent_prioritization_fee_lamports())


if __name__ == "__main__":
    unittest.main()
