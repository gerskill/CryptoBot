"""Tests de l'échelle de sortie et du breakeven par niveau — src/core/positions.py."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.models import Candidate  # noqa: E402
from src.core.portfolio import PaperPortfolio, normalise_ladder  # noqa: E402
from src.core.positions import (  # noqa: E402
    Position,
    apply_exit,
    evaluate_exits,
    update_water_marks,
)

ECHELLE = ((10.0, 0.2), (20.0, 0.2), (35.0, 0.2), (60.0, 0.2), (120.0, 0.2))


def position(**kwargs):
    base = dict(
        token_address="addr", symbol="RUN", chain="solana",
        entry_price=1.0, size_usd=100.0, stop_loss_pct=-25.0,
    )
    base.update(kwargs)
    return Position(**base)


def prix(pnl_pct: float) -> float:
    """Prix produisant ce P&L, avec entry_price = 1.0."""
    return 1.0 + pnl_pct / 100


class TestNormaliseLadder(unittest.TestCase):
    def test_listes_json_en_tuples(self):
        self.assertEqual(normalise_ladder([[10, 0.2], [20, 0.3]]), ((10.0, 0.2), (20.0, 0.3)))

    def test_trie_par_niveau(self):
        """Non trié, le parcours s'arrêterait au premier barreau haut."""
        self.assertEqual(
            normalise_ladder([[60, 0.2], [10, 0.2], [35, 0.2]]),
            ((10.0, 0.2), (35.0, 0.2), (60.0, 0.2)),
        )

    def test_barreau_illisible_ignore_sans_tuer_les_autres(self):
        self.assertEqual(
            normalise_ladder([[10, 0.2], "n'importe quoi", [20, 0.3]]),
            ((10.0, 0.2), (20.0, 0.3)),
        )

    def test_fraction_nulle_ou_negative_ecartee(self):
        self.assertEqual(normalise_ladder([[10, 0], [20, -0.1], [30, 0.5]]), ((30.0, 0.5),))

    def test_absente_rend_un_tuple_vide(self):
        for vide in (None, [], ()):
            self.assertEqual(normalise_ladder(vide), ())


class TestEchelle(unittest.TestCase):
    def test_un_barreau_a_la_fois(self):
        p = position(ladder=ECHELLE)
        actions = evaluate_exits(p, prix(12))
        self.assertEqual(len(actions), 1)
        self.assertAlmostEqual(actions[0].fraction, 0.2)
        self.assertEqual(actions[0].rung, 0)
        self.assertFalse(actions[0].is_final)

    def test_sous_le_premier_barreau_rien_ne_sort(self):
        self.assertEqual(evaluate_exits(position(ladder=ECHELLE), prix(9)), [])

    def test_plusieurs_barreaux_sur_un_seul_tick(self):
        """Un memecoin peut passer de +8 % à +140 % entre deux ticks de 5 s.

        Ne remplir qu'un barreau laisserait les autres derrière, à un prix
        déjà redescendu.
        """
        actions = evaluate_exits(position(ladder=ECHELLE), prix(140))
        self.assertEqual([a.rung for a in actions], [0, 1, 2, 3, 4])
        self.assertAlmostEqual(sum(a.fraction for a in actions), 1.0)
        self.assertTrue(actions[-1].is_final)
        self.assertFalse(any(a.is_final for a in actions[:-1]))

    def test_barreaux_deja_remplis_non_rejoues(self):
        p = position(ladder=ECHELLE, ladder_filled=2, remaining_fraction=0.6)
        actions = evaluate_exits(p, prix(40))
        self.assertEqual([a.rung for a in actions], [2])

    def test_ladder_filled_avance_a_l_application(self):
        p = position(ladder=ECHELLE)
        for action in evaluate_exits(p, prix(25)):
            p = apply_exit(p, action, prix(25))
        self.assertEqual(p.ladder_filled, 2)
        self.assertAlmostEqual(p.remaining_fraction, 0.6)

    def test_derniere_fraction_marquee_finale(self):
        """`is_final` déclenche la mesure du coût réel : une seule fois."""
        p = position(ladder=ECHELLE, ladder_filled=4, remaining_fraction=0.2)
        actions = evaluate_exits(p, prix(130))
        self.assertEqual(len(actions), 1)
        self.assertTrue(actions[0].is_final)

    def test_echelle_remplace_les_take_profits(self):
        """Les mélanger sortirait deux fois la même fraction au même tick."""
        p = position(ladder=ECHELLE, take_profit_1=15.0, take_profit_3=100.0)
        actions = evaluate_exits(p, prix(110))
        self.assertTrue(all(a.reason.startswith("LADDER_") for a in actions))

    def test_sans_echelle_le_comportement_dorigine_est_intact(self):
        p = position(take_profit_1=75.0, partial_sell_tp1_pct=0.5)
        actions = evaluate_exits(p, prix(80))
        self.assertEqual(len(actions), 1)
        self.assertTrue(actions[0].reason.startswith("TAKE_PROFIT_1"))
        self.assertIsNone(actions[0].rung)

    def test_stop_loss_prime_sur_l_echelle(self):
        p = position(ladder=ECHELLE, stop_loss_pct=-25.0)
        actions = evaluate_exits(p, prix(-30))
        self.assertEqual(len(actions), 1)
        self.assertIn("STOP_LOSS", actions[0].reason)


class TestBreakevenParNiveau(unittest.TestCase):
    def test_arme_au_niveau_configure(self):
        p = position(breakeven_trigger=30.0)
        self.assertFalse(update_water_marks(p, prix(29)).breakeven_moved)
        self.assertTrue(update_water_marks(p, prix(30)).breakeven_moved)

    def test_reste_arme_apres_redescente(self):
        """Un niveau franchi puis reperdu A ÉTÉ franchi."""
        p = update_water_marks(position(breakeven_trigger=30.0), prix(45))
        self.assertTrue(update_water_marks(p, prix(5)).breakeven_moved)

    def test_zero_desactive(self):
        p = position(breakeven_trigger=0.0)
        self.assertFalse(update_water_marks(p, prix(500)).breakeven_moved)

    def test_le_stop_devient_le_breakeven(self):
        p = update_water_marks(
            position(breakeven_trigger=30.0, stop_loss_pct=-25.0), prix(35)
        )
        self.assertEqual(p.effective_stop_loss_pct, 0.0)
        actions = evaluate_exits(p, prix(-1))
        self.assertEqual(len(actions), 1)
        self.assertIn("BREAKEVEN_STOP", actions[0].reason)

    def test_tp1_arme_toujours_le_breakeven(self):
        """L'armement par niveau s'AJOUTE, il ne remplace rien."""
        p = position(take_profit_1=50.0, breakeven_trigger=0.0)
        actions = evaluate_exits(p, prix(55))
        self.assertTrue(apply_exit(p, actions[0], prix(55)).breakeven_moved)

    def test_ne_peut_qu_armer_plus_tot_jamais_plus_tard(self):
        """Invariant du câblage : aucun bras ne devient plus risqué.

        `quality` a TP1 +150 et breakeven_trigger 100 : il se protège
        désormais à +100 au lieu de +150.
        """
        p = update_water_marks(
            position(take_profit_1=150.0, breakeven_trigger=100.0), prix(120)
        )
        self.assertTrue(p.breakeven_moved)


class TestBoutEnBout(unittest.TestCase):
    class _Journal:
        def read_positions(self):
            return []

        def record_exit(self, position, exit_price, pnl_pct, fraction, reason,
                        is_final, **_):
            return {"pnl_pct": pnl_pct, "pnl_usd": position.realized_pnl_usd,
                    "exit_reason": reason, "is_final_exit": is_final}

    def portefeuille(self, positions_path=None):
        return PaperPortfolio(
            capital=1000.0, journal=self._Journal(), positions_path=positions_path
        )

    def params(self):
        return {"exit_rules": {
            "ladder": [[10, 0.2], [20, 0.2], [35, 0.2], [60, 0.2], [120, 0.2]],
            "breakeven_trigger": 30,
            "stop_loss_pct": -25.0,
        }}

    def candidat(self):
        return Candidate(token_address="addr", symbol="RUN", name="Runner",
                         chain="solana", price_usd=1.0)

    def test_open_lit_l_echelle_et_le_breakeven(self):
        p = self.portefeuille().open(self.candidat(), self.params(), 30.0)
        self.assertEqual(p.ladder, ECHELLE)
        self.assertEqual(p.breakeven_trigger, 30.0)

    def test_montee_progressive_puis_retour_au_breakeven(self):
        portefeuille = self.portefeuille()
        p = portefeuille.open(self.candidat(), self.params(), 30.0)

        portefeuille.update(p.id, prix(22))   # barreaux 1 et 2
        courant = portefeuille.positions[p.id]
        self.assertEqual(courant.ladder_filled, 2)
        self.assertAlmostEqual(courant.remaining_fraction, 0.6)

        portefeuille.update(p.id, prix(38))   # barreau 3, et breakeven armé
        courant = portefeuille.positions[p.id]
        self.assertEqual(courant.ladder_filled, 3)
        self.assertTrue(courant.breakeven_moved)

        rows = portefeuille.update(p.id, prix(-2))  # retombe : breakeven
        self.assertTrue(rows)
        self.assertIn("BREAKEVEN_STOP", rows[-1]["exit_reason"])
        self.assertNotIn(p.id, portefeuille.positions)

    def test_echelle_survit_a_la_serialisation(self):
        """JSON n'a pas de tuple : `ladder` reviendrait en listes imbriquées."""
        import json
        import tempfile

        handle = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        handle.close()
        self.addCleanup(os.unlink, handle.name)

        portefeuille = self.portefeuille(positions_path=handle.name)
        portefeuille.open(self.candidat(), self.params(), 30.0)

        with open(handle.name, encoding="utf-8") as fh:
            self.assertIsInstance(json.load(fh)[0]["ladder"][0], list)

        rendue = next(iter(self.portefeuille(positions_path=handle.name).positions.values()))
        self.assertEqual(rendue.ladder, ECHELLE)
        # Et elle doit encore savoir sortir, ce qu'une liste imbriquée casserait.
        self.assertEqual(len(evaluate_exits(rendue, prix(12))), 1)


if __name__ == "__main__":
    unittest.main()
