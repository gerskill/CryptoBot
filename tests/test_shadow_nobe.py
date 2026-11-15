"""Suivi apparié sans le breakeven indépendant.

Le bug verrouillé ici : `BREAKEVEN_STOP` mélange le déclencheur, le TP1 et
l'ajout de taille. Couper le mot « breakeven » les couperait tous. L'ombre
n'enlève que le déclencheur, et elle ne touche pas l'état du bras.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.positions import Position, update_water_marks  # noqa: E402
from src.core.shadow_nobe import (  # noqa: E402
    REFERENCE_EQUITY_USD,
    RateLimitedQuotes,
    ShadowTracker,
    _jsonable,
    classify_be_transition,
    classify_stop_kind,
    collect_bundle,
    fee_policy_from_costs,
    position_from_mapping,
    select_pair_quote,
    stop_view,
)


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def position_row(**kwargs):
    base = dict(
        token_address="MintShadow111",
        symbol="DOG",
        chain="solana",
        pair_address="PairShadow111",
        entry_price=1.0,
        size_usd=100.0,
        id="pos1",
        mode="PAPER",
        entry_time=time.time(),
        stop_loss_pct=-25.0,
        breakeven_trigger=10.0,
        breakeven_stop_exact=True,
        stop_loss_slippage_buffer_pct=10.0,
        take_profit_1=30.0,
        take_profit_2=300.0,
        take_profit_3=500.0,
        partial_sell_tp1_pct=0.5,
        partial_sell_tp2_pct=0.5,
        trailing_stop_activation=200.0,
        trailing_stop_distance_pct=50.0,
        max_hold_time_minutes=330.0,
        params_version="regle-figee",
    )
    base.update(kwargs)
    return _jsonable(asdict(Position(**base)))


def arm_config(exit_rules, sha="a" * 64, mode="PAPER", entry_rules=None):
    return {
        "sha256": sha,
        "readable": True,
        "exit_rules": exit_rules,
        "entry_rules": entry_rules or {},
        "mode": mode,
    }


def bundle(row, price, ts, journal=None, costs=None, sha="a" * 64, quotes=None, arm="tp1_30"):
    rules = {
        "stop_loss_pct": row["stop_loss_pct"],
        "breakeven_trigger": row["breakeven_trigger"],
        "breakeven_stop_exact": row["breakeven_stop_exact"],
        "stop_loss_slippage_buffer_pct": row["stop_loss_slippage_buffer_pct"],
        "take_profit_1": row["take_profit_1"],
        "take_profit_2": row["take_profit_2"],
        "take_profit_3": row["take_profit_3"],
    }
    return {
        "state_updated_at": ts,
        "state_positions": [
            {"arm": arm, "id": row["id"], "current_price": price, "token_address": row["token_address"]}
        ]
        if price is not None
        else [],
        "continuation_quotes": quotes or {},
        "arms": {
            arm: {
                "open_positions": [row],
                "journal_rows": journal or [],
                "fee_costs": list(costs or []),
                "config": arm_config(rules, sha=sha),
            }
        },
    }


def shadow_of(tracker, pair_id="tp1_30:pos1"):
    pair = tracker.pairs.get(pair_id)
    if pair is None:
        pair = next(row for row in tracker.closed if row["pair_id"] == pair_id)
    return pair, position_from_mapping(pair["position"])


class TestOrigineEtNiveau(unittest.TestCase):
    def test_trois_origines_et_deux_niveaux(self):
        """Le drapeau unique ne dit pas qui l'a posé, ni si le stop est à 0 ou à +10."""
        self.assertEqual(classify_stop_kind(False, False), "initial")
        self.assertEqual(classify_stop_kind(True, True), "exact_zero")
        self.assertEqual(classify_stop_kind(True, False), "profit_floor")
        before = {"breakeven_moved": False, "tp1_hit": False, "scaled_in": False}
        self.assertEqual(
            classify_be_transition(before, {**before, "breakeven_moved": True})[0],
            "independent_trigger",
        )
        self.assertEqual(
            classify_be_transition(before, {**before, "breakeven_moved": True, "tp1_hit": True})[0],
            "tp1",
        )
        self.assertEqual(
            classify_be_transition(before, {**before, "breakeven_moved": True, "scaled_in": True})[0],
            "scale_in",
        )

    def test_plancher_plus_10_et_plus_15_ne_sont_pas_un_zero_exact(self):
        plus_10 = Position(
            token_address="m", symbol="A", chain="solana", entry_price=1, size_usd=1,
            breakeven_moved=True, breakeven_stop_exact=False, stop_loss_slippage_buffer_pct=10,
        )
        plus_15 = Position(
            token_address="m", symbol="A", chain="solana", entry_price=1, size_usd=1,
            breakeven_moved=True, breakeven_stop_exact=False, stop_loss_slippage_buffer_pct=15,
        )
        exact = Position(
            token_address="m", symbol="A", chain="solana", entry_price=1, size_usd=1,
            breakeven_moved=True, breakeven_stop_exact=True, stop_loss_slippage_buffer_pct=10,
        )
        self.assertEqual(stop_view(plus_10)["level_pct"], 10.0)
        self.assertEqual(stop_view(plus_15)["level_pct"], 15.0)
        self.assertEqual(stop_view(exact)["kind"], "exact_zero")
        self.assertEqual(stop_view(exact)["level_pct"], 0.0)
        self.assertEqual(stop_view(plus_10)["kind"], "profit_floor")


class TestOmbreSansDeclencheur(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.clock = Clock(1_700_000_000.0)
        self.tracker = ShadowTracker(self.tmp, clock=self.clock, arms=("tp1_30", "scaled_be"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_avant_le_declencheur_ombre_et_source_ont_le_meme_stop(self):
        """Jusqu'au seuil, couper le déclencheur ne change ni le stop, ni la taille, ni le réalisé."""
        row = position_row()
        source = position_from_mapping(row)
        self.clock.now = row["entry_time"] + 30
        self.tracker.apply(bundle(row, 1.08, self.clock.now))
        source = update_water_marks(source, 1.08)
        pair, shadow = shadow_of(self.tracker)
        self.assertFalse(source.breakeven_moved)
        self.assertFalse(shadow.breakeven_moved)
        self.assertEqual(shadow.effective_stop_loss_pct, source.effective_stop_loss_pct)
        self.assertEqual(shadow.remaining_fraction, 1.0)
        self.assertEqual(shadow.realized_pnl_usd, 0.0)
        self.assertEqual(shadow.breakeven_trigger, 0.0)
        self.assertEqual(pair["effect_status"], "incomplete")

    def test_le_declencheur_exact_arme_la_source_et_pas_l_ombre(self):
        """BE exact à 0 % : la source remonte le stop, l'ombre garde le stop initial."""
        row = position_row(breakeven_stop_exact=True)
        self.clock.now = row["entry_time"] + 20
        self.tracker.apply(bundle(row, 1.05, self.clock.now))
        armed = position_from_mapping(row)
        armed = update_water_marks(armed, 1.10)
        armed_row = _jsonable(asdict(armed))
        self.clock.now += 10
        self.tracker.apply(bundle(armed_row, 1.10, self.clock.now))
        pair, shadow = shadow_of(self.tracker)
        self.assertTrue(armed.breakeven_moved)
        self.assertEqual(stop_view(armed)["kind"], "exact_zero")
        self.assertFalse(shadow.breakeven_moved)
        self.assertEqual(shadow.effective_stop_loss_pct, -15.0)
        self.assertEqual(pair["baseline_stop_origin"], "independent_trigger")
        self.assertNotEqual(pair["shadow_stop_origin"], "independent_trigger")
        self.assertTrue(pair["shadow_open"])

    def test_plancher_plus_10_sort_la_source_et_laisse_l_ombre_ouverte(self):
        """Le plancher +10 sort au déclencheur. L'ombre, elle, n'a pas bougé son stop."""
        row = position_row(
            breakeven_stop_exact=False,
            take_profit_1=150.0,
            stop_loss_slippage_buffer_pct=10.0,
        )
        self.clock.now = row["entry_time"] + 20
        self.tracker.apply(bundle(row, 1.05, self.clock.now))
        self.clock.now += 10
        exit_row = {
            "id": "pos1-1",
            "position_id": "pos1",
            "is_final_exit": True,
            "exit_reason": "BREAKEVEN_STOP (10.0%)",
            "exit_price": 1.10,
            "entry_price": 1.0,
            "position_size": 100.0,
            "pnl_usd": 10.0,
            "pnl_pct": 10.0,
            "timestamp_exit": self.clock.now,
            "scaled_in": False,
            "stop_loss_trigger_pct": 10.0,
        }
        gone = bundle(row, None, self.clock.now, journal=[exit_row])
        gone["arms"]["tp1_30"]["open_positions"] = []
        gone["state_positions"] = []
        self.tracker.apply(gone)
        pair, shadow = shadow_of(self.tracker)
        self.assertFalse(shadow.breakeven_moved)
        self.assertTrue(pair["shadow_open"])
        self.assertFalse(pair["baseline_open"])
        self.assertEqual(pair["baseline_stop_origin"], "independent_trigger")
        self.assertEqual(pair["baseline_stop_events"][-1]["kind"], "profit_floor")
        self.assertEqual(pair["baseline_stop_events"][-1]["level_pct"], 10.0)
        self.assertEqual(pair["horizon_basis"], "independent_trigger")
        self.assertEqual(pair["effect_status"], "incomplete")
        self.assertIsNone(pair["delta_net_usd"])
        self.assertIsNotNone(pair["live_delta_net_usd"])

    def test_tp1_conserve_la_vente_et_le_stop_exact(self):
        """Couper le déclencheur ne coupe pas le TP1, ni le stop qu'il arme."""
        row = position_row()
        self.clock.now = row["entry_time"] + 15
        self.tracker.apply(bundle(row, 1.02, self.clock.now))
        hit = position_from_mapping(row)
        hit = update_water_marks(hit, 1.30)
        from src.core.positions import ExitAction, apply_exit

        hit = apply_exit(hit, ExitAction(0.5, "TAKE_PROFIT_1 (+30%)"), 1.30)
        hit_row = _jsonable(asdict(hit))
        self.clock.now += 10
        partial = {
            "id": "pos1-tp1",
            "position_id": "pos1",
            "is_final_exit": False,
            "exit_reason": "TAKE_PROFIT_1 (+30%)",
            "exit_price": 1.30,
            "entry_price": 1.0,
            "position_size": 50.0,
            "fraction_of_initial": 0.5,
            "pnl_usd": 15.0,
            "pnl_pct": 30.0,
            "timestamp_exit": self.clock.now,
        }
        self.tracker.apply(bundle(hit_row, 1.30, self.clock.now, journal=[partial]))
        pair, shadow = shadow_of(self.tracker)
        self.assertTrue(shadow.tp1_hit)
        self.assertTrue(shadow.breakeven_moved)
        self.assertEqual(stop_view(shadow)["kind"], "exact_zero")
        self.assertAlmostEqual(shadow.remaining_fraction, 0.5)
        self.assertEqual(pair["shadow_stop_origin"], "tp1")
        self.assertEqual(pair["baseline_stop_origin"], "tp1")
        self.assertNotEqual(pair["shadow_stop_origin"], "independent_trigger")
        self.assertIsNone(pair["horizon_basis"])

    def test_plancher_tp1_plus_15_reste_un_plancher(self):
        row = position_row(breakeven_stop_exact=False, stop_loss_slippage_buffer_pct=15.0, take_profit_1=30.0)
        self.clock.now = row["entry_time"] + 10
        self.tracker.apply(bundle(row, 1.0, self.clock.now))
        self.clock.now += 5
        hit = position_from_mapping(row)
        from src.core.positions import ExitAction, apply_exit

        hit = update_water_marks(hit, 1.30)
        hit = apply_exit(hit, ExitAction(0.5, "TAKE_PROFIT_1 (+30%)"), 1.30)
        self.tracker.apply(bundle(_jsonable(asdict(hit)), 1.30, self.clock.now))
        _pair, shadow = shadow_of(self.tracker)
        view = stop_view(shadow)
        self.assertEqual(view["kind"], "profit_floor")
        self.assertEqual(view["level_pct"], 15.0)

    def test_scale_in_ajoute_la_taille_et_pose_le_stop_exact(self):
        """L'ajout n'est pas le déclencheur : il grossit la position et vise le prix moyen."""
        row = position_row(
            id="sc1",
            size_usd=30.0,
            breakeven_trigger=10.0,
            breakeven_stop_exact=False,
            stop_loss_slippage_buffer_pct=10.0,
            take_profit_1=150.0,
            scale_in_trigger_pct=5.0,
            scale_in_max_pct=10.0,
            scale_in_pending_usd=60.0,
            initial_size_usd=30.0,
            initial_entry_price=1.0,
        )
        self.clock.now = row["entry_time"] + 10
        payload = bundle(row, 1.02, self.clock.now, arm="scaled_be")
        self.tracker.apply(payload)
        grown = dict(row)
        grown.update(
            size_usd=90.0,
            scaled_in=True,
            breakeven_moved=True,
            breakeven_stop_exact=True,
            scale_in_price=1.06,
            scale_in_pending_usd=0.0,
            entry_price=1.03,
        )
        self.clock.now += 5
        payload2 = bundle(grown, 1.06, self.clock.now, arm="scaled_be")
        self.tracker.apply(payload2)
        pair, shadow = shadow_of(self.tracker, "scaled_be:sc1")
        self.assertTrue(shadow.scaled_in)
        self.assertAlmostEqual(shadow.size_usd, 90.0)
        self.assertEqual(pair["shadow_stop_origin"], "scale_in")
        self.assertEqual(stop_view(shadow)["kind"], "exact_zero")
        self.assertEqual(stop_view(shadow)["level_pct"], 0.0)
        self.assertEqual(shadow.breakeven_trigger, 0.0)
        self.assertNotEqual(pair["shadow_stop_origin"], "independent_trigger")


class TestFraisEtDonnees(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.clock = Clock(1_700_000_000.0)
        self.tracker = ShadowTracker(self.tmp, clock=self.clock)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_partiel_moitie_mediane_et_final_non_reecrit(self):
        """La médiane est figée à l'entrée. Un coût mesuré plus tard ne la remplace pas."""
        row = position_row(breakeven_trigger=0.0, breakeven_stop_exact=True)
        self.clock.now = row["entry_time"] + 5
        costs = [4.0] * 10
        self.tracker.apply(bundle(row, 1.01, self.clock.now, costs=costs))
        policy = fee_policy_from_costs(costs)
        self.assertEqual(policy["partial_cost_pct"], 2.0)
        self.assertEqual(policy["final_cost_assumption_pct"], 4.0)
        self.clock.now += 5
        hit = position_from_mapping(row)
        from src.core.positions import ExitAction, apply_exit

        hit = apply_exit(hit, ExitAction(0.5, "TAKE_PROFIT_1 (+30%)"), 1.30)
        self.tracker.apply(bundle(_jsonable(asdict(hit)), 1.30, self.clock.now, costs=[50.0] * 12))
        pair, _shadow = shadow_of(self.tracker)
        self.assertEqual(pair["fee"]["final_cost_assumption_pct"], 4.0)
        self.assertEqual(pair["shadow_legs"][0]["fee_source"], "partial_median_half_frozen_at_entry")
        self.assertEqual(pair["shadow_legs"][0]["cost_pct_of_sold"], 2.0)
        self.clock.now += 5
        # Sous le stop exact armé par le TP1 : jambe finale, toujours l'ancienne médiane.
        self.tracker.apply(bundle(hit and _jsonable(asdict(hit)), 0.99, self.clock.now, costs=[80.0] * 12))
        pair, shadow = shadow_of(self.tracker)
        finals = [leg for leg in pair["shadow_legs"] if leg["is_final"]]
        self.assertTrue(finals)
        self.assertEqual(finals[-1]["fee_source"], "median_round_trip_frozen_at_entry")
        self.assertEqual(finals[-1]["cost_pct_of_sold"], 4.0)
        self.assertFalse(shadow.is_open)

    def test_sous_dix_mesures_la_jambe_reste_au_prix_nu(self):
        row = position_row(breakeven_trigger=0.0)
        self.clock.now = row["entry_time"] + 5
        self.tracker.apply(bundle(row, 1.0, self.clock.now, costs=[4.0, 5.0, 6.0]))
        self.clock.now += 5
        self.tracker.apply(bundle(row, 1.30, self.clock.now, costs=[4.0, 5.0, 6.0]))
        pair, _shadow = shadow_of(self.tracker)
        self.assertEqual(pair["fee"]["name"], "naked_insufficient_sample")
        self.assertEqual(pair["shadow_legs"][0]["cost_on_stake_pct"], 0.0)
        self.assertAlmostEqual(pair["shadow_legs"][0]["net_usd"], pair["shadow_legs"][0]["gross_usd"])

    def test_cout_final_plafonne_a_la_mise(self):
        """Un devis absurde ne fait pas perdre plus que la mise, comme sur le bras réel."""
        row = position_row(
            breakeven_trigger=0.0,
            breakeven_stop_exact=False,
            stop_loss_slippage_buffer_pct=0.0,
            stop_loss_pct=-25.0,
            take_profit_1=500.0,
        )
        self.clock.now = row["entry_time"] + 5
        self.tracker.apply(bundle(row, 1.0, self.clock.now, costs=[3728.0] * 10))
        self.clock.now += 5
        self.tracker.apply(bundle(row, 0.005, self.clock.now))
        pair, shadow = shadow_of(self.tracker)
        self.assertFalse(shadow.is_open)
        self.assertAlmostEqual(shadow.realized_pnl_usd, -100.0, places=2)
        self.assertGreater(shadow.realized_pnl_usd, -100.01)

    def test_prix_perime_ou_absent_ne_sort_pas(self):
        row = position_row(breakeven_trigger=0.0, stop_loss_slippage_buffer_pct=0.0)
        self.clock.now = row["entry_time"] + 5
        self.tracker.apply(bundle(row, 1.0, self.clock.now))
        self.clock.now += 200
        stale = bundle(row, 0.10, self.clock.now - 120)
        self.tracker.apply(stale)
        pair, shadow = shadow_of(self.tracker)
        self.assertTrue(shadow.is_open)
        self.assertGreaterEqual(pair["stale_quotes"], 1)
        missing = bundle(row, None, self.clock.now)
        missing["state_positions"] = []
        self.tracker.apply(missing)
        pair, shadow = shadow_of(self.tracker)
        self.assertTrue(shadow.is_open)
        self.assertGreaterEqual(pair["missing_quotes"], 1)

    def test_cote_en_retard_ignoree(self):
        row = position_row(breakeven_trigger=0.0, stop_loss_slippage_buffer_pct=0.0)
        self.clock.now = row["entry_time"] + 5
        self.tracker.apply(bundle(row, 1.0, self.clock.now))
        late = bundle(row, 0.10, self.clock.now - 30)
        self.tracker.apply(late)
        pair, shadow = shadow_of(self.tracker)
        self.assertTrue(shadow.is_open)
        self.assertGreaterEqual(pair["late_quotes_ignored"], 1)

    def test_trou_avant_un_stop_censure_sans_prix_invente(self):
        row = position_row(breakeven_trigger=0.0, stop_loss_slippage_buffer_pct=0.0, stop_loss_pct=-25.0)
        self.clock.now = row["entry_time"] + 5
        self.tracker.apply(bundle(row, 1.0, self.clock.now))
        self.clock.now += 200
        self.tracker.apply(bundle(row, 0.50, self.clock.now))
        pair, shadow = shadow_of(self.tracker)
        self.assertEqual(pair["terminal"], "censored_ambiguous_gap")
        self.assertEqual(pair["effect_status"], "censored")
        self.assertIsNone(pair["delta_net_usd"])
        self.assertEqual(pair["shadow_legs"], [])
        self.assertEqual(shadow.realized_pnl_usd, 0.0)

    def test_censure_si_aucune_cote_jusqu_apres_le_time_stop(self):
        old = time.time() - (400 * 60)
        row = position_row(entry_time=old, max_hold_time_minutes=330.0, breakeven_trigger=0.0)
        self.clock.now = time.time()
        payload = bundle(row, None, self.clock.now)
        payload["state_positions"] = []
        self.tracker.apply(payload)
        pair, _shadow = shadow_of(self.tracker)
        self.assertEqual(pair["terminal"], "censored_no_quote")
        self.assertEqual(pair["shadow_legs"], [])
        self.assertIsNone(pair["delta_net_usd"])

    def test_horizons_remplis_ou_manques_sans_interpolation(self):
        row = position_row(breakeven_stop_exact=False, take_profit_1=150.0)
        self.clock.now = row["entry_time"] + 20
        self.tracker.apply(bundle(row, 1.04, self.clock.now))
        exit_ts = self.clock.now + 10
        journal = [{
            "id": "pos1-be",
            "position_id": "pos1",
            "is_final_exit": True,
            "exit_reason": "BREAKEVEN_STOP (10.0%)",
            "exit_price": 1.10,
            "entry_price": 1.0,
            "position_size": 100.0,
            "pnl_usd": 10.0,
            "timestamp_exit": exit_ts,
            "scaled_in": False,
        }]
        self.clock.now = exit_ts
        gone = bundle(row, None, exit_ts, journal=journal)
        gone["arms"]["tp1_30"]["open_positions"] = []
        gone["state_positions"] = []
        self.tracker.apply(gone)
        quote_ts = exit_ts + 5 * 60 + 20
        self.clock.now = quote_ts
        follow = bundle(row, None, quote_ts)
        follow["arms"]["tp1_30"]["open_positions"] = []
        follow["state_positions"] = []
        follow["continuation_quotes"] = {
            row["token_address"]: {"matched": True, "price": 1.25, "ts": quote_ts, "reason": "anchored"}
        }
        self.tracker.apply(follow)
        pair, _shadow = shadow_of(self.tracker)
        self.assertEqual(pair["horizons"]["5"]["status"], "filled")
        self.assertAlmostEqual(pair["horizons"]["5"]["pnl_vs_be_exit_pct"], 100 * (1.25 / 1.10 - 1), places=2)
        self.assertNotIn("15", pair["horizons"])
        self.clock.now = exit_ts + 15 * 60 + 120
        empty = bundle(row, None, self.clock.now)
        empty["arms"]["tp1_30"]["open_positions"] = []
        empty["state_positions"] = []
        self.tracker.apply(empty)
        pair, _shadow = shadow_of(self.tracker)
        self.assertEqual(pair["horizons"]["15"]["status"], "missed")
        self.assertNotIn("price", pair["horizons"]["15"])


class TestRepriseEtIsolation(unittest.TestCase):
    def test_redemarrage_et_crash_ne_dedoublonnent_pas(self):
        tmp = tempfile.mkdtemp()
        try:
            clock = Clock(1_700_000_000.0)
            tracker = ShadowTracker(tmp, clock=clock)
            row = position_row(breakeven_trigger=0.0, stop_loss_slippage_buffer_pct=0.0, stop_loss_pct=-25.0)
            clock.now = row["entry_time"] + 5
            tracker.apply(bundle(row, 1.0, clock.now))
            state_path = os.path.join(tmp, "data", "shadow_nobe", "state.json")
            checkpoint = open(state_path, encoding="utf-8").read()
            before = len(tracker.journal_events())
            clock.now += 5
            tracker.apply(bundle(row, 0.50, clock.now))
            after = len(tracker.journal_events())
            self.assertGreater(after, before)
            # Deuxième passage du même tick : pas une seconde jambe.
            tracker.apply(bundle(row, 0.50, clock.now))
            self.assertEqual(len(tracker.journal_events()), after)
            # Crash : le checkpoint d'avant la sortie, le journal d'après.
            with open(state_path, "w", encoding="utf-8") as handle:
                handle.write(checkpoint)
            revived = ShadowTracker(tmp, clock=clock)
            pair, shadow = shadow_of(revived)
            self.assertFalse(shadow.is_open)
            revived.apply(bundle(row, 0.50, clock.now))
            self.assertEqual(len(revived.journal_events()), after)
            self.assertEqual(shadow_of(revived)[1].realized_pnl_usd, shadow.realized_pnl_usd)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_fichiers_source_et_capital_ne_bougent_pas(self):
        tmp = tempfile.mkdtemp()
        try:
            arm = os.path.join(tmp, "data", "arms", "tp1_30")
            os.makedirs(arm)
            row = position_row()
            open_path = os.path.join(arm, "open_positions.json")
            journal_path = os.path.join(arm, "trades_log.jsonl")
            config_dir = os.path.join(tmp, "config", "arms")
            os.makedirs(config_dir)
            config_path = os.path.join(config_dir, "tp1_30.json")
            state_path = os.path.join(tmp, "data", "state.json")
            with open(open_path, "w", encoding="utf-8") as handle:
                json.dump([row], handle)
            with open(journal_path, "w", encoding="utf-8") as handle:
                handle.write("")
            with open(config_path, "w", encoding="utf-8") as handle:
                json.dump({"mode": "PAPER", "exit_rules": {"breakeven_trigger": 10}}, handle)
            with open(state_path, "w", encoding="utf-8") as handle:
                json.dump({"updated_at": row["entry_time"] + 5, "positions": [
                    {"arm": "tp1_30", "id": "pos1", "current_price": 1.02}
                ]}, handle)
            digest = {path: hashlib.sha256(open(path, "rb").read()).hexdigest()
                      for path in (open_path, journal_path, config_path, state_path)}
            clock = Clock(row["entry_time"] + 5)
            tracker = ShadowTracker(tmp, clock=clock)
            payload, _offsets, _cache = collect_bundle(tmp, ("tp1_30",))
            tracker.apply(payload)
            for path, sha in digest.items():
                self.assertEqual(hashlib.sha256(open(path, "rb").read()).hexdigest(), sha, path)
            shadow_dir = os.path.join(tmp, "data", "shadow_nobe")
            self.assertTrue(os.path.exists(os.path.join(shadow_dir, "heartbeat.json")))
            # Le capital du bras n'existe pas dans le suivi : 7000 est une référence.
            stats = json.load(open(os.path.join(shadow_dir, "paired_stats.json"), encoding="utf-8"))
            self.assertTrue(stats["not_alternate_portfolio"])
            self.assertTrue(stats["not_arm_cash"])
            self.assertEqual(stats["reference_equity_usd"], REFERENCE_EQUITY_USD)
            self.assertFalse(stats["live_capable"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_mode_live_refuse_sans_ouvrir_d_ombre(self):
        tmp = tempfile.mkdtemp()
        try:
            clock = Clock(1_700_000_000.0)
            tracker = ShadowTracker(tmp, clock=clock)
            row = position_row(mode="LIVE")
            clock.now = row["entry_time"]
            tracker.apply(bundle(row, 1.0, clock.now))
            self.assertEqual(tracker.pairs, {})
            self.assertEqual(tracker.skipped.get("tp1_30:pos1"), "refused_not_paper")
            self.assertFalse(tracker.live_capable)
            self.assertFalse(ShadowTracker.live_capable)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_config_changee_apres_entree_reste_comparable(self):
        tmp = tempfile.mkdtemp()
        try:
            clock = Clock(1_700_000_000.0)
            tracker = ShadowTracker(tmp, clock=clock)
            row = position_row()
            clock.now = row["entry_time"] + 5
            tracker.apply(bundle(row, 1.02, clock.now, sha="a" * 64))
            rules_sha = tracker.pairs["tp1_30:pos1"]["rules_sha256"]
            clock.now += 5
            tracker.apply(bundle(row, 1.03, clock.now, sha="b" * 64))
            pair, _shadow = shadow_of(tracker)
            self.assertTrue(pair["config_changed_after_entry"])
            self.assertEqual(pair["rules_sha256"], rules_sha)
            self.assertTrue(pair["policy_status"].startswith("comparable_frozen"))
            self.assertIn("fichier a bougé", " ".join(
                event.get("note", "") for event in tracker.journal_events() if event.get("kind") == "config_changed"
            ))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_borne_de_journal_et_de_positions_closes(self):
        tmp = tempfile.mkdtemp()
        try:
            clock = Clock(1_700_000_000.0)
            tracker = ShadowTracker(tmp, clock=clock, max_log_bytes=600, max_closed=2)
            for index in range(8):
                tracker._emit(f"bulk-{index}", {"kind": "mark", "pad": "x" * 200})
            rotated = os.path.join(tmp, "data", "shadow_nobe", "events.1.jsonl")
            self.assertTrue(os.path.exists(rotated))
            current = os.path.join(tmp, "data", "shadow_nobe", "events.jsonl")
            if os.path.exists(current):
                self.assertLessEqual(os.path.getsize(current), 600 + 500)
            for index in range(3):
                row = position_row(
                    id=f"c{index}",
                    breakeven_trigger=0.0,
                    stop_loss_slippage_buffer_pct=0.0,
                    entry_time=clock.now - 10,
                )
                clock.now += 1
                tracker.apply(bundle(row, 1.0, clock.now))
                clock.now += 1
                tracker.apply(bundle(row, 0.5, clock.now))
            self.assertLessEqual(len(tracker.closed), 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_paire_dex_et_plafond_reseau(self):
        now = 1_700_000_000.0
        self.assertFalse(select_pair_quote(
            [{"pairAddress": "autre", "chainId": "solana", "priceUsd": "2"}],
            "attendue", "solana", now,
        )["matched"])
        hit = select_pair_quote(
            [{"pairAddress": "attendue", "chainId": "solana", "priceUsd": "1.5", "liquidity": {"usd": 10}}],
            "attendue", "solana", now,
        )
        self.assertTrue(hit["matched"])
        self.assertEqual(hit["price"], 1.5)
        self.assertEqual(
            select_pair_quote(
                [{"chainId": "solana", "priceUsd": "1"}, {"chainId": "solana", "priceUsd": "2"}],
                None, "solana", now,
            )["reason"],
            "multi_pair_no_anchor",
        )
        calls = []

        def fetch(tokens):
            calls.append(list(tokens))
            return {"Mint": {"matched": True, "price": 1.0, "ts": now}}

        clock = Clock(now)
        feed = RateLimitedQuotes(fetch, min_interval=15, max_per_min=4, clock=clock)
        self.assertTrue(feed.get(["Mint"]))
        self.assertEqual(feed.get(["Mint"]), {})
        self.assertEqual(len(calls), 1)
        capped = RateLimitedQuotes(fetch, min_interval=0, max_per_min=4, clock=clock)
        for _step in range(6):
            capped.get(["Mint"])
        self.assertEqual(capped.served, 4)

    def test_position_disparue_sans_journal_nest_pas_un_effet_exact(self):
        tmp = tempfile.mkdtemp()
        try:
            clock = Clock(1_700_000_000.0)
            tracker = ShadowTracker(tmp, clock=clock)
            row = position_row()
            clock.now = row["entry_time"] + 5
            tracker.apply(bundle(row, 1.02, clock.now))
            clock.now += 5
            gone = bundle(row, None, clock.now)
            gone["arms"]["tp1_30"]["open_positions"] = []
            gone["state_positions"] = []
            tracker.apply(gone)
            pair, _shadow = shadow_of(tracker)
            self.assertEqual(pair["effect_status"], "ambiguous")
            self.assertIsNone(pair["delta_net_usd"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
