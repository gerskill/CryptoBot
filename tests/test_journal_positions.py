"""Un trade = une POSITION, pas une sortie finale.

Le bug verrouillé ici : `read_final_exits()` ne garde que les lignes
`is_final_exit`. Un TP1 est une vente PARTIELLE, donc une ligne séparée. Sur
les 40 lignes du journal réel, ça donnait 1 gagnant sur 36 pour -214,39 $ alors
que 4 positions étaient gagnantes pour -166,46 $ : 47,93 $ de profit invisibles
pour les statistiques, l'apprentissage, le backtest, le dashboard et le verrou
du mode réel.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.journal import TradeJournal  # noqa: E402
from src.core.portfolio import PaperPortfolio  # noqa: E402


def leg(position_id, pnl_pct, pnl_usd, fraction, is_final, reason, **extra):
    row = {
        "id": f"{position_id}-{reason}",
        "position_id": position_id,
        "token": extra.pop("token", "TEST"),
        "pnl_pct": pnl_pct,
        "pnl_usd": pnl_usd,
        "fraction_of_initial": fraction,
        "position_size": extra.pop("position_size", 12.5),
        "is_final_exit": is_final,
        "exit_reason": reason,
    }
    row.update(extra)
    return row


class TestLectureParPosition(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))

    def _callcat(self):
        """Le cas réel : TP1 à +101% sur la moitié, puis breakeven à -1,8%."""
        self.journal._append(
            leg("p1", 100.69, 12.99, 0.5, False, "TAKE_PROFIT_1 (+101%)", token="CALLCAT")
        )
        self.journal._append(
            leg("p1", -1.77, -0.23, 0.5, True, "BREAKEVEN_STOP (-1.8%)", token="CALLCAT")
        )

    def test_position_exclue_est_ignoree_par_read_positions(self):
        """Incident du 2026-08-06 : stop loss effectif positif sur 4 bras,
        sorties sans trajectoire de prix observable après l'entrée. La ligne
        marquée `excluded_from_learning` reste dans le fichier (`read_all` la
        voit) mais `read_positions` — la source unique du capital, des stats
        et de l'apprentissage — l'ignore, sans qu'aucune donnée soit inventée
        pour la remplacer."""
        self.journal._append(
            leg(
                "buggy1", -10.0, -1.25, 1.0, True, "STOP_LOSS (-10.0%)",
                token="BUGGY", excluded_from_learning=True,
                exclusion_reason="incident 2026-08-06 : seuil effectif positif",
            )
        )
        self.journal._append(leg("ok1", 20.0, 2.5, 1.0, True, "STOP_LOSS (-10.0%)", token="OK"))

        self.assertEqual(len(self.journal.read_all()), 2)
        positions = self.journal.read_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["token"], "OK")

    def test_regroupe_les_jambes_dune_position(self):
        self._callcat()
        positions = self.journal.read_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["legs"], 2)

    def test_le_pnl_inclut_la_vente_partielle(self):
        self._callcat()
        position = self.journal.read_positions()[0]
        self.assertAlmostEqual(position["pnl_usd"], 12.76, places=2)
        self.assertGreater(position["pnl_usd"], 0, "CALLCAT est un trade GAGNANT")

    def test_le_pnl_pct_est_pondere_par_la_fraction_vendue(self):
        # +101% sur la moitié n'est pas +101% sur la position entière.
        self._callcat()
        position = self.journal.read_positions()[0]
        self.assertAlmostEqual(position["pnl_pct"], 49.46, places=1)

    def test_read_final_exits_classait_ce_gagnant_en_perte(self):
        # La régression elle-même, gardée explicite.
        self._callcat()
        final = self.journal.read_final_exits()[0]
        self.assertLess(final["pnl_usd"], 0)
        self.assertGreater(self.journal.read_positions()[0]["pnl_usd"], 0)

    def test_le_chemin_de_sortie_est_conserve(self):
        self._callcat()
        position = self.journal.read_positions()[0]
        self.assertEqual(len(position["exit_path"]), 2)
        self.assertTrue(position["exit_path"][0].startswith("TAKE_PROFIT_1"))
        self.assertTrue(position["exit_reason"].startswith("BREAKEVEN_STOP"))

    def test_la_taille_engagee_additionne_les_jambes(self):
        self._callcat()
        self.assertAlmostEqual(self.journal.read_positions()[0]["position_size"], 25.0)

    def test_le_pnl_de_la_jambe_finale_reste_accessible(self):
        # Mesurer le glissement d'un stop demande le P&L de la JAMBE qui l'a
        # touché : comparer +49% (position) à un seuil de 0% (breakeven)
        # donnait un « dépassement moyen » positif, donc absurde.
        self._callcat()
        position = self.journal.read_positions()[0]
        self.assertAlmostEqual(position["final_leg_pnl_pct"], -1.77)
        self.assertNotAlmostEqual(position["final_leg_pnl_pct"], position["pnl_pct"])

    def test_position_sans_jambe_finale_est_ignoree(self):
        # Une position encore ouverte ne doit pas compter comme un trade.
        self.journal._append(leg("p2", 100.0, 10.0, 0.5, False, "TAKE_PROFIT_1 (+100%)"))
        self.assertEqual(self.journal.read_positions(), [])

    def test_sortie_simple_reste_une_position(self):
        self.journal._append(leg("p3", -12.4, -1.55, 1.0, True, "STOP_LOSS (-12.4%)"))
        positions = self.journal.read_positions()
        self.assertEqual(len(positions), 1)
        self.assertAlmostEqual(positions[0]["pnl_pct"], -12.4)
        self.assertEqual(positions[0]["legs"], 1)

    def test_ordre_chronologique_conserve(self):
        self.journal._append(leg("a", -10, -1.0, 1.0, True, "STOP_LOSS", token="A"))
        self.journal._append(leg("b", -10, -1.0, 1.0, True, "STOP_LOSS", token="B"))
        self.assertEqual([p["token"] for p in self.journal.read_positions()], ["A", "B"])


class TestStatsParPosition(unittest.TestCase):
    """Le win rate du portefeuille compte les positions, pas les jambes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))

    def _journal_reel_reduit(self):
        """1 gagnant par TP1 + 3 perdants : WR attendu 25%, pas 0%."""
        self.journal._append(leg("w", 100.69, 12.99, 0.5, False, "TAKE_PROFIT_1 (+101%)"))
        self.journal._append(leg("w", -1.77, -0.23, 0.5, True, "BREAKEVEN_STOP (-1.8%)"))
        for i in range(3):
            self.journal._append(leg(f"l{i}", -14.0, -3.5, 1.0, True, "STOP_LOSS (-14.0%)"))

    def _portfolio(self):
        return PaperPortfolio(
            1000.0,
            self.journal,
            positions_path=os.path.join(self.tmp, "open.json"),
            cooldown_hours=0,
        )

    def test_win_rate_compte_les_positions(self):
        self._journal_reel_reduit()
        stats = self._portfolio().stats()
        self.assertEqual(stats["total_trades"], 4)
        self.assertEqual(stats["win_rate"], 25.0)

    def test_pnl_realise_inclut_les_ventes_partielles(self):
        self._journal_reel_reduit()
        stats = self._portfolio().stats()
        self.assertAlmostEqual(stats["total_pnl_usd"], 12.76 - 10.5, places=2)

    def test_une_position_gagnante_casse_la_serie_de_pertes(self):
        # Avant : la jambe finale (-1,8%) prolongeait la série et déclenchait
        # le cooldown sur un trade pourtant gagnant.
        for i in range(2):
            self.journal._append(leg(f"l{i}", -14.0, -3.5, 1.0, True, "STOP_LOSS (-14.0%)"))
        self.journal._append(leg("w", 100.69, 12.99, 0.5, False, "TAKE_PROFIT_1 (+101%)"))
        self.journal._append(leg("w", -1.77, -0.23, 0.5, True, "BREAKEVEN_STOP (-1.8%)"))
        self.assertEqual(self._portfolio().consecutive_losses, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
