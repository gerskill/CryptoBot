"""Tests du coût réel à la sortie — src/core/exit_fees.py + garde RPC Helius."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.apis.helius import ALLOWED_RPC_METHODS, ForbiddenRpcMethod, HeliusAPI  # noqa: E402
from src.core.exit_fees import (  # noqa: E402
    MIN_SOL_PRICE_USD,
    PartialCostEstimator,
    estimated_partial_cost_pct,
    measure_exit_cost,
)


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


class TestCoutJambePartielle(unittest.TestCase):
    """ADR 012 — les ventes partielles ne sortent plus au prix nu.

    Le bug verrouillé : un trade sorti en TP1 + TP2 + TP3 ne payait que sa
    dernière vente, ce qui rendait tout gagnant à sorties multiples plus beau
    qu'il ne l'était. C'est le chiffre qui décide du passage en réel.
    """

    def test_sous_dix_mesures_aucune_estimation(self):
        """Pas assez de données pour estimer n'est pas coût nul.

        Sous le plancher, on rend `None` et l'appelant garde le prix nu —
        l'ancien comportement — plutôt que d'inventer une médiane sur trois
        points.
        """
        self.assertIsNone(estimated_partial_cost_pct([3.0] * 9))

    def test_dix_mesures_donnent_la_moitie_de_la_mediane(self):
        """La jambe partielle ne paie que sa vente, pas l'aller-retour.

        `round_trip_cost_pct` mesure achat + vente ; la jambe finale porte
        déjà le round-trip complet pour toute la position. Facturer un
        round-trip entier à chaque jambe facturerait l'achat trois fois.
        """
        self.assertEqual(estimated_partial_cost_pct([3.0] * 10), 1.5)

    def test_les_estimations_ne_nourrissent_pas_l_estimation(self):
        """Sinon l'estimation se confirme elle-même, indéfiniment."""

        class JournalFactice:
            path = None

            def read_all(self):
                return [{"exit_cost_pct": 4.0, "exit_cost_estimated": True}] * 50

        self.assertIsNone(PartialCostEstimator(JournalFactice())())

    def test_journal_illisible_retombe_sur_le_prix_nu(self):
        """Une mesure de coût ne fait jamais tomber une clôture."""

        class JournalCasse:
            path = None

            def read_all(self):
                raise OSError("disque en carton")

        self.assertIsNone(PartialCostEstimator(JournalCasse())())
