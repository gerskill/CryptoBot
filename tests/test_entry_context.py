"""Journal enrichi à l'entrée (2026-10-10) : observationnel, persistant, sans effet sur les décisions."""

import glob
import json
import os
import sys
import tempfile
import unittest
from dataclasses import asdict, replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.entry_context import build_entry_context  # noqa: E402
from src.core.journal import TradeJournal  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.core.portfolio import PaperPortfolio  # noqa: E402
from src.core.positions import Position, evaluate_exits, update_water_marks  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARAMS = {"version": "2.0", "exit_rules": {"stop_loss_pct": -25, "take_profit_1": 100}}


def _cand(**kw) -> Candidate:
    base = dict(
        token_address="TOK", symbol="TOK", name="Token", chain="solana", price_usd=1.0,
        liquidity_usd=50_000.0, volume_1h=100_000.0, volume_liquidity_ratio=2.0,
        price_change_5m=12.0, buys_5m=30, sells_5m=10, holders=900, top10_holder_pct=22.5,
        rugcheck_score=99.0, smart_money_buys_30m=3, smart_money_wallets_30m=2,
        lp_locked_pct=100.0, rugcheck_risks=("mutable metadata",),
        alpha_score=70.0, alpha_score_absolute=72.0,
        sub_scores={"liquidity": 80.0, "_weights_used": 0.8},
    )
    base.update(kw)
    return Candidate(**base)


class TestConstruction(unittest.TestCase):
    def test_champs_connus_et_absents(self):
        ctx = build_entry_context(_cand(), 2.456)
        self.assertEqual(ctx["entry_roundtrip_cost_pct"], 2.456)
        self.assertEqual(ctx["entry_top10_holder_pct"], 22.5)
        self.assertEqual(ctx["entry_buy_sell_ratio_5m"], 0.75)
        self.assertEqual(ctx["entry_smart_money_wallets_30m"], 2)
        self.assertEqual(ctx["entry_lp_locked_pct"], 100.0)
        self.assertEqual(ctx["entry_rugcheck_risks"], ["mutable metadata"])
        # absents -> None, jamais 0
        self.assertIsNone(ctx["entry_social_mentions_1h"])
        self.assertIsNone(ctx["entry_dev_wallet_pct"])
        self.assertIsNone(ctx["alpha_comp_social_sentiment"])
        # composantes absolues recalculées
        self.assertIsNotNone(ctx["alpha_comp_liquidity"])
        self.assertIsNotNone(ctx["alpha_comp_volume_momentum"])
        self.assertEqual(ctx["alpha_comp_rugcheck"], 99.0)
        self.assertEqual(ctx["alpha_sub_scores"]["liquidity"], 80.0)
        self.assertTrue(0 < ctx["score_v2_prob"] < 1)
        json.dumps(ctx)  # sérialisable

    def test_cout_inconnu_reste_null(self):
        ctx = build_entry_context(_cand(buys_5m=0, sells_5m=0), None)
        self.assertIsNone(ctx["entry_roundtrip_cost_pct"])
        self.assertIsNone(ctx["entry_buy_sell_ratio_5m"])
        self.assertNotIn("exit_cost_pct", ctx)

    def test_prefixes(self):
        for key in build_entry_context(_cand(), 1.0):
            self.assertTrue(key.startswith(("entry_", "alpha_comp_", "alpha_sub_", "score_v2_")), key)


class TestPersistanceEtJournal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.positions = os.path.join(self.tmp, "open_positions.json")
        self.trades = os.path.join(self.tmp, "trades_log.jsonl")

    def _portfolio(self):
        return PaperPortfolio(7000.0, TradeJournal(self.trades), positions_path=self.positions)

    def _rows(self):
        with open(self.trades, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_survit_au_redemarrage_et_ecrit_sur_la_sortie(self):
        ctx = build_entry_context(_cand(), 3.1)
        pf = self._portfolio()
        pos = pf.open(_cand(), PARAMS, 70.0, entry_context=ctx)
        reloaded = self._portfolio()  # redémarrage
        self.assertEqual(reloaded.positions[pos.id].entry_context, json.loads(json.dumps(ctx)))
        reloaded.update(pos.id, 0.5)  # stop
        rows = self._rows()
        self.assertTrue(rows)
        row = rows[-1]
        self.assertEqual(row["entry_roundtrip_cost_pct"], 3.1)
        self.assertEqual(row["entry_top10_holder_pct"], 22.5)
        self.assertIn("score_v2_prob", row)
        self.assertIn("exit_cost_pct", row)  # colonne de sortie distincte, intacte

    def test_ne_remplace_jamais_une_colonne(self):
        pf = self._portfolio()
        pos = pf.open(_cand(), PARAMS, 70.0, entry_context={"pnl_pct": 999, "score_alpha": -1})
        pf.update(pos.id, 0.5)
        row = self._rows()[-1]
        self.assertNotEqual(row["pnl_pct"], 999)
        self.assertEqual(row["score_alpha"], 70.0)

    def test_ancien_fichier_sans_contexte(self):
        pf = self._portfolio()
        pos = pf.open(_cand(), PARAMS, 70.0)
        payload = [asdict(p) for p in pf.positions.values()]
        for p in payload:
            p.pop("entry_context")
        with open(self.positions, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        reloaded = self._portfolio()
        self.assertEqual(reloaded.positions[pos.id].entry_context, {})
        reloaded.update(pos.id, 0.5)
        self.assertNotIn("entry_roundtrip_cost_pct", self._rows()[-1])


class TestAucuneDecisionChangee(unittest.TestCase):
    def test_sorties_identiques_avec_ou_sans_contexte(self):
        base = Position(token_address="T", symbol="T", chain="solana", entry_price=1.0,
                        size_usd=100.0, stop_loss_pct=-25.0, take_profit_1=50.0)
        rich = replace(base, entry_context=build_entry_context(_cand(), 2.0))
        self.assertEqual(base, rich)  # le contexte n'entre pas dans l'égalité
        for price in (1.2, 1.6, 0.7, 0.5, 2.5):
            a = evaluate_exits(update_water_marks(base, price), price)
            b = evaluate_exits(update_water_marks(rich, price), price)
            self.assertEqual(a, b)

    def test_filtre_v2_actif_seulement_sur_score_v2(self):
        with open(os.path.join(ROOT, "config", "params.json"), encoding="utf-8") as fh:
            self.assertFalse((json.load(fh).get("entry_rules") or {}).get("score_v2_min_prob"))
        for path in glob.glob(os.path.join(ROOT, "config", "arms", "*.json")):
            name = os.path.basename(path)[:-5]
            if name == "score_v2":
                continue
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertFalse((doc.get("entry_rules") or {}).get("score_v2_min_prob"), name)


if __name__ == "__main__":
    unittest.main()
