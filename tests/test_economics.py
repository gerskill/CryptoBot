"""Économie du trade — complète `test_signals_economics.py`.

`test_signals_economics.py` verrouille déjà l'essentiel du comportement
métier de ce module (plancher de TP, veto sur intervalle, sizing par le
coût, `win_rate_for`). Ce fichier couvre ce qui reste : `TradeEconomics`
comme DTO (`as_dict`, immutabilité), le clamping des bornes dans
`minimum_viable_tp`/`expected_net_pct`, les branches de `evaluate` et
`size_for_cost` non exercées ailleurs (aller-retour hors plafond dur,
Jupiter désactivé, devis indisponible en cours de réduction), et les cas
limites (zéro, valeurs négatives, listes/paramètres vides).
"""

import os
import sys
import unittest
from dataclasses import FrozenInstanceError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import economics  # noqa: E402


class TestTradeEconomicsDto(unittest.TestCase):
    def test_as_dict_expose_tous_les_champs(self):
        verdict = economics.TradeEconomics(
            viable=True, reason="ok", round_trip_pct=2.0, expected_net_pct=1.5,
            minimum_tp_pct=10.0, configured_tp_pct=25.0,
        )
        payload = verdict.as_dict()
        self.assertEqual(payload, {
            "viable": True, "reason": "ok", "round_trip_pct": 2.0,
            "expected_net_pct": 1.5, "minimum_tp_pct": 10.0,
            "configured_tp_pct": 25.0,
        })

    def test_champs_optionnels_par_defaut_none(self):
        verdict = economics.TradeEconomics(viable=True, reason="coût non mesuré")
        payload = verdict.as_dict()
        self.assertIsNone(payload["round_trip_pct"])
        self.assertIsNone(payload["expected_net_pct"])
        self.assertIsNone(payload["minimum_tp_pct"])

    def test_est_immuable(self):
        verdict = economics.TradeEconomics(viable=True, reason="ok")
        with self.assertRaises(FrozenInstanceError):
            verdict.viable = False  # type: ignore[misc]


class TestMinimumViableTpClamping(unittest.TestCase):
    def test_win_rate_nul_est_plancher_a_1_pourcent(self):
        # Sans clamp, une division par win_rate=0 lèverait ZeroDivisionError.
        plancher = economics.minimum_viable_tp(2.0, win_rate=0.0)
        self.assertGreater(plancher, 0)

    def test_win_rate_superieur_a_1_est_plafonne(self):
        avec_100 = economics.minimum_viable_tp(2.0, win_rate=1.0)
        avec_150 = economics.minimum_viable_tp(2.0, win_rate=1.5)
        self.assertEqual(avec_100, avec_150)

    def test_win_rate_negatif_est_plancher_comme_zero(self):
        self.assertEqual(
            economics.minimum_viable_tp(2.0, win_rate=-0.5),
            economics.minimum_viable_tp(2.0, win_rate=0.0),
        )

    def test_partial_fraction_hors_bornes_est_clampee(self):
        avec_150 = economics.minimum_viable_tp(2.0, win_rate=0.3, partial_fraction=1.5)
        avec_100 = economics.minimum_viable_tp(2.0, win_rate=0.3, partial_fraction=1.0)
        self.assertEqual(avec_150, avec_100)

    def test_round_trip_nul_ne_force_pas_un_tp_positif_impossible(self):
        # Un coût nul (cas de test synthétique) doit rester non négatif.
        self.assertGreaterEqual(economics.minimum_viable_tp(0.0, win_rate=0.5), 0.0)

    def test_marge_plus_haute_releve_le_plancher(self):
        sans_marge = economics.minimum_viable_tp(2.0, win_rate=0.3, margin=1.0)
        avec_marge = economics.minimum_viable_tp(2.0, win_rate=0.3, margin=2.0)
        self.assertGreater(avec_marge, sans_marge)


class TestExpectedNetPctClamping(unittest.TestCase):
    def test_win_rate_hors_bornes_est_clampe(self):
        self.assertEqual(
            economics.expected_net_pct(25, win_rate=1.5, round_trip_pct=2.0),
            economics.expected_net_pct(25, win_rate=1.0, round_trip_pct=2.0),
        )
        self.assertEqual(
            economics.expected_net_pct(25, win_rate=-1.0, round_trip_pct=2.0),
            economics.expected_net_pct(25, win_rate=0.0, round_trip_pct=2.0),
        )

    def test_loss_pct_absent_vaut_zero(self):
        self.assertEqual(
            economics.expected_net_pct(25, win_rate=0.5, round_trip_pct=2.0),
            economics.expected_net_pct(25, win_rate=0.5, round_trip_pct=2.0, loss_pct=0.0),
        )

    def test_loss_pct_negatif_penalise_lesperance(self):
        sans_perte = economics.expected_net_pct(25, 0.5, 2.0, loss_pct=0.0)
        avec_perte = economics.expected_net_pct(25, 0.5, 2.0, loss_pct=-30.0)
        self.assertLess(avec_perte, sans_perte)


class TestEvaluateBranches(unittest.TestCase):
    def test_aller_retour_au_dela_du_plafond_dur_est_rejete_sans_calcul_de_plancher(self):
        verdict = economics.evaluate(
            round_trip_pct=10.0, take_profit_pct=25, win_rate=0.9,
            max_round_trip_pct=8.0,
        )
        self.assertFalse(verdict.viable)
        self.assertIn("carnet", verdict.reason)
        # Rejeté avant même le calcul du plancher : pas de plancher rapporté.
        self.assertIsNone(verdict.minimum_tp_pct)

    def test_aller_retour_pile_au_plafond_nest_pas_rejete_par_ce_garde_fou(self):
        verdict = economics.evaluate(
            round_trip_pct=8.0, take_profit_pct=1000, win_rate=0.9,
            max_round_trip_pct=8.0,
        )
        self.assertNotIn("carnet", verdict.reason)

    def test_tp_zero_reste_gere_sans_lever(self):
        verdict = economics.evaluate(round_trip_pct=2.0, take_profit_pct=0, win_rate=0.3)
        self.assertIsInstance(verdict, economics.TradeEconomics)

    def test_win_rate_zero_reste_gere_sans_lever(self):
        verdict = economics.evaluate(round_trip_pct=2.0, take_profit_pct=25, win_rate=0.0)
        self.assertFalse(verdict.viable)


class TestSizeForCostBranches(unittest.TestCase):
    class _FakeJupiter:
        def __init__(self, enabled=True, costs=None):
            self.enabled = enabled
            self.costs = list(costs or [])
            self.calls = 0

        def round_trip_cost_pct(self, mint, size_usd, sol_price_usd=0.0):
            self.calls += 1
            return self.costs.pop(0) if self.costs else None

    def test_jupiter_present_mais_desactive_ne_bloque_pas(self):
        jupiter = self._FakeJupiter(enabled=False)
        taille, cost, raison = economics.size_for_cost(jupiter, "mint", 20.0, 4.0)
        self.assertEqual(taille, 20.0)
        self.assertIsNone(cost)
        self.assertEqual(jupiter.calls, 0)
        self.assertIn("indisponible", raison)

    def test_devis_indisponible_en_cours_de_reduction_ne_bloque_pas(self):
        # Le premier essai réussit un devis mais rate le budget ; le second
        # (taille réduite) ne rend AUCUN devis : on ne force pas la taille
        # pleine, on renonce à réduire davantage.
        jupiter = self._FakeJupiter(costs=[9.0, None])
        taille, cost, raison = economics.size_for_cost(
            jupiter, "mint", 20.0, max_round_trip_pct=4.0
        )
        self.assertEqual(taille, 20.0)
        self.assertIsNone(cost)
        self.assertIn("indisponible", raison)

    def test_desired_usd_nul_ne_boucle_pas_indefiniment(self):
        jupiter = self._FakeJupiter(costs=[])
        taille, cost, raison = economics.size_for_cost(
            jupiter, "mint", 0.0, max_round_trip_pct=4.0
        )
        self.assertEqual(taille, 0.0)

    def test_steps_personnalises_sont_respectes(self):
        jupiter = self._FakeJupiter(costs=[10.0, 10.0, 1.0])
        taille, cost, _ = economics.size_for_cost(
            jupiter, "mint", 100.0, max_round_trip_pct=4.0, steps=(1.0, 0.5, 0.1)
        )
        self.assertAlmostEqual(taille, 10.0)
        self.assertEqual(cost, 1.0)


class TestExpectedHitRateBornes(unittest.TestCase):
    def test_tp_sous_le_niveau_le_plus_bas_utilise_le_plus_bas(self):
        self.assertEqual(economics.expected_hit_rate(5), economics.expected_hit_rate(10))

    def test_tp_au_dela_du_niveau_le_plus_haut_utilise_le_plus_haut(self):
        self.assertEqual(economics.expected_hit_rate(500), economics.expected_hit_rate(150))

    def test_interpolation_strictement_monotone_decroissante(self):
        # Interpolation entre 10 et 25 : un TP plus haut n'est jamais plus
        # facile à atteindre.
        self.assertGreater(economics.expected_hit_rate(15), economics.expected_hit_rate(20))


class TestWinRateForCasLimites(unittest.TestCase):
    def test_arm_trades_nul_retombe_sur_lapriori_mesure(self):
        taux, source, _ = economics.win_rate_for(25, arm_win_rate=1.0, arm_trades=0)
        self.assertNotEqual(taux, 1.0)
        self.assertIn("a priori", source)

    def test_pile_au_seuil_de_trades_propres_utilise_le_vecu_du_bras(self):
        taux, source, _ = economics.win_rate_for(
            25, arm_win_rate=0.5, arm_trades=economics.MIN_TRADES_POUR_WIN_RATE_PROPRE
        )
        self.assertEqual(taux, 0.5)
        self.assertIn("vécu du bras", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
