"""Briques sans fuite du hors-échantillon (audit I10 a, c, d, e).

Ces briques empêchent le rejeu de sorties de choisir et de juger un réglage sur
les mêmes trades : valeur réellement écrite (a), mesure bornée dans le temps
(c), P&L par position (d), découpage chronologique dédoublonné (e).
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.journal import TradeJournal  # noqa: E402
from src.core.learning import (  # noqa: E402
    HOLDOUT_TRAIN_FRACTION,
    LearningEngine,
    chronological_split,
)
from src.core.params import ParamsStore  # noqa: E402

EXITS = {
    "stop_loss_pct": -20,
    "stop_loss_slippage_buffer_pct": 5.0,
    "take_profit_1": 100,
    "take_profit_2": 300,
    "take_profit_3": 500,
    "trailing_stop_activation": 200,
    "partial_sell_tp1_pct": 0.5,
    "partial_sell_tp2_pct": 0.5,
}
PARAMS = {"version": "test", "exit_rules": dict(EXITS), "filters": {}, "learning": {}}


def position(jour, token="TOK", peak=5.0, trough=-30.0, pnl_pct=-14.0, size=12.5,
             **extra):
    row = {
        "is_final_exit": True,
        "token_address": token,
        "timestamp_entry": f"2026-11-{jour:02d}T08:00:00+00:00",
        "timestamp_exit": f"2026-11-{jour:02d}T09:00:00+00:00",
        "peak_pct": peak,
        "trough_pct": trough,
        "pnl_pct": pnl_pct,
        "pnl_usd": round(size * pnl_pct / 100, 4),
        "position_size": size,
        "exit_reason": "STOP_LOSS (-14.0%)",
    }
    row.update(extra)
    return row


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        path = os.path.join(self.tmp, "params.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(PARAMS, fh)
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))
        self.engine = LearningEngine(ParamsStore(path), self.journal)


class TestDecoupageChronologique(unittest.TestCase):
    def test_neuf_positions_melangees_donnent_six_et_trois_triees(self):
        jours = [5, 1, 9, 3, 7, 2, 8, 4, 6]
        positions = [position(j, token=f"T{j}") for j in jours]
        train, test, exclues = chronological_split(positions)
        self.assertEqual((len(train), len(test), exclues), (6, 3, 0))
        self.assertEqual([p["token_address"] for p in train],
                         [f"T{j}" for j in range(1, 7)])
        self.assertEqual([p["token_address"] for p in test],
                         [f"T{j}" for j in range(7, 10)])

    def test_fraction_un_ne_laisse_aucun_test(self):
        train, test, _ = chronological_split(
            [position(j, token=f"T{j}") for j in (1, 2, 3)], train_fraction=1.0)
        self.assertEqual((len(train), test), (3, []))

    def test_liste_vide(self):
        self.assertEqual(chronological_split([]), ([], [], 0))

    def test_fraction_par_defaut_est_deux_tiers(self):
        self.assertAlmostEqual(HOLDOUT_TRAIN_FRACTION, 2 / 3)

    def test_une_position_presente_deux_fois_nest_que_dun_cote(self):
        a = position(3, token="DUP", position_id="p-propre")
        b = position(3, token="DUP", position_id="p-emprunt")
        autres = [position(j, token=f"T{j}") for j in (1, 2, 4, 5)]
        train, test, _ = chronological_split([a] + autres + [b])
        tous = train + test
        self.assertEqual(sum(1 for p in tous if p["token_address"] == "DUP"), 1)
        self.assertEqual(len(tous), 5)
        # la première occurrence (la propre) est celle qui reste
        self.assertEqual(
            [p for p in tous if p["token_address"] == "DUP"][0]["position_id"],
            "p-propre")

    def test_sans_timestamp_entry_le_doublon_se_detecte_par_position_id(self):
        a = position(3, token="X", position_id="same")
        b = position(3, token="Y", position_id="same")
        for p in (a, b):
            del p["timestamp_entry"]
        train, test, _ = chronological_split([a, b])
        self.assertEqual(len(train) + len(test), 1)

    def test_positions_sans_cle_exploitable_ne_sont_pas_fusionnees(self):
        sans_cle = []
        for j in (1, 2, 3):
            p = position(j, token=f"T{j}")
            del p["timestamp_entry"]  # ni position_id ni timestamp_entry
            sans_cle.append(p)
        sans_token = position(4)
        del sans_token["token_address"]
        autre = position(4)
        del autre["token_address"]  # même entrée, token absent des deux
        train, test, exclues = chronological_split(sans_cle + [sans_token, autre])
        self.assertEqual((len(train) + len(test), exclues), (5, 0))

    def test_position_sans_date_exclue_et_comptee(self):
        sans = position(2, token="A")
        del sans["timestamp_exit"]
        illisible = position(3, token="B", timestamp_exit="pas une date")
        bonnes = [position(j, token=f"T{j}") for j in (4, 5, 6)]
        train, test, exclues = chronological_split([sans, illisible] + bonnes)
        self.assertEqual(exclues, 2)
        self.assertEqual(len(train) + len(test), 3)

    def test_date_naive_lue_comme_utc_sans_exception(self):
        naive = position(2, token="N", timestamp_exit="2026-11-02T09:00:00")
        train, test, exclues = chronological_split([naive])
        self.assertEqual((len(train), len(test), exclues), (1, 0, 0))


class TestValeurPlanifiee(Base):
    def test_le_pas_maximal_est_dans_la_valeur_planifiee(self):
        self.engine.params.set("exit_rules.take_profit_1", 100, "init", 0)
        planifie = self.engine._planned_value("exit_rules.take_profit_1", 150)
        self.assertAlmostEqual(planifie, 115)
        self.assertEqual(self.engine.params.get("exit_rules.take_profit_1"), 100)

    def test_le_stop_effectif_positif_est_plafonne_dans_la_valeur_planifiee(self):
        self.engine.params.set("exit_rules.stop_loss_slippage_buffer_pct", 15.0, "init", 0)
        planifie = self.engine._planned_value("exit_rules.stop_loss_pct", -10)
        self.assertLessEqual(planifie, self.engine._stop_loss_ceiling(15.0))
        self.assertEqual(self.engine.params.get("exit_rules.stop_loss_pct"), -20)

    def test_bounded_set_ecrit_exactement_la_valeur_planifiee(self):
        path = "exit_rules.take_profit_1"
        planifie = self.engine._planned_value(path, 150)
        self.engine._bounded_set(path, 150, "test", 20)
        self.assertAlmostEqual(self.engine.params.get(path), planifie)

    def test_tp1_reste_sous_tp2_dans_la_valeur_planifiee(self):
        self.engine.params.set("exit_rules.take_profit_1", 295, "init", 0)
        planifie = self.engine._planned_value("exit_rules.take_profit_1", 400)
        self.assertLess(planifie, 300)

    def test_la_valeur_planifiee_nimprime_rien_quand_le_pas_limite(self):
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            planifie = self.engine._planned_value("exit_rules.take_profit_1", 150)
        self.assertAlmostEqual(planifie, 115)
        self.assertEqual(sortie.getvalue(), "")

    def test_bounded_set_imprime_toujours_le_message_de_pas_limite(self):
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            self.engine._bounded_set("exit_rules.take_profit_1", 150, "test", 20)
        self.assertIn("limité à 115.0", sortie.getvalue())

    def test_famine_bloquee_renvoie_la_valeur_courante(self):
        path = "exit_rules.take_profit_1"
        self.engine._starving = lambda p, v: "bloqué"
        self.assertEqual(self.engine._planned_value(path, 110), 100)
        self.assertIsNone(self.engine._bounded_set(path, 110, "test", 20))
        self.assertEqual(self.engine.params.get(path), 100)


class TestMesureSansFutur(Base):
    def _jambe(self, jour, pnl):
        self.journal._append({
            "exit_reason": "BREAKEVEN_STOP",
            "pnl_pct": pnl,
            "timestamp_exit": f"2026-11-{jour:02d}T09:00:00+00:00",
        })

    def test_le_reste_au_breakeven_ignore_les_jambes_posterieures(self):
        for jour in (1, 2, 3):
            self._jambe(jour, -1.0)
        for jour in (20, 21, 22):
            self._jambe(jour, -30.0)
        until = datetime(2026, 11, 10, tzinfo=timezone.utc)
        self.assertEqual(self.engine.measured_breakeven_rest([], until), -1.0)

    def test_sans_borne_le_reste_est_inchange(self):
        for jour, pnl in ((1, -1.0), (2, -1.0), (20, -30.0)):
            self._jambe(jour, pnl)
        self.assertEqual(self.engine.measured_breakeven_rest([]), -1.0)
        self._jambe(21, -30.0)
        self.assertEqual(self.engine.measured_breakeven_rest([]), -15.5)

    def test_jambe_sans_date_exclue_quand_une_borne_est_donnee(self):
        self.journal._append({"exit_reason": "BREAKEVEN_STOP", "pnl_pct": -50.0})
        self._jambe(1, -2.0)
        until = datetime(2026, 11, 10, tzinfo=timezone.utc)
        self.assertEqual(self.engine.measured_breakeven_rest([], until), -2.0)

    def test_simulate_exits_transmet_la_borne(self):
        self._jambe(1, -1.0)
        self._jambe(20, -30.0)
        rows = [position(1, peak=120.0, trough=-3.0, pnl_pct=48.0)]
        out = self.engine.simulate_exits(
            rows, EXITS, until=datetime(2026, 11, 10, tzinfo=timezone.utc))
        self.assertEqual(out["breakeven_rest_used"], -1.0)


class TestPnlParPosition(Base):
    def test_pnl_par_position_somme_au_total_et_saute_les_non_evaluees(self):
        rows = [
            position(1, peak=5.0, trough=-30.0),
            position(2, peak=120.0, trough=-3.0, pnl_pct=48.0),
            position(3, peak=None),  # sautée : pas de pic
            position(4, peak=5.0, trough=-2.0, pnl_pct=-1.0),  # inchangée
        ]
        out = self.engine.simulate_exits(rows, EXITS)
        self.assertEqual(len(out["pnl_par_position"]), out["trades"])
        self.assertEqual(out["trades"], 3)
        self.assertAlmostEqual(sum(out["pnl_par_position"]), out["total_pnl_usd"],
                               places=3)

    def test_ordre_de_la_liste_recue_conserve(self):
        rows = [position(1, peak=5.0, trough=-30.0),
                position(2, peak=120.0, trough=-3.0, pnl_pct=48.0)]
        out = self.engine.simulate_exits(rows, EXITS)
        self.assertLess(out["pnl_par_position"][0], 0)
        self.assertGreater(out["pnl_par_position"][1], 0)


if __name__ == "__main__":
    unittest.main()
