"""Isolation des stratégies.

La promesse du multi-bras : chaque stratégie apprend de SES trades, dans SON
fichier. Une contamination entre bras rendrait la comparaison sans valeur —
et pire, invisible. Ces tests verrouillent l'étanchéité.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from src.core import arm as arm_module  # noqa: E402
from src.core.arm import (  # noqa: E402
    ManifestError,
    attach_portfolios,
    bootstrap_arms,
    materialise_arm_params,
)
from src.core.params import ParamsStore  # noqa: E402

BASE = {
    "version": "2.0",
    "mode": "PAPER",
    "filters": {"min_liquidity_usd": 25000, "min_holders": 75},
    "exit_rules": {"stop_loss_pct": -10, "take_profit_1": 100},
    "scoring_weights": {"liquidity": 1.0},
    "scan": {"alpha_score_entry_threshold": 75},
    "learning": {"total_trades": 36, "parameter_adjustment_history": [{"param_name": "vieux"}]},
}


class ArmsTestCase(unittest.TestCase):
    """Redirige tous les chemins vers un dossier jetable."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = {
            key: getattr(settings, key)
            for key in ("PARAMS_PATH", "TRADES_LOG_PATH", "SHADOW_LOG_PATH",
                        "POSITIONS_PATH", "STRATEGIES_PATH", "ARMS_CONFIG_DIR",
                        "ARMS_DATA_DIR", "FUNNEL_LOG_PATH")
        }
        # Sans cette redirection, `_flow_reader` et `_inactivity_reader` lisent
        # le VRAI journal d'entonnoir de production : 11 Mo par appel, et des
        # tests dont le résultat dépend de ce que le bot a fait cette nuit.
        settings.FUNNEL_LOG_PATH = os.path.join(self.tmp, "funnel_log.jsonl")
        settings.PARAMS_PATH = os.path.join(self.tmp, "params.json")
        settings.TRADES_LOG_PATH = os.path.join(self.tmp, "trades_log.jsonl")
        settings.SHADOW_LOG_PATH = os.path.join(self.tmp, "shadow_log.jsonl")
        settings.POSITIONS_PATH = os.path.join(self.tmp, "open_positions.json")
        settings.STRATEGIES_PATH = os.path.join(self.tmp, "strategies.json")
        settings.ARMS_CONFIG_DIR = os.path.join(self.tmp, "arms")
        settings.ARMS_DATA_DIR = os.path.join(self.tmp, "data_arms")
        with open(settings.PARAMS_PATH, "w", encoding="utf-8") as fh:
            json.dump(BASE, fh)

    def tearDown(self):
        for key, value in self._saved.items():
            setattr(settings, key, value)

    def _manifest(self, arms):
        with open(settings.STRATEGIES_PATH, "w", encoding="utf-8") as fh:
            json.dump({"arms": arms}, fh)

    def _deux_bras(self):
        self._manifest([
            {"name": "baseline", "role": "voter", "capital_pct": 0.5, "notify": "all"},
            {"name": "runner", "role": "voter", "capital_pct": 0.5, "notify": "none",
             "overrides": {"exit_rules.stop_loss_pct": -35,
                           "filters.min_liquidity_usd": 40000}},
        ])
        return bootstrap_arms()


class TestChemins(ArmsTestCase):
    def test_baseline_garde_les_chemins_historiques(self):
        # Toute la rétrocompatibilité tient là : pas de migration des 36 trades.
        paths = settings.arm_paths("baseline")
        self.assertEqual(paths["trades"], settings.TRADES_LOG_PATH)
        self.assertEqual(paths["params"], settings.PARAMS_PATH)
        self.assertEqual(paths["positions"], settings.POSITIONS_PATH)

    def test_les_autres_bras_ont_leurs_propres_fichiers(self):
        paths = settings.arm_paths("runner")
        self.assertNotEqual(paths["trades"], settings.TRADES_LOG_PATH)
        self.assertIn("runner", paths["trades"])
        self.assertIn("runner", paths["params"])


class TestMaterialisation(ArmsTestCase):
    def test_applique_les_overrides_en_chemin_pointe(self):
        path = materialise_arm_params("runner", BASE, {"exit_rules.stop_loss_pct": -35})
        with open(path, encoding="utf-8") as fh:
            document = json.load(fh)
        self.assertEqual(document["exit_rules"]["stop_loss_pct"], -35)
        self.assertEqual(document["exit_rules"]["take_profit_1"], 100, "le reste est hérité")

    def test_nherite_pas_de_lhistorique_du_temoin(self):
        path = materialise_arm_params("runner", BASE, {})
        with open(path, encoding="utf-8") as fh:
            document = json.load(fh)
        self.assertEqual(document["learning"], {})

    def test_ne_regenere_jamais_un_fichier_existant(self):
        # Sinon chaque redémarrage effacerait ce que le bras a appris et les
        # overrides du manifeste deviendraient un plafond invisible.
        path = materialise_arm_params("runner", BASE, {"exit_rules.stop_loss_pct": -35})
        store = ParamsStore(path)
        store.set("exit_rules.stop_loss_pct", -22, log=False)
        materialise_arm_params("runner", BASE, {"exit_rules.stop_loss_pct": -35})
        self.assertEqual(ParamsStore(path).get("exit_rules.stop_loss_pct"), -22)


class TestManifeste(ArmsTestCase):
    def test_refuse_un_capital_qui_ne_somme_pas_a_un(self):
        self._manifest([
            {"name": "baseline", "capital_pct": 0.5},
            {"name": "runner", "capital_pct": 0.9},
        ])
        with self.assertRaises(ManifestError):
            bootstrap_arms()

    def test_refuse_des_noms_en_double(self):
        self._manifest([
            {"name": "baseline", "capital_pct": 0.5},
            {"name": "baseline", "capital_pct": 0.5},
        ])
        with self.assertRaises(ManifestError):
            bootstrap_arms()

    def test_refuse_une_politique_de_notification_inconnue(self):
        self._manifest([{"name": "baseline", "capital_pct": 1.0, "notify": "spam"}])
        with self.assertRaises(ManifestError):
            bootstrap_arms()

    def test_ignore_les_bras_desactives(self):
        self._manifest([
            {"name": "baseline", "capital_pct": 1.0},
            {"name": "runner", "capital_pct": 5.0, "enabled": False},
        ])
        self.assertEqual([a.name for a in bootstrap_arms()], ["baseline"])

    def test_sans_manifeste_un_seul_bras_temoin(self):
        arms = bootstrap_arms()
        self.assertEqual(len(arms), 1)
        self.assertTrue(arms[0].is_baseline)

    def test_le_bras_consensus_est_evalue_en_dernier(self):
        # Il lit le décompte des votes : il lui faut les votants d'abord.
        self._manifest([
            {"name": "consensus", "role": "consensus", "capital_pct": 0.5,
             "min_confluence": 2},
            {"name": "baseline", "role": "voter", "capital_pct": 0.5, "notify": "all"},
        ])
        self.assertEqual([a.name for a in bootstrap_arms()][-1], "consensus")


class TestIsolation(ArmsTestCase):
    def test_chaque_bras_a_ses_propres_regles(self):
        baseline, runner = self._deux_bras()
        self.assertEqual(baseline.params.get("exit_rules.stop_loss_pct"), -10)
        self.assertEqual(runner.params.get("exit_rules.stop_loss_pct"), -35)
        self.assertEqual(runner.filters()["min_liquidity_usd"], 40000)

    def test_lapprentissage_dun_bras_nimpacte_pas_lautre(self):
        baseline, runner = self._deux_bras()
        path = settings.arm_paths("runner")["params"]
        with open(path, encoding="utf-8") as fh:
            before = json.load(fh)
        baseline.params.set("filters.min_liquidity_usd", 99000, "test")
        with open(path, encoding="utf-8") as fh:
            after = json.load(fh)
        self.assertEqual(before, after)
        self.assertEqual(runner.filters()["min_liquidity_usd"], 40000)

    def test_les_historiques_dajustement_sont_separes(self):
        baseline, runner = self._deux_bras()
        runner.params.set("filters.min_holders", 200, "essai runner")
        baseline_history = baseline.params.get("learning.parameter_adjustment_history", [])
        runner_history = runner.params.get("learning.parameter_adjustment_history", [])
        self.assertEqual(len(runner_history), 1)
        self.assertNotIn(
            "essai runner", [entry.get("reason") for entry in baseline_history]
        )

    def test_chaque_bras_ecrit_dans_son_propre_journal(self):
        baseline, runner = self._deux_bras()
        self.assertNotEqual(baseline.journal.path, runner.journal.path)
        self.assertNotEqual(baseline.shadow.path, runner.shadow.path)

    def test_les_bornes_du_manifeste_arrivent_au_learning(self):
        self._manifest([
            {"name": "baseline", "capital_pct": 0.5, "notify": "all"},
            {"name": "runner", "capital_pct": 0.5,
             "bounds": {"exit_rules.stop_loss_pct": [-60, -15]}},
        ])
        baseline, runner = bootstrap_arms()
        self.assertEqual(runner.learning.bounds["exit_rules.stop_loss_pct"], (-60.0, -15.0))
        self.assertEqual(baseline.learning.bounds["exit_rules.stop_loss_pct"], (-50, -10))

    def test_chaque_bras_recoit_une_mise_independante(self):
        # capital_pct n'est PAS appliqué en PAPER : découper rétroactivement
        # ferait démarrer le témoin à -16,46 $ (mise 150 $, journal -166 $),
        # et un bras à 5 % prendrait des positions dix fois plus petites,
        # affichant un P&L moindre pour une raison d'allocation.
        from src.core.arm import attach_portfolios

        baseline, runner = self._deux_bras()
        attach_portfolios([baseline, runner], capital_total=1000.0)
        self.assertEqual(baseline.portfolio.baseline, 1000.0)
        self.assertEqual(runner.portfolio.baseline, 1000.0)

    def test_les_portefeuilles_ecrivent_dans_des_fichiers_distincts(self):
        from src.core.arm import attach_portfolios

        baseline, runner = self._deux_bras()
        attach_portfolios([baseline, runner], capital_total=1000.0)
        self.assertNotEqual(
            baseline.portfolio.positions_path, runner.portfolio.positions_path
        )

    def test_le_label_du_temoin_reste_vide(self):
        # Sa sortie console ne doit pas changer d'un poil.
        baseline, runner = self._deux_bras()
        self.assertEqual(baseline.label, "")
        self.assertEqual(runner.label, " [runner]")


class _FauxDex:
    """Compte les appels : la collecte est partagée, le shadow doit l'être aussi."""

    def __init__(self, prix: float = 3.0):
        self.prix = prix
        self.appels: list[list[str]] = []

    def get_tokens_data(self, addresses):
        self.appels.append(list(addresses))
        return {a: [{"priceUsd": self.prix}] for a in addresses}


def _rejet(symbol: str, reason: str, price: float = 1.0):
    from src.core.models import Candidate

    return Candidate(
        token_address=f"addr_{symbol}", symbol=symbol, name=symbol, chain="solana",
        price_usd=price, liquidity_usd=12000, rejected_reason=reason,
    )


class _FausseEvaluation:
    def __init__(self, rejected):
        self.result = type("R", (), {"rejected": rejected})()


class TestShadowParBras(ArmsTestCase):
    """Chaque bras suit SES rejets.

    LE BUG VERROUILLÉ ICI. `bootstrap_arms` donnait un `ShadowTracker` à
    chaque bras et le passait à son `LearningEngine`, mais `_track_rejections`
    n'alimentait que `self.shadow`. Mesuré au 2026-08-02 : 399 rejets jugés
    pour le témoin, ZÉRO pour les six autres — leur fichier n'existait même
    pas. `_relax_from_shadow`, seul contrepoids au resserrage des filtres, ne
    pouvait donc rien rendre pour eux.

    Un bras rejette sur SES seuils : le shadow du témoin ne dit rien de ce que
    `quality` ou `narrative` ont écarté.
    """

    def _loop(self, arms, evaluations, dex):
        """Stand-in : `_track_rejections` n'a besoin que de ces quatre champs.

        Construire un `AlphaLoop` complet ouvrirait des sockets et lirait le
        vrai `.env` — on teste la méthode, pas le constructeur.
        """
        from src.main import AlphaLoop

        faux = type("L", (), {})()
        faux.arms = arms
        faux._last_evaluations = evaluations
        faux.dex = dex
        faux.shadow = next(a.shadow for a in arms if a.is_baseline)
        AlphaLoop._track_rejections(faux, evaluations["baseline"].result)
        return faux

    def test_chaque_bras_enregistre_ses_propres_rejets(self):
        baseline, runner = self._deux_bras()
        evaluations = {
            "baseline": _FausseEvaluation([_rejet("A", "liquidité 12000 < 15000")]),
            "runner": _FausseEvaluation([
                _rejet("A", "liquidité 12000 < 15000"),
                _rejet("B", "liquidité 12000 < 40000"),
            ]),
        }

        self._loop([baseline, runner], evaluations, _FauxDex())

        self.assertEqual(len(baseline.shadow.tracked_addresses), 1)
        self.assertEqual(len(runner.shadow.tracked_addresses), 2)

    def test_les_logs_des_bras_sont_separes(self):
        baseline, runner = self._deux_bras()
        self.assertNotEqual(baseline.shadow.path, runner.shadow.path)

    def test_le_prix_est_interroge_une_seule_fois_pour_tous_les_bras(self):
        """La collecte est partagée ; interroger DexScreener par bras
        multiplierait par sept le coût que ce partage existe pour éviter."""
        import time

        baseline, runner = self._deux_bras()
        evaluations = {
            "baseline": _FausseEvaluation([_rejet("A", "liquidité 12000 < 15000")]),
            "runner": _FausseEvaluation([_rejet("A", "liquidité 12000 < 15000")]),
        }
        self._loop([baseline, runner], evaluations, _FauxDex())

        # Vieillir les suivis pour qu'ils passent en revue au cycle suivant.
        for arm in (baseline, runner):
            for entry in arm.shadow._tracked.values():
                entry["rejected_at"] = time.time() - 3600

        dex = _FauxDex(prix=3.0)
        self._loop([baseline, runner], evaluations, dex)

        self.assertEqual(len(dex.appels), 1)
        self.assertEqual(dex.appels[0], ["addr_A"])
        # Et le prix a bien été distribué aux DEUX bras.
        for arm in (baseline, runner):
            self.assertEqual(arm.shadow._tracked["addr_A"]["peak_price"], 3.0)

    def test_pas_de_double_ecriture_sur_le_journal_du_temoin(self):
        """`arm_paths('baseline')['shadow']` EST `SHADOW_LOG_PATH`. Deux
        `ShadowTracker` sur ce fichier écriraient deux verdicts par rejet."""
        baseline, _ = self._deux_bras()
        self.assertEqual(baseline.shadow.path, settings.SHADOW_LOG_PATH)


class TestRelachementDepuisLeCycle(ArmsTestCase):
    """Le relâchement sur inactivité doit partir du CYCLE, pas d'un trade.

    LA POULE ET L'ŒUF VERROUILLÉE ICI. `learning.run` n'est appelé que par
    `_after_trade_closed`. Un bras qui n'ouvre jamais de position n'en ferme
    jamais, donc `run` n'est jamais appelé, donc le relâchement sur inactivité
    serait inatteignable EXACTEMENT pour les bras qu'il doit débloquer.
    `narrative` était à 0 entrée sur 927 cycles.

    Même famille que les poules-et-œufs corrigées dans `economics` et dans la
    porte des 15 trades, un cran plus haut : cette fois ce n'est pas un seuil
    qui bloque, c'est le point d'appel.
    """

    def _loop(self, arms, cycle):
        from src.main import AlphaLoop

        faux = type("L", (), {})()
        faux.arms = arms
        faux.paused = False
        faux.cycle_count = cycle
        AlphaLoop._relax_inactive_arms(faux)
        return faux

    def _bras_inactif(self):
        baseline, runner = self._deux_bras()
        for arm in (baseline, runner):
            arm.learning.inactivity = lambda: (10_000, ["liquidité 9000$ < 40000$"])
        return baseline, runner

    def test_un_bras_a_zero_trade_est_bien_atteint(self):
        baseline, runner = self._bras_inactif()
        from src.main import INACTIVITY_CHECK_EVERY

        self.assertEqual(len(runner.journal.read_positions()), 0)
        self._loop([baseline, runner], cycle=INACTIVITY_CHECK_EVERY)

        self.assertEqual(runner.params.get("filters.min_liquidity_usd"), 35000)

    def test_le_temoin_reste_gele(self):
        baseline, runner = self._bras_inactif()
        from src.main import INACTIVITY_CHECK_EVERY

        avant = baseline.params.get("filters.min_liquidity_usd")
        self._loop([baseline, runner], cycle=INACTIVITY_CHECK_EVERY)

        self.assertEqual(baseline.params.get("filters.min_liquidity_usd"), avant)

    def test_hors_cadence_rien_ne_bouge(self):
        baseline, runner = self._bras_inactif()
        from src.main import INACTIVITY_CHECK_EVERY

        avant = runner.params.get("filters.min_liquidity_usd")
        self._loop([baseline, runner], cycle=INACTIVITY_CHECK_EVERY + 1)

        self.assertEqual(runner.params.get("filters.min_liquidity_usd"), avant)

    def test_en_pause_rien_ne_bouge(self):
        from src.main import AlphaLoop, INACTIVITY_CHECK_EVERY

        baseline, runner = self._bras_inactif()
        avant = runner.params.get("filters.min_liquidity_usd")

        faux = type("L", (), {})()
        faux.arms = [baseline, runner]
        faux.paused = True
        faux.cycle_count = INACTIVITY_CHECK_EVERY
        AlphaLoop._relax_inactive_arms(faux)

        self.assertEqual(runner.params.get("filters.min_liquidity_usd"), avant)

    def test_la_cadence_ne_peut_pas_manquer_un_declenchement(self):
        """Il faut 300 cycles sans entrée pour déclencher ; contrôler tous les
        50 cycles ne peut donc rien rater."""
        from src.core.learning import INACTIVITY_CYCLES
        from src.main import INACTIVITY_CHECK_EVERY

        self.assertLess(INACTIVITY_CHECK_EVERY, INACTIVITY_CYCLES)


class TestStatsDeLaFlotte(ArmsTestCase):
    """Le panneau affichait le TÉMOIN et le présentait comme le bot.

    LE BUG VERROUILLÉ ICI. `stats` est le portefeuille du bras témoin, qui est
    GELÉ. Il montrait « 4 gagnants sur 39 », un profit factor de 0,28 et un
    drawdown de 17,4 % figés depuis 16 h, pendant que les six autres bras
    accumulaient 27 gagnants sur 93 trades. Les gains étaient bien écrits dans
    les journaux : c'est la LECTURE qui regardait le mauvais portefeuille.
    """

    def _flotte(self, arms):
        from src.main import AlphaLoop

        faux = type("L", (), {})()
        faux.arms = arms
        return AlphaLoop._aggregate_stats(faux)

    def _avec_portefeuilles(self):
        arms = self._deux_bras()
        attach_portfolios(arms, capital_total=1000.0)
        return arms

    def _avec_trades(self):
        baseline, runner = self._avec_portefeuilles()
        # Le témoin perd, le second gagne — le cas exact du bug.
        for arm, pnls in ((baseline, [-4.0] * 3), (runner, [10.0, 10.0, -2.0])):
            for index, pnl in enumerate(pnls):
                with open(arm.journal.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "position_id": f"{arm.name}{index}", "token": "T",
                        "pnl_usd": pnl, "pnl_pct": pnl, "is_final_exit": True,
                    }) + "\n")
        return baseline, runner

    def test_compte_les_gagnants_de_tous_les_bras(self):
        agg = self._flotte(list(self._avec_trades()))
        self.assertEqual(agg["closed_trades"], 6)
        self.assertEqual(agg["wins"], 2)

    def test_le_win_rate_nest_pas_celui_du_temoin(self):
        baseline, runner = self._avec_trades()
        agg = self._flotte([baseline, runner])
        self.assertEqual(baseline.portfolio.stats()["win_rate"], 0.0)
        self.assertEqual(agg["win_rate"], round(100 * 2 / 6, 1))

    def test_le_profit_factor_agrege_les_gains_et_les_pertes(self):
        agg = self._flotte(list(self._avec_trades()))
        # gains 20, pertes 3*4 + 2 = 14
        self.assertEqual(agg["profit_factor"], round(20 / 14, 2))

    def test_le_win_rate_nest_pas_une_moyenne_des_taux(self):
        """Une moyenne pondérerait `quality` (2 trades) comme le témoin (39).
        Le taux se recalcule sur les journaux."""
        baseline, runner = self._avec_portefeuilles()
        for index in range(20):
            with open(baseline.journal.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"position_id": f"b{index}", "token": "T",
                                     "pnl_usd": -1.0, "is_final_exit": True}) + "\n")
        with open(runner.journal.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"position_id": "r0", "token": "T",
                                 "pnl_usd": 1.0, "is_final_exit": True}) + "\n")

        agg = self._flotte([baseline, runner])
        # Moyenne des taux = (0% + 100%) / 2 = 50%. Le vrai taux est 1/21.
        self.assertEqual(agg["win_rate"], round(100 / 21, 1))

    def test_le_drawdown_ne_sadditionne_pas(self):
        agg = self._flotte(list(self._avec_trades()))
        pires = [a.portfolio.stats()["max_drawdown_pct"] for a in self._avec_portefeuilles()]
        self.assertLessEqual(agg["worst_arm_drawdown_pct"], max(pires) + 100)
        self.assertIn("worst_arm_drawdown_pct", agg)

    def test_sans_aucun_trade_rien_ne_divise_par_zero(self):
        agg = self._flotte(list(self._avec_portefeuilles()))
        self.assertEqual(agg["closed_trades"], 0)
        self.assertEqual(agg["win_rate"], 0.0)
        self.assertEqual(agg["profit_factor"], 0.0)


class TestManifesteLivre(unittest.TestCase):
    """Le manifeste réellement livré doit être cohérent."""

    def test_le_manifeste_du_depot_est_valide(self):
        arms = arm_module.load_manifest()
        actifs = [a for a in arms if a.get("enabled", True)]
        self.assertAlmostEqual(sum(a["capital_pct"] for a in actifs), 1.0, places=6)
        self.assertIn("baseline", [a["name"] for a in actifs])
        consensus = [a for a in actifs if a.get("role") == "consensus"]
        for entry in consensus:
            self.assertGreaterEqual(entry.get("min_confluence", 0), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
