"""Hors-échantillon de la recherche de sorties (audit I10, conception A2).

Briques sans fuite : valeur réellement écrite (a), mesure bornée dans le temps
(c), P&L par position (d), découpage chronologique dédoublonné (e). Puis la
recherche elle-même : choisie sur les positions anciennes, confirmée sur les
récentes, sans que la validation dans l'échantillon puisse la défaire (b).
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.journal import TradeJournal  # noqa: E402
from src.core.learning import (  # noqa: E402
    HOLDOUT_TRAIN_FRACTION,
    STOP_LOSS_PCT_PATH,
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


TP1 = "exit_rules.take_profit_1"

# Trajectoires types. Les minutes tranchent l'ordre pic/creux : sans elles, le
# rejeu devine (pessimiste) et la position compte comme « dégradée ».
FAVORABLE = dict(peak=200.0, trough=-5.0, pnl_pct=40.0,
                 minutes_to_peak=10.0, minutes_to_trough=40.0)
# Creux AVANT le pic : stop touché sous tous les réglages, aucun TP1 atteint.
PUNITIVE = dict(peak=30.0, trough=-60.0, pnl_pct=-25.0,
                minutes_to_peak=40.0, minutes_to_trough=10.0)
# Aucun seuil franchi : la position garderait son P&L réel sous tout réglage.
CALME = dict(peak=4.0, trough=-4.0, pnl_pct=-1.0,
             minutes_to_peak=10.0, minutes_to_trough=40.0)
# Pic et creux franchis, ordre inconnu : le rejeu le devine.
DEVINEE = dict(peak=200.0, trough=-60.0, pnl_pct=-25.0, minutes_to_peak=10.0)


def chronologie(*lots):
    """Positions datées, de la plus ancienne à la plus récente.

    `lots` : (nombre, profil). Chaque position a son jeton, son entrée et sa
    clôture, une heure après la précédente.
    """
    debut = datetime(2026, 11, 1, tzinfo=timezone.utc)
    rows = []
    for nombre, profil in lots:
        for _ in range(nombre):
            rang = len(rows)
            moment = debut + timedelta(hours=rang)
            row = position(1, token=f"T{rang:03d}", **profil)
            row["timestamp_entry"] = (moment - timedelta(minutes=30)).isoformat()
            row["timestamp_exit"] = moment.isoformat()
            row["position_id"] = f"p{rang:03d}"
            rows.append(row)
    return rows


class TestRechercheHorsEchantillon(Base):
    """`_search_exits` choisit sur les 2/3 anciens et confirme sur le tiers récent.

    Le bug verrouillé : la grille de 36 couples choisissait le meilleur sur
    jusqu'à 100 positions et le « validait » sur LES MÊMES — un choix a
    posteriori qui ne pouvait pas échouer.
    """

    def _ecrire(self, rows, journal=None):
        for row in rows:
            (journal or self.journal)._append(row)

    def _moteur(self, nom):
        path = os.path.join(self.tmp, f"{nom}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(PARAMS, fh)
        journal = TradeJournal(os.path.join(self.tmp, f"{nom}.jsonl"))
        return LearningEngine(ParamsStore(path), journal)

    def _chercher(self, engine=None):
        engine = engine or self.engine
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            changes = engine._search_exits()
        return changes, sortie.getvalue()

    def test_couple_bon_sur_le_passe_mauvais_sur_le_recent_nest_pas_applique(self):
        self._ecrire(chronologie((14, FAVORABLE), (7, PUNITIVE)))
        changes, sortie = self._chercher()
        self.assertEqual(changes, [])
        self.assertEqual(self.engine.params.get(TP1), 100)
        self.assertEqual(self.engine.params.get(STOP_LOSS_PCT_PATH), -20)
        verdict = self.engine.last_exit_search
        self.assertEqual(verdict["issue"], "rejeté hors échantillon")
        self.assertEqual((verdict["train_n"], verdict["test_n"], verdict["exclues"]),
                         (14, 7, 0))
        self.assertIn("🧠", sortie)
        self.assertIn("rejeté hors échantillon", sortie)

    def test_couple_bon_sur_les_deux_tranches_est_applique_et_cite_les_deux(self):
        self._ecrire(chronologie((21, FAVORABLE)))
        changes, sortie = self._chercher()
        self.assertTrue(changes)
        self.assertEqual(self.engine.last_exit_search["issue"], "confirmé")
        self.assertEqual(self.engine.params.get(TP1), 115)
        historique = self.engine.params.get("learning.parameter_adjustment_history")
        raison = historique[-1]["reason"]
        self.assertIn("hors échantillon", raison)
        self.assertIn("14 positions anciennes", raison)
        self.assertIn("7 positions récentes", raison)
        self.assertIn("confirmé", sortie)

    def test_on_rejoue_la_valeur_planifiee_pas_le_point_de_grille(self):
        rows = chronologie((21, FAVORABLE))
        self._ecrire(rows)
        appels = []
        reel = self.engine.simulate_exits

        def espion(positions, rules, ambiguity="resolve", until=None):
            appels.append(([p["position_id"] for p in positions], dict(rules), until))
            return reel(positions, rules, ambiguity, until)

        self.engine.simulate_exits = espion
        self._chercher()
        # La grille visait bien 150 sur l'apprentissage...
        self.assertEqual(self.engine.last_exit_search["candidat"]["take_profit_1"], 150)
        recentes = {row["position_id"] for row in rows[14:]}
        sur_le_test = [regles for ids, regles, _ in appels
                       if ids and set(ids) <= recentes]
        self.assertTrue(sur_le_test)
        # ... mais le test rejoue ce qui sera écrit (pas max ±15 depuis 100),
        # à côté des règles actuelles.
        self.assertEqual({r["take_profit_1"] for r in sur_le_test}, {100, 115})

    def test_trop_peu_de_positions_changees_en_test_donne_indetermine(self):
        self._ecrire(chronologie((14, FAVORABLE), (7, CALME)))
        changes, sortie = self._chercher()
        self.assertEqual(changes, [])
        self.assertEqual(self.engine.last_exit_search["issue"], "indéterminé")
        self.assertEqual(self.engine.params.get(TP1), 100)
        self.assertIn("indéterminé", sortie)
        self.assertNotIn("rejeté", sortie)

    def test_ordre_melange_donne_la_meme_decision_que_trie(self):
        rows = chronologie((14, FAVORABLE), (7, PUNITIVE))
        trie = self._moteur("trie")
        melange = self._moteur("melange")
        self._ecrire(rows, trie.journal)
        # Les récentes écrites d'abord : sans tri, elles nourriraient
        # l'apprentissage et le test serait fait des anciennes favorables.
        self._ecrire(rows[14:] + rows[:14], melange.journal)

        def decision(engine):
            changes, _ = self._chercher(engine)
            return (changes, engine.last_exit_search["issue"],
                    engine.params.get(TP1), engine.params.get(STOP_LOSS_PCT_PATH))

        attendu = decision(trie)
        self.assertEqual(attendu[1], "rejeté hors échantillon")
        self.assertEqual(decision(melange), attendu)

    def test_tiers_de_test_degrade_donne_indetermine(self):
        # 3 devinées sur 21 (14 %) : sous le garde-fou global des 25 %, mais 3
        # sur les 7 du test (43 %) : la confirmation reposerait sur un ordre
        # pic/creux deviné.
        self._ecrire(chronologie((18, FAVORABLE), (3, DEVINEE)))
        changes, _ = self._chercher()
        self.assertEqual(changes, [])
        self.assertEqual(self.engine.last_exit_search["issue"], "indéterminé")
        self.assertEqual(self.engine.params.get(TP1), 100)

    def test_gain_de_quelques_centimes_nest_pas_confirme(self):
        # Sur le test, TP1 100 → 115 rapporte +0,01 $ sur une position sur deux
        # (taille 0,1333) et en coûte 0,01 $ sur l'autre (pic 110 : TP1 100
        # touché avant le creux, TP1 115 jamais — le stop sort à -19,4 %).
        plus = dict(FAVORABLE, size=0.1333)
        moins = dict(peak=110.0, trough=-16.0, pnl_pct=-14.0, size=0.0147,
                     minutes_to_peak=10.0, minutes_to_trough=40.0)
        self._ecrire(chronologie((14, FAVORABLE), (1, plus), (1, moins), (1, plus),
                                 (1, moins), (1, plus), (1, moins), (1, plus)))
        changes, _ = self._chercher()
        verdict = self.engine.last_exit_search
        # Un gain moyen existe (quelques dixièmes de centime)...
        self.assertGreater(verdict["ecart_moyen"], 0)
        self.assertLess(verdict["ecart_moyen"], 0.005)
        # ... mais l'intervalle apparié contient zéro : rien n'est écrit.
        self.assertLessEqual(verdict["ecart_bas"], 0)
        self.assertNotEqual(verdict["issue"], "confirmé")
        self.assertEqual(changes, [])
        self.assertEqual(self.engine.params.get(TP1), 100)

    def test_la_validation_dans_lechantillon_ne_defait_pas_un_choix_confirme(self):
        # 6 grosses pertes inchangées sous tout réglage gonflent |P&L/trade| :
        # sur les 21 positions, TP1 115 gagne moins de 10 % et
        # `validate_exit_changes` l'annulerait. Hors échantillon, il gagne sur
        # chaque position favorable du test : confirmé.
        favorable = dict(FAVORABLE, exit_reason="TAKE_PROFIT_1 (+100%)")
        perte = dict(peak=4.0, trough=-14.0, pnl_pct=-14.0, size=430.0,
                     minutes_to_peak=10.0, minutes_to_trough=40.0,
                     exit_reason="TIME_STOP (240 min)")
        self._ecrire(chronologie((10, favorable), (4, perte), (5, favorable), (2, perte)))
        self.engine.params.set("learning.cadence_trades", 20, log=False)
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            applied = self.engine.run()
        self.assertEqual(self.engine.last_exit_search["issue"], "confirmé")
        self.assertEqual(self.engine.params.get(TP1), 115)
        self.assertIn(f"{TP1} -> 115.0", applied)
        self.assertFalse([c for c in applied if c.startswith("(annulé)")])

    def test_sous_la_couverture_rien_comme_avant(self):
        self.assertIsNone(self.engine.last_exit_search)
        self._ecrire(chronologie((10, FAVORABLE)))
        changes, sortie = self._chercher()
        self.assertEqual(changes, [])
        self.assertEqual(self.engine.last_exit_search["issue"], "pas assez de données")
        self.assertEqual(self.engine.params.get(TP1), 100)
        self.assertIn("pas assez de données", sortie)

    def test_positions_sans_date_exclues_sans_exception(self):
        rows = chronologie((21, FAVORABLE))
        for row in rows[:3]:
            del row["timestamp_exit"]
        self._ecrire(rows)
        changes, _ = self._chercher()
        verdict = self.engine.last_exit_search
        self.assertEqual(verdict["exclues"], 3)
        # 18 datées restent, au-dessus de la couverture : 12 choisissent, 6 confirment.
        self.assertEqual((verdict["train_n"], verdict["test_n"]), (12, 6))
        self.assertEqual(verdict["issue"], "confirmé")

    def test_trop_peu_de_positions_datees_donne_pas_assez_de_donnees(self):
        rows = chronologie((21, FAVORABLE))
        for row in rows[:8]:
            del row["timestamp_exit"]
        self._ecrire(rows)
        changes, _ = self._chercher()
        verdict = self.engine.last_exit_search
        self.assertEqual(changes, [])
        self.assertEqual(verdict["issue"], "pas assez de données")
        self.assertEqual(verdict["exclues"], 8)
        self.assertEqual(self.engine.params.get(TP1), 100)

    def test_rejeu_global_devine_donne_degrade(self):
        self._ecrire(chronologie((21, DEVINEE)))
        changes, sortie = self._chercher()
        self.assertEqual(changes, [])
        self.assertEqual(self.engine.last_exit_search["issue"], "dégradé")
        self.assertIn("dégradé", sortie)

    def test_couple_ramene_a_lactuel_par_les_garde_fous_ne_change_rien(self):
        # TP2 à 101 : la grille vise 150, mais TP1 < TP2 ramène la valeur
        # écrite à 100, déjà en place ; le stop choisi (-20) l'est aussi.
        self.engine.params.set("exit_rules.take_profit_2", 101, log=False)
        self._ecrire(chronologie((21, FAVORABLE)))
        changes, sortie = self._chercher()
        self.assertEqual(changes, [])
        self.assertEqual(self.engine.last_exit_search["issue"], "rien à ajuster")
        self.assertEqual(self.engine.params.get(TP1), 100)
        self.assertIn("rien à ajuster", sortie)

    def test_ligne_de_grille_au_stop_effectif_trop_haut_est_ecartee(self):
        # Tampon 5 : un stop à -10 ou -15 donnerait un stop effectif au-dessus
        # de MIN_EFFECTIVE_STOP_LOSS_PCT (-15), que le moteur n'écrira jamais.
        self._ecrire(chronologie((21, FAVORABLE)))
        self._chercher()
        self.assertEqual(self.engine.last_exit_search["candidat"]["stop_loss_pct"], -20)


if __name__ == "__main__":
    unittest.main()
