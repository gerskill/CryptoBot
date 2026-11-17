"""P&L par bras sur 7 j / 30 j / tout — `/api/arms/performance`.

Le besoin : « je ne vois pas le profit par bras, ni par jour ni par semaine ».
Les chiffres existaient dans `scripts/rapport_hebdo.py` mais pas dans le
dashboard, et deux calculs séparés finissent par diverger. Ces tests
verrouillent les pièges déjà tombés ailleurs dans le dépôt :

- une position sortie en TP1 puis breakeven est UNE position, et son gain de
  TP1 compte (le bug `read_final_exits()` du 2026-08) ;
- le drawdown d'une fenêtre part de l'équité réelle au début de la fenêtre,
  pas de la mise de départ ;
- un horodatage illisible ne fait pas tomber le calcul ;
- chaque ligne servie porte son `arm`.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from src.core.stats import position_stats, window_performance  # noqa: E402
from tests.test_api_server import ApiTestCase  # noqa: E402

MAINTENANT = datetime.now(timezone.utc)


def il_y_a(jours: float) -> str:
    return (MAINTENANT - timedelta(days=jours)).isoformat()


def position(pnl, jours, **extra):
    return {"pnl_usd": pnl, "pnl_pct": pnl, "timestamp_exit": il_y_a(jours), **extra}


class TestPositionStats(unittest.TestCase):
    def test_profit_factor_sans_perte_est_none_et_pas_infini(self):
        """L'infini ne passe pas en JSON ; 0 dirait l'inverse de la réalité."""
        stats = position_stats([position(5.0, 1), position(3.0, 1)])
        self.assertIsNone(stats["profit_factor"])
        self.assertEqual(stats["gross_win"], 8.0)

    def test_aucun_trade_ne_divise_pas_par_zero(self):
        stats = position_stats([])
        self.assertEqual(stats["trades"], 0)
        self.assertEqual(stats["win_rate"], 0.0)
        self.assertIsNone(stats["profit_factor"])

    def test_pnl_absent_compte_comme_zero_sans_planter(self):
        """Une donnée absente ne fait jamais tomber le calcul."""
        stats = position_stats([{"pnl_usd": None}, position(2.0, 1)])
        self.assertEqual(stats["trades"], 2)
        self.assertEqual(stats["pnl_usd"], 2.0)

    def test_fermeture_manuelle_reconnue_par_exclusion(self):
        stats = position_stats([
            position(-1, 1, exit_reason="RUG_PULL (liquidité -80%)"),
            position(-1, 1, exit_reason="STOP_LOSS (-20%)"),
            position(-1, 1, exit_reason="PANIC_STOP"),
        ])
        self.assertEqual(stats["rug_pulls"], 1)
        self.assertEqual(stats["manual_closes"], 1)


class TestWindowPerformance(unittest.TestCase):
    def test_la_fenetre_exclut_les_positions_anterieures(self):
        rows = [position(-50.0, 20), position(10.0, 2), position(-4.0, 1)]
        perf = window_performance(rows, 1000.0, MAINTENANT - timedelta(days=7))
        self.assertEqual(perf["trades"], 2)
        self.assertAlmostEqual(perf["pnl_usd"], 6.0)

    def test_le_drawdown_part_de_lequite_reelle_au_debut_de_la_fenetre(self):
        """Un bras déjà à 500 $ qui perd 50 $ recule de 10 %, pas de 5 %
        comme s'il repartait de la mise de 1000 $."""
        rows = [position(-500.0, 20), position(-50.0, 1)]
        perf = window_performance(rows, 1000.0, MAINTENANT - timedelta(days=7))
        self.assertEqual(perf["equity_start"], 500.0)
        self.assertAlmostEqual(perf["max_drawdown_pct"], 10.0)

    def test_tout_sans_borne_reprend_toute_lhistoire(self):
        rows = [position(-500.0, 200), position(100.0, 1)]
        perf = window_performance(rows, 1000.0, None)
        self.assertEqual(perf["trades"], 2)
        self.assertEqual(perf["equity_start"], 1000.0)
        self.assertAlmostEqual(perf["max_drawdown_pct"], 50.0)

    def test_pnl_par_jour_regroupe_sur_la_date_utc(self):
        rows = [
            {"pnl_usd": 2.0, "timestamp_exit": "2026-11-10T23:30:00+00:00"},
            {"pnl_usd": 3.0, "timestamp_exit": "2026-11-10T01:00:00"},  # naïf = UTC
            {"pnl_usd": -1.0, "timestamp_exit": "2026-11-11T00:10:00+00:00"},
        ]
        perf = window_performance(rows, 1000.0, None)
        self.assertEqual(
            perf["daily"],
            [
                {"date": "2026-11-10", "trades": 2, "pnl_usd": 5.0},
                {"date": "2026-11-11", "trades": 1, "pnl_usd": -1.0},
            ],
        )

    def test_horodatage_illisible_exclu_dune_fenetre_bornee_sans_planter(self):
        rows = [{"pnl_usd": 7.0, "timestamp_exit": "pas une date"}, position(1.0, 1)]
        perf = window_performance(rows, 1000.0, MAINTENANT - timedelta(days=7))
        self.assertEqual(perf["trades"], 1)

    def test_intervalle_absent_quand_la_fenetre_est_vide(self):
        perf = window_performance([position(1.0, 40)], 1000.0, MAINTENANT - timedelta(days=7))
        self.assertEqual(perf["trades"], 0)
        self.assertIsNone(perf["win_rate_interval"])


class TestEndpointPerformance(ApiTestCase):
    def _jambe(self, pid, pnl, jours, final, fraction=0.5):
        return {
            "position_id": pid, "token": pid, "pnl_usd": pnl, "pnl_pct": pnl,
            "timestamp_exit": il_y_a(jours), "is_final_exit": final,
            "fraction_sold": fraction,
        }

    def _deux_bras(self):
        self._manifest([
            {"name": "baseline", "capital_pct": 0.5},
            {"name": "sniper", "capital_pct": 0.5},
            {"name": "eteint", "capital_pct": 0.0, "enabled": False},
        ])
        self._ecrire_jsonl(settings.TRADES_LOG_PATH, [
            self._jambe("b1", -3.0, 1, True, 1.0),
        ])
        # Une position sniper : TP1 +20 $ puis breakeven -1 $ = UN trade gagnant.
        self._ecrire_jsonl(settings.arm_paths("sniper")["trades"], [
            self._jambe("s1", 20.0, 2, False),
            self._jambe("s1", -1.0, 2, True),
            self._jambe("s0", -40.0, 60, True, 1.0),
        ])
        self._ecrire_jsonl(settings.arm_paths("eteint")["trades"], [
            self._jambe("e1", 999.0, 1, True, 1.0),
        ])

    def test_chaque_ligne_porte_son_bras(self):
        self._deux_bras()
        reponse = self.server.get_arms_performance(window="7d")
        self.assertEqual([r["arm"] for r in reponse["arms"]], ["baseline", "sniper"])
        self.assertEqual(reponse["fleet"]["arm"], "flotte")

    def test_un_tp1_suivi_dun_breakeven_est_un_seul_trade_gagnant(self):
        """Le bug `read_final_exits()` : le gain du TP1 disparaissait."""
        self._deux_bras()
        sniper = self.server.get_arms_performance(window="7d")["arms"][1]
        self.assertEqual(sniper["trades"], 1)
        self.assertEqual(sniper["wins"], 1)
        self.assertAlmostEqual(sniper["pnl_usd"], 19.0)

    def test_la_fenetre_30j_et_tout_ne_comptent_pas_la_meme_chose(self):
        self._deux_bras()
        sniper_30 = self.server.get_arms_performance(window="30d")["arms"][1]
        sniper_all = self.server.get_arms_performance(window="all")["arms"][1]
        self.assertEqual(sniper_30["trades"], 1)
        self.assertEqual(sniper_all["trades"], 2)

    def test_un_bras_desactive_est_exclu_du_tableau_et_de_la_flotte(self):
        self._deux_bras()
        reponse = self.server.get_arms_performance(window="7d")
        self.assertNotIn("eteint", [r["arm"] for r in reponse["arms"]])
        self.assertAlmostEqual(reponse["fleet"]["pnl_usd"], 16.0)

    def test_fenetre_inconnue_refusee_plutot_que_choisie_apres_coup(self):
        from fastapi import HTTPException

        self._deux_bras()
        with self.assertRaises(HTTPException) as ctx:
            self.server.get_arms_performance(window="3d")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_le_temoin_na_pas_de_verdict_contre_lui_meme(self):
        self._deux_bras()
        arms = self.server.get_arms_performance(window="7d")["arms"]
        self.assertIsNone(arms[0]["verdict"])
        self.assertEqual(arms[1]["verdict"]["verdict"], "échantillon trop court")

    def test_capital_par_bras_lu_dans_params_sinon_mise_fictive(self):
        self._deux_bras()
        self.assertEqual(
            self.server.get_arms_performance(window="all")["starting_capital_per_arm"], 1000.0
        )
        with open(settings.PARAMS_PATH, "w", encoding="utf-8") as fh:
            json.dump({"capital_total": 7000}, fh)
        self.assertEqual(
            self.server.get_arms_performance(window="all")["starting_capital_per_arm"], 7000.0
        )


if __name__ == "__main__":
    unittest.main()
