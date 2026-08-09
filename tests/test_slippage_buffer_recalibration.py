"""Recalibrage du tampon de glissement — la version qui tourne réellement.

CONTEXTE. Une critique proposait un `SlippageMonitorAgent` avec du code qui ne
tournait pas : `journal.get_trade_count()` (inexistant), `measured_slippage`
appelée avec un nom de bras au lieu de lignes, `self.arms_config[...]`
(structure jamais définie), `await` dans un `main.py` à 100 % synchrone. Ce
fichier verrouille la version qui utilise les mécanismes réels du dépôt :
`_bounded_set`, `PARAM_BOUNDS`, `measured_slippage(rows)`.

LE POINT DE SÉMANTIQUE À NE PAS RATER. `stop_loss_trigger_pct`, écrit au
journal, est déjà `effective_stop_loss_pct` = seuil configuré + tampon ALORS
actif. `measured_slippage` mesure donc le RÉSIDU après le tampon en place, pas
le dépassement brut. Un test dessus doit construire des lignes avec un
`stop_loss_trigger_pct` qui reflète un tampon déjà appliqué, sinon il ne teste
pas la bonne quantité.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.journal import TradeJournal  # noqa: E402
from src.core.learning import (  # noqa: E402
    MIN_EFFECTIVE_STOP_LOSS_PCT,
    MIN_SEGMENT_SAMPLE,
    PARAM_BOUNDS,
    LearningEngine,
)
from src.core.params import ParamsStore  # noqa: E402

PARAMS = {
    "version": "test",
    "filters": {},
    "exit_rules": {"stop_loss_pct": -25, "stop_loss_slippage_buffer_pct": 0.0},
    "learning": {},
}


def _sl_row(pid: str, trigger: float, realized: float) -> dict:
    return {
        "position_id": pid, "token": pid,
        "exit_reason": f"STOP_LOSS ({realized:.1f}%)",
        "stop_loss_trigger_pct": trigger,
        "pnl_pct": realized, "pnl_usd": realized / 10,
        "is_final_exit": True,
    }


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        chemin = os.path.join(self.tmp, "params.json")
        with open(chemin, "w", encoding="utf-8") as fh:
            import json
            json.dump(PARAMS, fh)
        self.params = ParamsStore(chemin)
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))
        self.engine = LearningEngine(self.params, self.journal)

    def _ecrire(self, rows):
        for row in rows:
            with open(self.journal.path, "a", encoding="utf-8") as fh:
                import json
                fh.write(json.dumps(row) + "\n")


class TestBorne(unittest.TestCase):
    def test_le_tampon_est_borne(self):
        self.assertIn("exit_rules.stop_loss_slippage_buffer_pct", PARAM_BOUNDS)
        low, high = PARAM_BOUNDS["exit_rules.stop_loss_slippage_buffer_pct"]
        self.assertEqual(low, 0.0)
        self.assertLessEqual(high, 15.0)


class TestRecalibration(Base):
    def test_sous_le_seuil_dechantillon_rien_ne_bouge(self):
        rows = [_sl_row(f"p{i}", -25.0, -34.75) for i in range(MIN_SEGMENT_SAMPLE - 1)]
        self.assertIsNone(self.engine._recalibrate_slippage_buffer(rows))

    def test_un_residu_positif_augmente_le_tampon(self):
        """Trigger -25 (tampon 0 actif), realise -34.75 : residu 9.75."""
        rows = [_sl_row(f"p{i}", -25.0, -34.75) for i in range(MIN_SEGMENT_SAMPLE)]
        change = self.engine._recalibrate_slippage_buffer(rows)
        self.assertIsNotNone(change)
        self.assertEqual(
            self.params.get("exit_rules.stop_loss_slippage_buffer_pct"), 9.8
        )

    def test_le_residu_sadditionne_au_tampon_deja_actif(self):
        """Le trigger porte DEJA le tampon en place (5.0) : le residu mesure
        ce qui reste a couvrir AU-DELA, et s'ajoute par-dessus."""
        self.params.set("exit_rules.stop_loss_slippage_buffer_pct", 5.0, "setup", 0)
        # trigger = -25 (base) + 5 (tampon actif) = -20 ; realise -23 -> residu 3
        rows = [_sl_row(f"p{i}", -20.0, -23.0) for i in range(MIN_SEGMENT_SAMPLE)]
        self.engine._recalibrate_slippage_buffer(rows)
        self.assertEqual(
            self.params.get("exit_rules.stop_loss_slippage_buffer_pct"), 8.0
        )

    def test_un_tampon_deja_suffisant_ne_bouge_pas(self):
        """Realise AU-DESSUS du trigger effectif : le tampon actuel suffit,
        on ne le reduit pas sur ce seul signal."""
        rows = [_sl_row(f"p{i}", -25.0, -24.0) for i in range(MIN_SEGMENT_SAMPLE)]
        self.assertIsNone(self.engine._recalibrate_slippage_buffer(rows))
        self.assertEqual(
            self.params.get("exit_rules.stop_loss_slippage_buffer_pct"), 0.0
        )

    def test_ne_reduit_jamais_meme_face_a_un_residu_negatif(self):
        self.params.set("exit_rules.stop_loss_slippage_buffer_pct", 10.0, "setup", 0)
        rows = [_sl_row(f"p{i}", -15.0, -10.0) for i in range(MIN_SEGMENT_SAMPLE)]
        self.assertIsNone(self.engine._recalibrate_slippage_buffer(rows))
        self.assertEqual(
            self.params.get("exit_rules.stop_loss_slippage_buffer_pct"), 10.0
        )

    def test_le_resultat_reste_borne_a_15(self):
        """stop_loss_pct large (-40) pour isoler la PROPRE borne du tampon
        (15.0) de celle du garde-fou croise (`MIN_EFFECTIVE_STOP_LOSS_PCT`) :
        a -25 les deux se chevauchent depuis que le plafond est monte a
        -15.0 (voir TestGardeFouCroise), ce test ne verifierait plus la
        meme chose."""
        self.params.set("exit_rules.stop_loss_pct", -40.0, "setup", 0)
        rows = [_sl_row(f"p{i}", -40.0, -75.0) for i in range(MIN_SEGMENT_SAMPLE)]
        self.engine._recalibrate_slippage_buffer(rows)
        self.assertEqual(
            self.params.get("exit_rules.stop_loss_slippage_buffer_pct"), 15.0
        )

    def test_les_sorties_hors_stop_loss_sont_ignorees(self):
        rows = [
            {"exit_reason": "TAKE_PROFIT_1", "pnl_pct": 50.0,
             "stop_loss_trigger_pct": -25.0, "position_id": f"tp{i}"}
            for i in range(20)
        ]
        self.assertIsNone(self.engine._recalibrate_slippage_buffer(rows))

    def test_est_appele_depuis_adjust_exits(self):
        """La recalibration doit vraiment se déclencher au fil de l'apprentissage
        normal, pas seulement quand on l'appelle en isolation."""
        self._ecrire([_sl_row(f"p{i}", -25.0, -34.75) for i in range(MIN_SEGMENT_SAMPLE)])
        self.engine._adjust_exits({"avg_loss_pct": 0})
        self.assertGreater(
            self.params.get("exit_rules.stop_loss_slippage_buffer_pct"), 0.0
        )

    def test_ecrit_dans_lhistorique_existant_pas_un_nouveau_fichier(self):
        """Pas de `data/slippage_buffer_log.jsonl` séparé : l'audit existant
        (`parameter_adjustment_history`) couvre déjà tout changement de
        paramètre. En dupliquer un ferait deux sources de vérité."""
        rows = [_sl_row(f"p{i}", -25.0, -34.75) for i in range(MIN_SEGMENT_SAMPLE)]
        self.engine._recalibrate_slippage_buffer(rows)
        historique = self.params.get("learning.parameter_adjustment_history", [])
        self.assertTrue(
            any("stop_loss_slippage_buffer_pct" in h.get("param_name", "")
                for h in historique)
        )


class TestGardeFouCroise(unittest.TestCase):
    """Régression du 2026-08-06 sur `quality` : `stop_loss_pct` (resserré par
    `_adjust_exits`) et le tampon (augmenté par `_recalibrate_slippage_buffer`)
    sont deux mécanismes indépendants, chacun borné seul. Sans garde croisé,
    stop_loss_pct au plancher (-10) + tampon au plafond (15.0) = seuil effectif
    +5 % — un stop loss positif qui sort quasi toute position en quelques
    secondes. 139 trades perdus en une journée sur ce bras avant le fix.
    """

    def _engine(self, stop_loss_pct: float, buffer: float) -> tuple[LearningEngine, ParamsStore, TradeJournal]:
        tmp = tempfile.mkdtemp()
        chemin = os.path.join(tmp, "params.json")
        with open(chemin, "w", encoding="utf-8") as fh:
            import json
            json.dump(
                {
                    "version": "test",
                    "filters": {},
                    "exit_rules": {
                        "stop_loss_pct": stop_loss_pct,
                        "stop_loss_slippage_buffer_pct": buffer,
                    },
                    "learning": {},
                },
                fh,
            )
        params = ParamsStore(chemin)
        journal = TradeJournal(os.path.join(tmp, "trades.jsonl"))
        return LearningEngine(params, journal), params, journal

    def test_le_tampon_ne_peut_pas_faire_franchir_zero(self):
        """stop_loss_pct au plancher (-10, borne dure) : le tampon ne doit
        jamais pouvoir grandir au point que la somme atteigne ou dépasse
        `MIN_EFFECTIVE_STOP_LOSS_PCT`, même si sa PROPRE borne (15.0) le
        permettrait et que le résidu mesuré le justifierait.

        Depuis que le plafond est monté à -15.0, -10 (borne dure de
        `stop_loss_pct` seul) est DÉJÀ plus serré que le plafond visé — le
        tampon ne peut pas widen ce que `stop_loss_pct` seul a déjà cassé
        (il ne fait qu'ajouter, jamais retrancher). Le garde-fou refuse
        alors tout ajustement (`buffer` reste à 0) plutôt que de produire un
        tampon négatif absurde. L'invariant réellement garanti — jamais nul
        ni positif — tient toujours ; atteindre precisement -15.0 ne l'est
        plus dans ce cas de figure extrême."""
        engine, params, _ = self._engine(stop_loss_pct=-10, buffer=0.0)
        rows = [_sl_row(f"p{i}", -10.0, -40.0) for i in range(MIN_SEGMENT_SAMPLE)]
        engine._recalibrate_slippage_buffer(rows)
        buffer_final = params.get("exit_rules.stop_loss_slippage_buffer_pct")
        self.assertLessEqual(buffer_final, 15.0)  # borne propre toujours respectée
        self.assertGreaterEqual(buffer_final, 0.0)  # jamais negatif
        self.assertLess(
            -10 + buffer_final,
            0.0,
            "le seuil effectif ne doit jamais devenir nul ou positif",
        )

    def test_le_resserrage_du_stop_ne_peut_pas_faire_franchir_zero(self):
        """Tampon déjà à 15.0 (plafond) : resserrer stop_loss_pct vers -10
        (borne dure) le ferait franchir zéro. Le resserrage doit être plafonné
        par le tampon en place, pas seulement par sa propre borne (-50, -10)."""
        engine, params, _ = self._engine(stop_loss_pct=-20, buffer=15.0)
        changes = engine._adjust_exits({"avg_loss_pct": -35, "losing_trades": 20})
        stop_loss_final = params.get("exit_rules.stop_loss_pct")
        self.assertLessEqual(
            stop_loss_final + 15.0,
            MIN_EFFECTIVE_STOP_LOSS_PCT,
            "le seuil effectif ne doit jamais devenir nul ou positif",
        )
        # Le tampon, lui, n'a pas bougé : le garde-fou plafonne le paramètre
        # qui bouge à cet instant, jamais celui déjà posé (jamais réduit).
        self.assertEqual(params.get("exit_rules.stop_loss_slippage_buffer_pct"), 15.0)


class TestValidationParBacktest(Base):
    """LE « MIX » DEMANDÉ : mesure empirique pour PROPOSER, rejeu pour VALIDER.

    `_recalibrate_slippage_buffer` ne décide jamais seule — elle tourne
    depuis `_adjust_exits`, dont chaque changement passe déjà par
    `validate_exit_changes` dans `run()`. Ce fichier construit un cas où la
    mesure empirique (résidu réel sur des sorties stop loss) propose un
    tampon que le rejeu (sur des trajectoires DIFFÉRENTES, où ce tampon
    aurait converti une remontée réelle en perte simulée) désapprouve — et
    vérifie que le tout est bien annulé, pas juste l'un des deux.
    """

    def test_un_tampon_mesure_mais_nefaste_est_annule_par_le_backtest(self):
        # 10 sorties STOP_LOSS réelles : trigger -25 (tampon 0 actif),
        # réalisé -33 -> résidu 8 -> candidat 0 + 8 = 8.0. trough assez
        # profond pour rester stoppé quel que soit le tampon : ces lignes ne
        # doivent RIEN changer au rejeu, seulement proposer le candidat.
        sl_rows = [
            {**_sl_row(f"sl{i}", -25.0, -33.0),
             "peak_pct": 5.0, "trough_pct": -33.0, "position_size": 20.0}
            for i in range(MIN_SEGMENT_SAMPLE)
        ]
        # 8 positions qui ont RÉELLEMENT remonté (TIME_STOP à +8%), avec un
        # creux à -18 : hors de portée du seuil actuel (-25), mais À portée
        # du seuil élargi par le candidat (-25+8 = -17). Le rejeu doit noter
        # cette conversion remontée -> perte, sans que rien ne fuite depuis
        # la raison de sortie historique.
        recovery_rows = [
            {"position_id": f"rec{i}", "token": f"rec{i}",
             "exit_reason": "TIME_STOP", "pnl_pct": 8.0, "pnl_usd": 1.6,
             "peak_pct": 5.0, "trough_pct": -18.0, "position_size": 20.0,
             "is_final_exit": True}
            for i in range(8)
        ]
        self._ecrire(sl_rows + recovery_rows)

        previous_exits = dict(self.params.get("exit_rules", {}))
        change = self.engine._recalibrate_slippage_buffer(
            self.journal.read_positions()
        )
        self.assertIsNotNone(change, "le candidat doit être proposé")
        self.assertEqual(
            self.params.get("exit_rules.stop_loss_slippage_buffer_pct"), 8.0
        )

        verdict = self.engine.validate_exit_changes(previous_exits)

        self.assertIn("annulées", verdict or "")
        self.assertEqual(
            self.params.get("exit_rules.stop_loss_slippage_buffer_pct"), 0.0,
            "le backtest doit revenir au tampon d'avant, pas garder le "
            "candidat mesuré seul",
        )

    def test_un_tampon_mesure_et_confirme_par_le_backtest_est_garde(self):
        # Même mesure empirique, mais aucune position ne bascule d'une
        # remontée réelle vers une perte simulée : le candidat doit survivre.
        sl_rows = [
            {**_sl_row(f"sl{i}", -25.0, -33.0),
             "peak_pct": 5.0, "trough_pct": -33.0, "position_size": 20.0}
            for i in range(MIN_SEGMENT_SAMPLE)
        ]
        neutral_rows = [
            {"position_id": f"n{i}", "token": f"n{i}",
             "exit_reason": "TIME_STOP", "pnl_pct": 2.0, "pnl_usd": 0.4,
             "peak_pct": 5.0, "trough_pct": -2.0, "position_size": 20.0,
             "is_final_exit": True}
            for i in range(8)
        ]
        self._ecrire(sl_rows + neutral_rows)

        previous_exits = dict(self.params.get("exit_rules", {}))
        self.engine._recalibrate_slippage_buffer(self.journal.read_positions())
        verdict = self.engine.validate_exit_changes(previous_exits)

        self.assertNotIn("annulées", verdict or "")
        self.assertEqual(
            self.params.get("exit_rules.stop_loss_slippage_buffer_pct"), 8.0
        )


if __name__ == "__main__":
    unittest.main()
