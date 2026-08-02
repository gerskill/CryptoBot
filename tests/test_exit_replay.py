"""Rejeu des règles de sortie sur pic et creux.

Débloque un apprentissage qui était un no-op : la seule règle de
`_adjust_exits` sur le stop loss ne sait que le resserrer, et il est déjà collé
à sa borne basse — `_bounded_set` retournait `None` à chaque appel.

Les tests encodent aussi les LIMITES du rejeu, pas seulement ce qu'il sait
faire : une simulation dont on oublie l'angle mort finit par être crue.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.journal import TradeJournal  # noqa: E402
from src.core.learning import LearningEngine  # noqa: E402
from src.core.params import ParamsStore  # noqa: E402

EXITS = {
    "stop_loss_pct": -10,
    "stop_loss_slippage_buffer_pct": 0.0,
    "take_profit_1": 100,
    "take_profit_2": 300,
    "take_profit_3": 500,
    "partial_sell_tp1_pct": 0.5,
    "partial_sell_tp2_pct": 0.5,
}
PARAMS = {"version": "test", "exit_rules": dict(EXITS), "filters": {}, "learning": {}}


def position(peak, trough, pnl_pct=-14.0, size=12.5, reason="STOP_LOSS (-14.0%)",
             minutes_to_peak=None, minutes_to_trough=None, **extra):
    row = {
        "is_final_exit": True,
        "peak_pct": peak,
        "trough_pct": trough,
        "pnl_pct": pnl_pct,
        "pnl_usd": round(size * pnl_pct / 100, 4),
        "position_size": size,
        "exit_reason": reason,
        "minutes_to_peak": minutes_to_peak,
        "minutes_to_trough": minutes_to_trough,
    }
    row.update(extra)
    return row


class TestSimulateExits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        path = os.path.join(self.tmp, "params.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(PARAMS, fh)
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))
        self.engine = LearningEngine(ParamsStore(path), self.journal)

    def _run(self, rows, exits=None, ambiguity="resolve"):
        return self.engine.simulate_exits(rows, exits or EXITS, ambiguity)

    def test_stop_touche_sans_pic(self):
        out = self._run([position(peak=5.0, trough=-30.0)])
        self.assertEqual(out["by_outcome"], {"SL": 1})
        # -10 + glissement mesuré (repli -4.4 faute d'échantillon)
        self.assertAlmostEqual(out["pnl_per_trade"], 12.5 * -14.4 / 100, places=3)

    def test_tp1_touche_sans_stop(self):
        out = self._run([position(peak=120.0, trough=-3.0, pnl_pct=48.0)])
        self.assertEqual(out["by_outcome"], {"TP1": 1})
        # 0.5 * 100 + 0.5 * (-2.9 de repli breakeven)
        self.assertAlmostEqual(out["pnl_per_trade"], 12.5 * 48.55 / 100, places=3)

    def test_echelle_tp2_quand_le_pic_va_plus_loin(self):
        out = self._run([position(peak=350.0, trough=-3.0, pnl_pct=200.0)])
        self.assertEqual(out["by_outcome"], {"TP_LADDER": 1})
        self.assertGreater(out["pnl_per_trade"], 12.5 * 100 / 100)

    def test_aucun_seuil_franchi_garde_le_resultat_reel(self):
        # Un TIME_STOP entre les deux seuils ne change pas quand on bouge
        # SL et TP : son P&L réel doit être conservé tel quel.
        rows = [position(peak=40.0, trough=-5.0, pnl_pct=22.0,
                         reason="TIME_STOP (240 min, +22.0%)")]
        out = self._run(rows)
        self.assertEqual(out["by_outcome"], {"UNCHANGED": 1})
        self.assertAlmostEqual(out["total_pnl_usd"], rows[0]["pnl_usd"])
        self.assertEqual(out["changed"], 0)

    def test_positions_non_instrumentees_sont_exclues_pas_nulles(self):
        rows = [position(peak=None, trough=None), position(peak=5.0, trough=-30.0)]
        out = self._run(rows)
        self.assertEqual(out["skipped"], 1)
        self.assertEqual(out["coverage"], 1)

    def test_un_pic_a_zero_est_une_vraie_valeur(self):
        # 0.0 signifie « n'est jamais monté », pas « donnée absente ».
        out = self._run([position(peak=0.0, trough=-30.0)])
        self.assertEqual(out["skipped"], 0)
        self.assertEqual(out["by_outcome"], {"SL": 1})

    def test_le_trailing_nest_pas_simule(self):
        # Faire varier trailing_stop_* ne doit rien changer : c'est hors
        # périmètre, et un rejeu qui prétendrait le simuler mentirait.
        rows = [position(peak=120.0, trough=-30.0, minutes_to_peak=10,
                         minutes_to_trough=50)]
        serre = self._run(rows, {**EXITS, "trailing_stop_distance_pct": 20})
        large = self._run(rows, {**EXITS, "trailing_stop_distance_pct": 80})
        self.assertEqual(serre["pnl_per_trade"], large["pnl_per_trade"])

    def test_max_hold_est_hors_perimetre(self):
        rows = [position(peak=5.0, trough=-30.0)]
        court = self._run(rows, {**EXITS, "max_hold_time_minutes": 30})
        long = self._run(rows, {**EXITS, "max_hold_time_minutes": 600})
        self.assertEqual(court["pnl_per_trade"], long["pnl_per_trade"])


class TestAmbiguite(unittest.TestCase):
    """3 des 4 gagnants réels sont ambigus : la politique décide du verdict."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        path = os.path.join(self.tmp, "params.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(PARAMS, fh)
        self.engine = LearningEngine(
            ParamsStore(path), TradeJournal(os.path.join(self.tmp, "trades.jsonl"))
        )

    def _callcat(self, **extra):
        """peak +133,94 ET trough -10,83 : les deux seuils sont franchis."""
        return position(peak=133.94, trough=-10.83, pnl_pct=49.46, size=25.0,
                        reason="BREAKEVEN_STOP (-1.8%)", **extra)

    def test_le_cas_est_bien_detecte_comme_ambigu(self):
        out = self.engine.simulate_exits([self._callcat()], EXITS)
        self.assertEqual(out["ambiguous"], 1)

    def test_pessimiste_et_optimiste_divergent(self):
        # La limite honnête, verrouillée en test : « rapporter les deux et
        # arbitrer au pessimiste » ne marche pas, les deux ne disent pas la
        # même chose sur ces données.
        rows = [self._callcat()]
        pess = self.engine.simulate_exits(rows, EXITS, "pessimistic")
        opti = self.engine.simulate_exits(rows, EXITS, "optimistic")
        self.assertEqual(pess["by_outcome"], {"SL": 1})
        self.assertEqual(opti["by_outcome"], {"TP1": 1})
        self.assertNotAlmostEqual(pess["pnl_per_trade"], opti["pnl_per_trade"])

    def test_resolve_nutilise_plus_la_raison_de_sortie(self):
        # FUITE CORRIGÉE. Déduire « BREAKEVEN_STOP donc TP1 touché » est
        # circulaire : cette raison a été produite par les règles qu'on
        # évalue. Sans horodatage du creux, le rejeu doit être PESSIMISTE —
        # ne pas savoir doit coûter, pas rapporter.
        out = self.engine.simulate_exits([self._callcat()], EXITS, "resolve")
        self.assertEqual(out["by_outcome"], {"SL": 1})
        self.assertEqual(out["degraded"], 1, "la déduction doit être signalée")

    def test_les_positions_horodatees_ne_sont_pas_degradees(self):
        tot = self._callcat(minutes_to_peak=10.0, minutes_to_trough=60.0)
        out = self.engine.simulate_exits([tot], EXITS, "resolve")
        self.assertEqual(out["degraded"], 0)
        self.assertEqual(out["by_outcome"], {"TP1": 1})

    def test_les_dates_tranchent_exactement_quand_elles_existent(self):
        # Instrumentation complète : plus d'heuristique, plus de fuite.
        tard = self._callcat(minutes_to_peak=60.0, minutes_to_trough=10.0)
        out = self.engine.simulate_exits([tard], EXITS, "resolve")
        self.assertEqual(out["by_outcome"], {"SL": 1}, "creux avant pic -> stop")

    def test_les_dates_priment_sur_la_politique(self):
        tot = self._callcat(minutes_to_peak=10.0, minutes_to_trough=60.0)
        out = self.engine.simulate_exits([tot], EXITS, "pessimistic")
        self.assertEqual(out["by_outcome"], {"TP1": 1})


class TestValidationDesSorties(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "params.json")
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(PARAMS, fh)
        self.params = ParamsStore(self.path)
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))
        self.engine = LearningEngine(self.params, self.journal)

    def _log(self, count, **kwargs):
        for index in range(count):
            row = position(**kwargs)
            row["position_id"] = f"p{len(self.journal.read_all())}-{index}"
            self.journal._append(row)

    def test_pas_de_verdict_sous_la_couverture_minimale(self):
        self._log(5, peak=5.0, trough=-30.0)
        self.assertIsNone(self.engine.validate_exit_changes(dict(EXITS)))

    def test_ne_touche_a_rien_sous_la_couverture_minimale(self):
        self._log(5, peak=5.0, trough=-30.0)
        self.params.set("exit_rules.stop_loss_pct", -30, log=False)
        self.engine.validate_exit_changes(dict(EXITS))
        self.assertEqual(self.params.get("exit_rules.stop_loss_pct"), -30)

    def test_annule_un_changement_sans_amelioration(self):
        # 20 positions qui touchent le stop dans les deux configurations :
        # élargir le stop ne fait qu'aggraver la perte simulée.
        self._log(20, peak=2.0, trough=-60.0)
        self.params.set("exit_rules.stop_loss_pct", -40, log=False)
        verdict = self.engine.validate_exit_changes(dict(EXITS))
        self.assertIn("annulées", verdict)
        self.assertEqual(self.params.get("exit_rules.stop_loss_pct"), -10)

    def test_valide_un_changement_qui_ameliore(self):
        # Le creux s'arrête à -12 : un stop à -20 ne se déclenche plus et la
        # position atteint son TP1 au lieu d'être coupée.
        self._log(20, peak=150.0, trough=-12.0, pnl_pct=-14.0,
                  minutes_to_peak=40.0, minutes_to_trough=5.0)
        self.params.set("exit_rules.stop_loss_pct", -20, log=False)
        verdict = self.engine.validate_exit_changes(dict(EXITS))
        self.assertIn("validées", verdict)
        self.assertEqual(self.params.get("exit_rules.stop_loss_pct"), -20)


class TestGrilleEtRecherche(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        path = os.path.join(self.tmp, "params.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(PARAMS, fh)
        self.params = ParamsStore(path)
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))
        self.engine = LearningEngine(self.params, self.journal)

    def _log(self, count, **kwargs):
        for index in range(count):
            row = position(**kwargs)
            row["position_id"] = f"p{index}"
            self.journal._append(row)

    def test_la_grille_couvre_toute_la_combinatoire(self):
        rows = [position(peak=50.0, trough=-25.0)]
        self.assertEqual(len(self.engine.exit_grid(rows)), 36)

    def test_la_grille_est_triee_par_pnl(self):
        rows = [position(peak=50.0, trough=-25.0)]
        grid = self.engine.exit_grid(rows)
        self.assertEqual(
            [r["pnl_per_trade"] for r in grid],
            sorted((r["pnl_per_trade"] for r in grid), reverse=True),
        )

    def test_recherche_muette_sous_la_couverture(self):
        self._log(5, peak=150.0, trough=-12.0)
        self.assertEqual(self.engine._search_exits(), [])

    def test_recherche_respecte_les_bornes_de_la_strategie(self):
        # Bornes resserrées volontairement : la recherche ne doit pas en sortir.
        engine = LearningEngine(
            self.params, self.journal,
            bounds={"exit_rules.stop_loss_pct": (-15.0, -10.0)},
        )
        self._log(20, peak=150.0, trough=-50.0, minutes_to_peak=40.0,
                  minutes_to_trough=5.0)
        engine._search_exits()
        self.assertGreaterEqual(self.params.get("exit_rules.stop_loss_pct"), -15)

    def test_bornes_par_defaut_inchangees_sans_override(self):
        self.assertEqual(
            self.engine.bounds["exit_rules.stop_loss_pct"], (-50, -10)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
