"""Mise en commun des trajectoires, et verdict contre le témoin.

DEUX PROBLÈMES DISTINCTS, tous deux nés du multi-bras.

1. LE REJEU DES SORTIES ÉTAIT PAR BRAS. `simulate_exits` ne lit que `peak_pct`
   et `trough_pct` : une trajectoire est une trajectoire, elle ne dépend pas du
   bras dont les filtres l'ont admise. Pourtant `_instrumented()` ne lisait que
   `self.journal`. Mesuré au 2026-08-02 : 6 positions instrumentées pour
   `scalp`, `runner` et `consensus`, 2 pour `quality`, contre
   `EXIT_BACKTEST_MIN_COVERAGE` = 15. Aucun verdict possible. Mises en
   commun : 75.

   Mais les trajectoires ne sont PAS interchangeables — un token de `sniper`
   (âge 0-1 h, liquidité 4 K) n'a pas la forme d'un token de `quality` (âge
   4-48 h, liquidité 20 K). Sans filtre de fenêtre on remplacerait un manque
   de données par un biais, ce qui est pire : le manque, lui, se voit.

2. SEPT BRAS, AUCUNE CORRECTION DE COMPARAISON MULTIPLE. Regarder sept
   stratégies et garder la meilleure produit un gagnant par hasard. `runner`
   affichait +7,44 $/trade — sur six trades, IC95 [-5,61 .. +25,00].
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
from src.core.stats import verdict_vs_reference  # noqa: E402

PARAMS = {
    "version": "test",
    "filters": {"min_age_hours": 1.0, "max_age_hours": 6.0,
                "min_liquidity_usd": 20000},
    "exit_rules": {"stop_loss_pct": -25, "take_profit_1": 100},
    "learning": {},
}


def _position(pid, age, liq, pnl=1.0, peak=50.0, trough=-10.0):
    return {
        "position_id": pid, "token": pid, "pnl_usd": pnl, "pnl_pct": pnl,
        "age_hours_at_entry": age, "liquidity_at_entry": liq,
        "peak_pct": peak, "trough_pct": trough, "is_final_exit": True,
    }


class MiseEnCommunTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        chemin = os.path.join(self.tmp, "params.json")
        with open(chemin, "w", encoding="utf-8") as fh:
            json.dump(PARAMS, fh)
        self.params = ParamsStore(chemin)
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))

    def _engine(self, pool):
        return LearningEngine(self.params, self.journal, pool=lambda: pool)

    def _ecrire(self, row):
        """Écrit une ligne brute : `record_exit` reconstruit un P&L depuis une
        position, on veut ici contrôler les champs du journal directement."""
        with open(self.journal.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


class TestFenetre(MiseEnCommunTestCase):
    def test_une_trajectoire_dans_la_fenetre_est_empruntee(self):
        engine = self._engine([_position("p1", age=3.0, liq=30000)])
        self.assertEqual(len(engine._instrumented()), 1)

    def test_trop_vieille_est_ecartee(self):
        engine = self._engine([_position("p1", age=20.0, liq=30000)])
        self.assertEqual(engine._instrumented(), [])

    def test_trop_jeune_est_ecartee(self):
        engine = self._engine([_position("p1", age=0.2, liq=30000)])
        self.assertEqual(engine._instrumented(), [])

    def test_carnet_trop_mince_est_ecarte(self):
        engine = self._engine([_position("p1", age=3.0, liq=5000)])
        self.assertEqual(engine._instrumented(), [])

    def test_une_donnee_absente_ne_rejette_pas(self):
        """Même invariant que le pipeline, jusque dans le rejeu."""
        sans_age = _position("p1", age=None, liq=30000)
        self.assertEqual(len(self._engine([sans_age])._instrumented()), 1)

    def test_sans_pic_ni_creux_rien_a_rejouer(self):
        muette = _position("p1", age=3.0, liq=30000)
        muette["peak_pct"] = None
        self.assertEqual(self._engine([muette])._instrumented(), [])


class TestPrioriteAuxSiennes(MiseEnCommunTestCase):
    def test_ses_propres_positions_passent_meme_hors_fenetre(self):
        """Elles ont DÉJÀ franchi ses filtres. Les rejuger sur des seuils qui
        ont bougé depuis les écarterait à tort."""
        self._ecrire(_position("mien", age=99.0, liq=1.0))
        rows = self._engine([])._instrumented()
        self.assertEqual([r["position_id"] for r in rows], ["mien"])

    def test_pas_de_doublon_entre_les_siennes_et_le_pool(self):
        self._ecrire(_position("commun", age=3.0, liq=30000))
        rows = self._engine([_position("commun", age=3.0, liq=30000)])._instrumented()
        self.assertEqual(len(rows), 1)

    def test_sans_pool_le_comportement_est_inchange(self):
        self._ecrire(_position("mien", age=3.0, liq=30000))
        engine = LearningEngine(self.params, self.journal)
        self.assertEqual(len(engine._instrumented()), 1)


class TestVerdictContreLeTemoin(unittest.TestCase):
    def test_des_intervalles_qui_se_recouvrent_ne_concluent_pas(self):
        """Le cas réel : runner +7,44 $/trade sur six trades."""
        bras = [_position(f"a{i}", 3.0, 30000, pnl=p)
                for i, p in enumerate([40, -5, -5, -5, -5, 25])]
        temoin = [_position(f"b{i}", 3.0, 30000, pnl=p)
                  for i, p in enumerate([-4] * 30)]
        self.assertEqual(verdict_vs_reference(bras, temoin)["verdict"],
                         "indistinguable")

    def test_un_ecart_franc_et_repete_conclut(self):
        bras = [_position(f"a{i}", 3.0, 30000, pnl=10.0) for i in range(40)]
        temoin = [_position(f"b{i}", 3.0, 30000, pnl=-10.0) for i in range(40)]
        self.assertEqual(verdict_vs_reference(bras, temoin)["verdict"], "supérieur")
        self.assertEqual(verdict_vs_reference(temoin, bras)["verdict"], "inférieur")

    def test_echantillon_trop_court_nest_pas_une_egalite(self):
        """« indistinguable » et « pas assez de trades » sont deux états
        différents. Les confondre ferait croire qu'on a mesuré une égalité."""
        verdict = verdict_vs_reference([], [_position("b", 3.0, 30000)])
        self.assertEqual(verdict["verdict"], "échantillon trop court")
        self.assertIsNone(verdict["interval"])

    def test_les_deux_intervalles_sont_toujours_rendus(self):
        bras = [_position(f"a{i}", 3.0, 30000, pnl=1.0) for i in range(20)]
        temoin = [_position(f"b{i}", 3.0, 30000, pnl=-1.0) for i in range(20)]
        verdict = verdict_vs_reference(bras, temoin)
        for cle in ("interval", "reference"):
            self.assertIn("low", verdict[cle])
            self.assertIn("high", verdict[cle])
            self.assertIn("n", verdict[cle])


if __name__ == "__main__":
    unittest.main()
