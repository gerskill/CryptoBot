"""Explication d'une position (scripts/explique_position.py) et règles de sortie au journal.

Question d'origine : OGTA a touché +105,65 % puis est sortie à +3,85 % en BREAKEVEN_STOP, sans prise
de profit. Bug ou règle ? Le journal ne gardait pas le TP1 : impossible de trancher. Verrouillé ici :
les règles sont désormais écrites à chaque sortie, et le diagnostic distingue « règle » d'« anomalie »
sans jamais conclure sans la donnée.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import explique_position as ep
from src.core.journal import TradeJournal
from src.core.models import Candidate
from src.core.portfolio import PaperPortfolio

PARAMS = {
    "version": "2.0", "risk_per_trade": 0.03,
    "exit_rules": {"stop_loss_pct": -25, "take_profit_1": 150, "take_profit_2": 400,
                   "take_profit_3": 800, "partial_sell_tp1_pct": 0.5, "partial_sell_tp2_pct": 0.5,
                   "trailing_stop_activation": 150, "trailing_stop_distance_pct": 50,
                   "max_hold_time_minutes": 240, "breakeven_trigger": 10},
    "filters": {}, "scoring_weights": {}, "learning": {},
}


def ogta(**champs):
    base = {"position_id": "3d7a4da1aa80", "token": "OGTA", "is_final_exit": True,
            "peak_pct": 105.65, "pnl_pct": 3.85, "exit_reason": "BREAKEVEN_STOP (6.1%)",
            "fraction_of_initial": 1.0}
    base.update(champs)
    return base


class TestJournalDesRegles(unittest.TestCase):
    def test_chaque_sortie_porte_les_regles_figees_a_l_entree(self):
        tmp = tempfile.mkdtemp()
        journal = TradeJournal(os.path.join(tmp, "j.jsonl"))
        portefeuille = PaperPortfolio(capital=1000.0, journal=journal)
        c = Candidate(token_address="m", symbol="OGTA", name="OGTA", chain="solana",
                      price_usd=1.0, liquidity_usd=50000, volume_1h=20000)
        position = portefeuille.open(c, PARAMS, 100.0)
        portefeuille.update(position.id, 0.5)  # stop loss
        ligne = journal.read_all()[-1]
        self.assertEqual(ligne["take_profit_1_pct"], 150)
        self.assertEqual(ligne["breakeven_trigger_pct"], 10)
        self.assertEqual(ligne["trailing_activation_pct"], 150)
        self.assertEqual(ligne["max_hold_minutes"], 240)
        self.assertIsNone(ligne["exit_ladder"])


class TestRegles(unittest.TestCase):
    def test_les_regles_du_journal_priment(self):
        regle, provenance = ep.regles([ogta(take_profit_1_pct=150)], {"take_profit_1": 30})
        self.assertEqual(regle["take_profit_1_pct"], 150)
        self.assertIn("journal", provenance)

    def test_a_defaut_la_config_actuelle_avec_avertissement(self):
        regle, provenance = ep.regles([ogta()], {"take_profit_1": 150, "breakeven_trigger": 10})
        self.assertEqual(regle["take_profit_1_pct"], 150)
        self.assertIn("ACTUELLE", provenance)


class TestDiagnostic(unittest.TestCase):
    def test_ogta_avec_tp1_a_150_est_une_regle_pas_un_bug(self):
        constats = ep.diagnostic([ogta()], {"take_profit_1_pct": 150}, [])
        self.assertTrue(constats[0].startswith("règle, pas bug"))
        self.assertTrue(any("rendu 102 points" in c for c in constats))

    def test_pic_au_dessus_du_tp1_sans_jambe_tp1_est_une_anomalie(self):
        constats = ep.diagnostic([ogta()], {"take_profit_1_pct": 30}, [])
        self.assertTrue(constats[0].startswith("ANOMALIE"))

    def test_pic_au_dessus_du_tp1_avec_jambe_tp1_est_normal(self):
        jambes = [ogta(is_final_exit=False, exit_reason="TAKE_PROFIT_1 (+30%)", fraction_of_initial=0.5),
                  ogta()]
        constats = ep.diagnostic(jambes, {"take_profit_1_pct": 30}, [])
        self.assertFalse(any(c.startswith("ANOMALIE") for c in constats))

    def test_echelle_franchie_sans_vente_est_une_anomalie(self):
        constats = ep.diagnostic([ogta()], {"take_profit_1_pct": 150, "exit_ladder": [[10, 0.2], [20, 0.2]]}, [])
        self.assertTrue(constats[0].startswith("ANOMALIE"))
        self.assertIn("échelle", constats[0])

    def test_tp1_inconnu_ne_conclut_pas(self):
        constats = ep.diagnostic([ogta()], {}, [])
        self.assertTrue(constats[0].startswith("TP1 inconnu"))

    def test_mouvement_extreme_rend_le_prix_aberrant_plausible(self):
        constats = ep.diagnostic([ogta()], {"take_profit_1_pct": 150}, [{"extreme": True}])
        self.assertTrue(any("aberrant" in c for c in constats))

    def test_pic_inconnu(self):
        self.assertEqual(ep.diagnostic([ogta(peak_pct=None)], {"take_profit_1_pct": 150}, []),
                         ["pic inconnu : rien à conclure"])


if __name__ == "__main__":
    unittest.main()
