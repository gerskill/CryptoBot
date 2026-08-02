"""Les cinq garde-fous critiques issus de l'audit.

Chacun corrige un défaut prouvé sur les données réelles, pas une hypothèse.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.journal import TradeJournal  # noqa: E402
from src.core.learning import MIN_FLOW_TO_TIGHTEN, LearningEngine  # noqa: E402
from src.core.params import ParamsStore  # noqa: E402
from src.core.portfolio import PaperPortfolio  # noqa: E402
from src.core.stats import bootstrap_mean, compare, wilson  # noqa: E402

PARAMS = {
    "version": "test",
    "filters": {"min_liquidity_usd": 25000, "min_age_hours": 1.5, "max_age_hours": 6},
    "exit_rules": {"stop_loss_pct": -10},
    "learning": {},
}


class TestIntervallesDeConfiance(unittest.TestCase):
    """Sans intervalle, 11,1 % sur 36 trades se lit comme une mesure."""

    def test_wilson_reste_dans_les_bornes_sur_petit_echantillon(self):
        # Le défaut de Wald, qu'on évite : sur 1 succès sur 36 il rend une
        # borne basse NÉGATIVE, c'est-à-dire impossible.
        interval = wilson(1, 36)
        self.assertGreaterEqual(interval.low, 0.0)
        self.assertLessEqual(interval.high, 100.0)

    def test_le_cas_reel_du_bot(self):
        interval = wilson(4, 36)
        self.assertAlmostEqual(interval.value, 11.1, places=1)
        self.assertLess(interval.low, 6)
        self.assertGreater(interval.high, 20)

    def test_un_echantillon_minuscule_nest_pas_concluant(self):
        self.assertFalse(wilson(1, 4).conclusive)

    def test_lintervalle_retrecit_avec_lechantillon(self):
        self.assertLess(wilson(100, 1000).width, wilson(10, 100).width)

    def test_bootstrap_refuse_moins_de_trois_valeurs(self):
        self.assertIsNone(bootstrap_mean([1.0, 2.0]))

    def test_bootstrap_reproductible(self):
        data = [1.0, -5.0, 30.0, -4.0, -6.0, 2.0]
        self.assertEqual(bootstrap_mean(data).low, bootstrap_mean(data).low)

    def test_comparaison_conservatrice(self):
        # Deux intervalles qui se chevauchent ne permettent PAS de conclure.
        # On préfère manquer une vraie différence qu'en inventer une : c'est
        # de l'argent qui serait alloué sur cette conclusion.
        self.assertEqual(compare(wilson(5, 20), wilson(6, 20)), "indistinguable")
        self.assertEqual(compare(wilson(19, 20), wilson(1, 20)), "supérieur")


class TestCoupeCircuitDrawdown(unittest.TestCase):
    """Le cooldown voit 3 pertes consécutives, pas une érosion lente."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))

    def _portfolio(self, seuil):
        return PaperPortfolio(
            1000.0, self.journal,
            positions_path=os.path.join(self.tmp, "open.json"),
            cooldown_hours=0, max_drawdown_stop_pct=seuil,
        )

    def _perdre(self, pnl_usd, n=1):
        for i in range(n):
            self.journal._append({
                "position_id": f"p{len(self.journal.read_all())}-{i}",
                "is_final_exit": True, "pnl_usd": pnl_usd, "pnl_pct": pnl_usd,
                "fraction_of_initial": 1.0, "exit_reason": "STOP_LOSS",
            })

    def test_desactive_par_defaut(self):
        self._perdre(-100, 5)
        self.assertFalse(self._portfolio(0).drawdown_breached)

    def test_declenche_au_seuil(self):
        self._perdre(-60, 3)  # -180 sur 1000 = 18%
        portfolio = self._portfolio(15)
        self.assertTrue(portfolio.drawdown_breached)

    def test_bloque_toute_entree(self):
        self._perdre(-60, 3)
        portfolio = self._portfolio(15)

        class C:
            token_address = "x"

        raison = portfolio.can_open(C(), 3)
        self.assertIn("COUPE-CIRCUIT", raison)

    def test_ne_declenche_pas_sous_le_seuil(self):
        self._perdre(-10, 2)
        self.assertFalse(self._portfolio(15).drawdown_breached)


class TestAntiBoucle(unittest.TestCase):
    """6 resserrages en 13 h, aucun relâchement, sur 10-13 trades."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        path = os.path.join(self.tmp, "params.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(PARAMS, fh)
        self.params = ParamsStore(path)
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))

    def _engine(self, flux):
        return LearningEngine(self.params, self.journal, flow=lambda: flux)

    def test_refuse_de_resserrer_quand_le_flux_est_nul(self):
        engine = self._engine(0.0)
        self.assertIsNone(
            engine._bounded_set("filters.min_liquidity_usd", 30000, "test", 10)
        )
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 25000)

    def test_relacher_reste_toujours_permis(self):
        # L'asymétrie EST la correction : bloquer les deux figerait le bot
        # dans l'état étranglé où la boucle l'a mené.
        engine = self._engine(0.0)
        engine._bounded_set("filters.min_liquidity_usd", 15000, "relâche", 10)
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 15000)

    def test_resserrer_reste_permis_quand_le_flux_est_bon(self):
        engine = self._engine(MIN_FLOW_TO_TIGHTEN + 5)
        engine._bounded_set("filters.min_liquidity_usd", 30000, "test", 10)
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 30000)

    def test_baisser_un_plafond_compte_comme_resserrer(self):
        engine = self._engine(0.0)
        engine._bounded_set("filters.max_age_hours", 3, "test", 10)
        self.assertEqual(self.params.get("filters.max_age_hours"), 6)

    def test_monter_un_plafond_est_un_relachement(self):
        engine = self._engine(0.0)
        engine._bounded_set("filters.max_age_hours", 12, "relâche", 10)
        self.assertEqual(self.params.get("filters.max_age_hours"), 12)

    def test_sans_mesure_de_flux_rien_nest_bloque(self):
        # Une donnée absente ne rejette jamais — invariant du projet.
        engine = LearningEngine(self.params, self.journal, flow=None)
        engine._bounded_set("filters.min_liquidity_usd", 30000, "test", 10)
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 30000)

    def test_une_mesure_de_flux_qui_echoue_ne_bloque_pas(self):
        def casse():
            raise RuntimeError("journal illisible")

        engine = LearningEngine(self.params, self.journal, flow=casse)
        engine._bounded_set("filters.min_liquidity_usd", 30000, "test", 10)
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 30000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
