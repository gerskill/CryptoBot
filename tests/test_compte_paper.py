"""Nouveau compte paper par bras sans effacer l'historique (src/core/compte.py), 2026-11-21.

Demande de Killian : repartir de 100 $ par bras, garder les anciens trades, l'apprentissage garde
l'historique. Verrouillé : solde et statistiques ne comptent que les positions ouvertes depuis le
début du compte ; une position d'avant ne touche pas au nouveau solde ; la taille suit le capital.
"""

import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import nouveau_compte
from src import settings
from src.core import compte as cp
from src.core.journal import TradeJournal
from src.core.models import Candidate
from src.core.portfolio import PaperPortfolio

PARAMS = {
    "version": "2.0", "risk_per_trade": 0.03,
    "exit_rules": {"stop_loss_pct": -25, "take_profit_1": 100, "take_profit_2": 300,
                   "take_profit_3": 500, "partial_sell_tp1_pct": 0.5, "partial_sell_tp2_pct": 0.5,
                   "trailing_stop_activation": 200, "trailing_stop_distance_pct": 50,
                   "max_hold_time_minutes": 240},
    "filters": {}, "scoring_weights": {}, "learning": {},
}


def ligne(pid, pnl_usd, heures_avant):
    entree = datetime.now(timezone.utc) - timedelta(hours=heures_avant)
    return {"position_id": pid, "pnl_usd": pnl_usd, "pnl_pct": pnl_usd, "is_final_exit": True,
            "fraction_of_initial": 1.0, "timestamp_entry": entree.isoformat(),
            "timestamp_exit": (entree + timedelta(minutes=30)).isoformat()}


def candidat(adresse="m1"):
    return Candidate(token_address=adresse, symbol="TOK", name="TOK", chain="solana",
                     price_usd=1.0, liquidity_usd=50000, volume_1h=20000)


class TestFichier(unittest.TestCase):
    def setUp(self):
        self.chemin = os.path.join(tempfile.mkdtemp(), "compte.json")

    def test_absent_ou_illisible_donne_none(self):
        self.assertIsNone(cp.compte_actuel(self.chemin))
        with open(self.chemin, "w") as fh:
            fh.write("{pas du json")
        self.assertIsNone(cp.compte_actuel(self.chemin))

    def test_ouvrir_puis_relire_et_numeroter(self):
        premier = cp.ouvrir_compte(100, maintenant=1000.0, chemin=self.chemin)
        self.assertEqual((premier.capital, premier.numero), (100.0, 2))
        relu = cp.compte_actuel(self.chemin)
        self.assertEqual((relu.depuis, relu.capital), (1000.0, 100.0))
        self.assertEqual(cp.ouvrir_compte(50, maintenant=2000.0, chemin=self.chemin).numero, 3)

    def test_solde_nul_refuse(self):
        with self.assertRaises(ValueError):
            cp.ouvrir_compte(0, chemin=self.chemin)

    def test_appartenance_decidee_par_l_entree(self):
        compte = cp.Compte(depuis=time.time() - 3600, capital=100)
        self.assertTrue(compte.contient(ligne("a", 1, 0.5)))
        self.assertFalse(compte.contient(ligne("b", 1, 2)))
        self.assertFalse(compte.contient({"pnl_usd": 1}))  # sans date : pas dans le compte


class TestPortefeuille(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.journal = TradeJournal(os.path.join(tmp, "journal.jsonl"))
        with open(self.journal.path, "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(row) + "\n" for row in (ligne("vieux1", -4000.0, 48), ligne("vieux2", -300.0, 30), ligne("neuf", 5.0, 0.5)))
        self.positions_path = os.path.join(tmp, "positions.json")
        self.depuis = time.time() - 3600

    def _portefeuille(self, depuis="defaut"):
        return PaperPortfolio(capital=100.0, journal=self.journal, positions_path=self.positions_path,
                              depuis=self.depuis if depuis == "defaut" else depuis)

    def test_le_solde_repart_de_100_avec_seulement_les_trades_du_compte(self):
        with redirect_stdout(io.StringIO()):
            p = self._portefeuille()
        self.assertAlmostEqual(p.capital, 105.0)
        stats = p.stats()
        self.assertEqual(stats["total_trades"], 1)
        self.assertEqual(stats["win_rate"], 100.0)

    def test_sans_compte_tout_le_journal_compte_comme_avant(self):
        with redirect_stdout(io.StringIO()):
            p = self._portefeuille(depuis=None)
        self.assertAlmostEqual(p.capital, 100.0 - 4295.0)
        self.assertEqual(p.stats()["total_trades"], 3)

    def test_la_taille_suit_le_nouveau_capital(self):
        with redirect_stdout(io.StringIO()):
            p = self._portefeuille()
        self.assertLessEqual(p.position_size(0.03, 0.0, 0), 0.05 * p.capital + 1e-9)
        self.assertAlmostEqual(p.position_size(0.03, 0.0, 0), round(0.03 * p.capital, 4))

    def test_une_position_d_avant_ne_touche_pas_au_nouveau_solde(self):
        with redirect_stdout(io.StringIO()):
            ancien = PaperPortfolio(capital=7000.0, journal=TradeJournal(self.journal.path + ".x"),
                                    positions_path=self.positions_path)
            position = ancien.open(candidat("vieux"), PARAMS, 50.0)
            ancien.positions[position.id] = replace(position, entry_time=self.depuis - 600)
            ancien.save_positions()
            p = self._portefeuille()
        capital = p.capital
        self.assertAlmostEqual(capital, 105.0)  # la position ouverte d'avant n'est pas déduite
        p.update(next(iter(p.positions)), 0.5)  # stop loss de l'ancienne position
        self.assertAlmostEqual(p.capital, capital)  # sa sortie ne crédite pas le nouveau compte
        self.assertEqual(p.stats()["total_trades"], 1)


class TestScript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._sauve = settings.TRADES_LOG_PATH
        settings.TRADES_LOG_PATH = os.path.join(self.tmp, "trades_log.jsonl")

    def tearDown(self):
        settings.TRADES_LOG_PATH = self._sauve

    def test_sans_oui_rien_n_est_ecrit(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(nouveau_compte.main(["--capital", "100"]), 0)
        self.assertIsNone(cp.compte_actuel())

    def test_avec_oui_le_compte_est_ouvert(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(nouveau_compte.main(["--capital", "100", "--oui"]), 0)
        self.assertEqual(cp.compte_actuel().capital, 100.0)

    def test_solde_negatif_refuse(self):
        with redirect_stdout(io.StringIO()):
            self.assertEqual(nouveau_compte.main(["--capital", "-5", "--oui"]), 2)
        self.assertIsNone(cp.compte_actuel())


if __name__ == "__main__":
    unittest.main()
