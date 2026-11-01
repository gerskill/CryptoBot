"""Bras de test `scaled_be` (2026-10-10) : entrée progressive + breakeven.

Verrouille :
  - entrée à 1/3 de la taille normale, complément de 2/3 UNE fois à +5 % ;
  - aucun ajout sous le seuil, ni après une sortie ;
  - stop de toute la position remonté au prix MOYEN exact dès l'ajout ;
  - avant l'ajout, stop et sorties identiques au témoin ;
  - P&L / taille du journal = taille réelle (1/3 puis totale) ;
  - garde « coût de sortie estimé > 5 % » opt-in ;
  - bras existants inchangés (tout est désactivé par défaut).
"""

import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.arm import bootstrap_arms  # noqa: E402
from src.core.journal import TradeJournal  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.core.portfolio import PaperPortfolio  # noqa: E402
from src.core.positions import Position, scale_in_due  # noqa: E402
from tests.test_arms import ArmsTestCase  # noqa: E402

EXITS = {
    "stop_loss_pct": -25,
    "take_profit_1": 150.0,
    "take_profit_2": 300,
    "take_profit_3": 500,
    "breakeven_trigger": 100,
    "partial_sell_tp1_pct": 0.5,
    "trailing_stop_activation": 200,
    "trailing_stop_distance_pct": 80,
    "max_hold_time_minutes": 420,
    "stop_loss_slippage_buffer_pct": 10.0,
}
SCALED = {
    "scale_in_trigger_pct": 5.0,
    "scale_in_initial_fraction": 0.3333333333,
    "max_exit_cost_pct_entry": 5.0,
}


def _params(scaled: bool = True) -> dict:
    return {"exit_rules": dict(EXITS), "entry_rules": dict(SCALED) if scaled else {}}


def _candidate() -> Candidate:
    return Candidate(token_address="TOKEN", symbol="TST", name="Test", chain="solana",
                     price_usd=1.0)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.journal = TradeJournal(os.path.join(self.tmp, "trades_log.jsonl"))
        self.pf = PaperPortfolio(
            capital=7000.0, journal=self.journal,
            positions_path=os.path.join(self.tmp, "open_positions.json"),
            cooldown_hours=0,
        )

    def _open(self, scaled=True, size=90.0) -> Position:
        return self.pf.open(_candidate(), _params(scaled), size)

    def _events(self):
        path = self.journal.events_path
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]


class TestEntreeUnTiers(_Base):
    def test_entree_initiale_un_tiers(self):
        pos = self._open()
        self.assertAlmostEqual(pos.size_usd, 30.0, places=3)
        self.assertAlmostEqual(pos.scale_in_pending_usd, 60.0, places=3)
        self.assertEqual(pos.scale_in_trigger_pct, 5.0)
        self.assertFalse(pos.scaled_in)
        self.assertAlmostEqual(self.pf.capital, 7000.0 - 30.0, places=3)
        self.assertEqual(pos.initial_entry_price, 1.0)

    def test_stop_initial_identique_au_temoin_avant_ajout(self):
        pos = self._open()
        self.assertEqual(pos.effective_stop_loss_pct, -15.0)
        rows = self.pf.update(pos.id, 0.84)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["exit_reason"].startswith("STOP_LOSS"))
        # perte sur le TIERS engagé seulement
        self.assertAlmostEqual(rows[0]["position_size"], 30.0, places=3)
        self.assertAlmostEqual(rows[0]["pnl_usd"], 30.0 * -0.16, places=3)
        self.assertFalse(rows[0]["scaled_in"])
        self.assertEqual(self._events(), [])


class TestAjout(_Base):
    def test_pas_dajout_sous_le_seuil(self):
        pos = self._open()
        self.assertEqual(self.pf.update(pos.id, 1.049), [])
        pos = self.pf.positions[pos.id]
        self.assertFalse(pos.scaled_in)
        self.assertAlmostEqual(pos.size_usd, 30.0, places=3)
        self.assertEqual(self._events(), [])

    def test_ajout_a_plus_5_et_stop_au_prix_moyen(self):
        pos = self._open()
        rows = self.pf.update(pos.id, 1.05)
        self.assertEqual(rows, [], "l'ajout n'est pas une sortie")
        pos = self.pf.positions[pos.id]
        self.assertTrue(pos.scaled_in)
        self.assertAlmostEqual(pos.size_usd, 90.0, places=3)
        self.assertEqual(pos.scale_in_pending_usd, 0.0)
        avg = 90.0 / (30.0 / 1.0 + 60.0 / 1.05)
        self.assertAlmostEqual(pos.entry_price, avg, places=9)
        self.assertAlmostEqual(self.pf.capital, 7000.0 - 90.0, places=3)
        self.assertTrue(pos.breakeven_moved)
        self.assertEqual(pos.effective_stop_loss_pct, 0.0, "prix moyen exact, sans tampon")
        # événement journalisé à part, pas dans trades_log
        events = self._events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "SCALE_IN")
        self.assertAlmostEqual(events[0]["add_usd"], 60.0, places=3)
        self.assertAlmostEqual(events[0]["size_after_usd"], 90.0, places=3)
        self.assertEqual(self.journal.read_all(), [])
        # persistance : un redémarrage relit l'état ajouté
        reloaded = PaperPortfolio(
            capital=7000.0, journal=self.journal,
            positions_path=os.path.join(self.tmp, "open_positions.json"), cooldown_hours=0,
        )
        self.assertTrue(reloaded.positions[pos.id].scaled_in)
        self.assertAlmostEqual(reloaded.capital, 7000.0 - 90.0, places=3)

    def test_un_seul_ajout(self):
        pos = self._open()
        self.pf.update(pos.id, 1.05)
        self.pf.update(pos.id, 1.10)
        self.assertEqual(len(self._events()), 1)
        self.assertAlmostEqual(self.pf.positions[pos.id].size_usd, 90.0, places=3)

    def test_sortie_breakeven_sur_toute_la_position(self):
        pos = self._open()
        self.pf.update(pos.id, 1.05)
        avg = self.pf.positions[pos.id].entry_price
        self.assertEqual(self.pf.update(pos.id, avg * 1.001), [])
        price = avg * 0.999
        rows = self.pf.update(pos.id, price)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row["exit_reason"].startswith("BREAKEVEN_STOP"))
        self.assertTrue(row["is_final_exit"])
        self.assertAlmostEqual(row["position_size"], 90.0, places=3)
        self.assertAlmostEqual(row["pnl_usd"], 90.0 * (price / avg - 1), places=3)
        self.assertTrue(row["scaled_in"])
        self.assertAlmostEqual(row["initial_size_usd"], 30.0, places=3)
        self.assertAlmostEqual(row["total_size_usd"], 90.0, places=3)
        self.assertEqual(row["initial_entry_price"], 1.0)
        self.assertNotIn(pos.id, self.pf.positions)
        agg = self.journal.read_positions()[0]
        self.assertAlmostEqual(agg["position_size"], 90.0, places=3)
        # capital : -90 engagés, rendus avec la (petite) perte
        self.assertAlmostEqual(self.pf.capital, 7000.0 + row["pnl_usd"], places=3)

    def test_saut_au_dessus_du_seuil_ajoute_au_prix_observe(self):
        pos = self._open()
        self.pf.update(pos.id, 1.20)
        pos = self.pf.positions[pos.id]
        self.assertTrue(pos.scaled_in)
        self.assertAlmostEqual(pos.scale_in_price, 1.20)
        self.assertAlmostEqual(pos.entry_price, 90.0 / (30.0 + 60.0 / 1.2), places=9)

    def test_pas_dajout_apres_une_sortie(self):
        pos = self._open()
        # TP1 artificiellement bas : la sortie partielle passe avant tout ajout
        from dataclasses import replace
        self.pf.positions[pos.id] = replace(pos, take_profit_1=4.0)
        rows = self.pf.update(pos.id, 1.06)
        self.assertEqual(len(rows), 1)
        self.assertFalse(self.pf.positions[pos.id].scaled_in)
        self.pf.update(pos.id, 1.15)
        self.assertFalse(self.pf.positions[pos.id].scaled_in)
        self.assertFalse(scale_in_due(self.pf.positions[pos.id], 1.15))


class TestBrasExistantsInchanges(_Base):
    def test_open_sans_entry_rules_taille_pleine(self):
        pos = self._open(scaled=False)
        self.assertEqual(pos.size_usd, 90.0)
        self.assertEqual(pos.scale_in_trigger_pct, 0.0)
        self.assertEqual(pos.scale_in_pending_usd, 0.0)
        self.assertEqual(pos.initial_entry_price, 0.0)
        self.pf.update(pos.id, 1.20)
        self.assertFalse(self.pf.positions[pos.id].scaled_in)
        self.assertEqual(self.pf.positions[pos.id].size_usd, 90.0)
        rows = self.pf.update(pos.id, 0.80)
        self.assertNotIn("scaled_in", rows[0], "aucun champ ajouté aux lignes des autres bras")
        self.assertEqual(self._events(), [])

    def test_position_par_defaut(self):
        pos = Position(token_address="a", symbol="b", chain="c", entry_price=1, size_usd=1)
        self.assertEqual(pos.scale_in_trigger_pct, 0.0)
        self.assertFalse(scale_in_due(pos, 10.0))


class _Params:
    def __init__(self, data):
        self.data = data

    def get(self, path, default=None):
        node = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


class TestGardeCoutDeSortie(unittest.TestCase):
    def _guard(self, entry_rules, cost):
        from src.main import AlphaLoop

        jupiter = SimpleNamespace(
            enabled=True,
            price=lambda mint: 150.0,
            sellable=lambda *a, **k: (True, ""),
            round_trip_cost_pct=lambda *a, **k: cost,
        )
        fake = SimpleNamespace(jupiter=jupiter, cache=SimpleNamespace(blacklist=lambda *a: None))
        arm = SimpleNamespace(
            params=_Params({"exit_rules": dict(EXITS), "entry_rules": entry_rules}),
            portfolio=SimpleNamespace(stats=lambda: {"win_rate": 0.0, "total_trades": 0}),
        )
        return AlphaLoop._economics_guard(fake, _candidate(), 70.0, arm)

    def test_refus_au_dessus_de_5(self):
        size, verdict = self._guard(dict(SCALED), 5.5)
        self.assertEqual(size, 0.0)
        self.assertIn("max_exit_cost_pct_entry", verdict["reason"])

    def test_passe_sous_5(self):
        size, verdict = self._guard(dict(SCALED), 4.0)
        self.assertNotIn("max_exit_cost_pct_entry", (verdict or {}).get("reason", ""))

    def test_desactive_par_defaut(self):
        size, verdict = self._guard({}, 5.5)
        self.assertNotIn("max_exit_cost_pct_entry", (verdict or {}).get("reason", ""))

    def test_devis_indisponible_ne_rejette_pas(self):
        size, verdict = self._guard(dict(SCALED), None)
        self.assertEqual(size, 70.0)


class TestManifeste(ArmsTestCase):
    def test_bras_scaled_be_isole_et_temoin_inchange(self):
        self._manifest([
            {"name": "baseline", "role": "voter", "capital_pct": 1.0, "notify": "all"},
            {"name": "scaled_be", "role": "test", "capital_pct": 0.0, "learning": False,
             "overrides": {f"entry_rules.{k}": v for k, v in SCALED.items()}},
        ])
        arms = {a.name: a for a in bootstrap_arms()}
        arm = arms["scaled_be"]
        self.assertTrue(arm.is_test)
        self.assertFalse(arm.is_voter)
        self.assertIsNone(arm.learning)
        self.assertEqual(arm.params.get("entry_rules.scale_in_trigger_pct"), 5.0)
        self.assertEqual(arm.params.get("entry_rules.max_exit_cost_pct_entry"), 5.0)
        self.assertIsNone(arms["baseline"].params.get("entry_rules.scale_in_trigger_pct"))
        self.assertEqual(arm.params.get("filters"), arms["baseline"].params.get("filters"))
        self.assertEqual(arm.params.get("exit_rules"), arms["baseline"].params.get("exit_rules"))


class TestManifesteReel(unittest.TestCase):
    def test_config_reelle(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "config", "strategies.json"), encoding="utf-8") as fh:
            arms = {a["name"]: a for a in json.load(fh)["arms"]}
        entry = arms["scaled_be"]
        self.assertEqual(entry["role"], "test")
        self.assertFalse(entry["learning"])
        self.assertEqual(entry["capital_pct"], 0.0)
        for name, arm in arms.items():
            if name != "scaled_be":
                for key in (arm.get("overrides") or {}):
                    self.assertNotIn("scale_in", key, name)
                    self.assertNotIn("max_exit_cost_pct_entry", key, name)



# ---------------------------------------------------------------------------
# 2026-10-10 10:40 — fenêtre +5..+10 %, devis réel, sécurité, coût (fail closed)

from src.apis.jupiter import SOL_MINT, SwapQuote  # noqa: E402
from src.core.scale_in import (  # noqa: E402
    ScaleInCheck,
    check_scale_in,
    effective_price_from_quote,
)

SOL = 150.0
DEC = 6


class FakeJupiter:
    """Devis déterministes. `buy_premium` : prix effectif = observé x (1+p)."""

    def __init__(self, observed, buy_premium=0.01, sell_impact=1.5, buy_impact=1.0,
                 fail_buy=False, no_sell_route=False, sell_ratio=None):
        self.enabled = True
        self.observed = observed
        self.buy_premium = buy_premium
        self.sell_impact = sell_impact
        self.buy_impact = buy_impact
        self.fail_buy = fail_buy
        self.no_sell_route = no_sell_route
        self.sell_ratio = sell_ratio
        self.calls = []

    def quote(self, inp, out, amount, timeout=None, max_retries=None):
        self.calls.append((inp, out, amount, timeout, max_retries))
        if inp == SOL_MINT:
            if self.fail_buy:
                return None
            usd = amount / 1e9 * SOL
            tokens = usd / (self.observed * (1 + self.buy_premium))
            return SwapQuote(inp, out, amount, int(tokens * 10 ** DEC), self.buy_impact)
        if self.no_sell_route:
            return None
        lamports_back = int(self._last_buy_lamports() * (self.sell_ratio or (1 - self.sell_impact / 100)))
        return SwapQuote(inp, out, amount, lamports_back, self.sell_impact)

    def _last_buy_lamports(self):
        return [c for c in self.calls if c[0] == SOL_MINT][-1][2]


def _checker(jup, min_liq=65000.0, max_cost=5.0):
    def checker(position, add_usd, observed_price, liquidity_usd):
        return check_scale_in(
            jup, position.token_address, add_usd=add_usd,
            total_usd=position.size_usd + add_usd, observed_price=observed_price,
            liquidity_usd=liquidity_usd, min_liquidity_usd=min_liq,
            max_exit_cost_pct=max_cost, sol_price_fn=lambda: SOL,
        )
    return checker


def _params_v2() -> dict:
    p = _params(True)
    p["entry_rules"]["scale_in_max_pct"] = 10.0
    return p


class _BaseV2(_Base):
    def _open2(self, jup=None, size=90.0):
        self.pf.scale_in_checker = _checker(jup) if jup is not None else None
        return self.pf.open(_candidate(), _params_v2(), size)


class TestEffectivePrice(unittest.TestCase):
    def test_decimales_deduites(self):
        eff, dec = effective_price_from_quote(60.0, int(60 / 1.0605 * 10 ** 6), 1.05)
        self.assertEqual(dec, 6)
        self.assertAlmostEqual(eff, 1.0605, places=5)

    def test_incoherent_rejete(self):
        eff, _ = effective_price_from_quote(60.0, int(60 / 5.0 * 10 ** 6), 1.0)
        self.assertIsNone(eff)


class TestFenetreEtDevis(_BaseV2):
    def test_ajout_dans_la_fenetre_au_prix_du_devis(self):
        jup = FakeJupiter(observed=1.06, buy_premium=0.02)
        pos = self._open2(jup)
        self.assertEqual(pos.scale_in_max_pct, 10.0)
        self.pf.update(pos.id, 1.06, liquidity_usd=100000)
        pos = self.pf.positions[pos.id]
        self.assertTrue(pos.scaled_in)
        eff = 1.06 * 1.02
        self.assertAlmostEqual(pos.scale_in_price, eff, places=4)
        self.assertAlmostEqual(pos.scale_in_observed_price, 1.06)
        avg = 90.0 / (30.0 / 1.0 + 60.0 / eff)
        self.assertAlmostEqual(pos.entry_price, avg, places=4)
        self.assertEqual(pos.effective_stop_loss_pct, 0.0)
        ev = self._events()[-1]
        self.assertEqual(ev["event"], "SCALE_IN")
        self.assertEqual(ev["price_source"], "jupiter_quote")
        self.assertAlmostEqual(ev["round_trip_total_pct"], 2.5)
        # timeouts courts, sans réessai
        self.assertTrue(all(c[3] is not None and c[3] <= 3 and c[4] == 1 for c in jup.calls))

    def test_stop_au_prix_moyen_effectif(self):
        jup = FakeJupiter(observed=1.06, buy_premium=0.03)
        pos = self._open2(jup)
        self.pf.update(pos.id, 1.06, liquidity_usd=100000)
        avg = self.pf.positions[pos.id].entry_price
        observed_avg = 90.0 / (30.0 + 60.0 / 1.06)
        self.assertGreater(avg, observed_avg, "prix moyen calculé sur le prix EFFECTIF")
        # entre le prix moyen « observé » et le prix moyen effectif : on sort
        rows = self.pf.update(pos.id, (avg + observed_avg) / 2)
        self.assertTrue(rows[0]["exit_reason"].startswith("BREAKEVEN_STOP"))
        self.assertAlmostEqual(rows[0]["position_size"], 90.0, places=3)

    def test_refus_au_dessus_de_10_observe(self):
        jup = FakeJupiter(observed=1.12)
        pos = self._open2(jup)
        self.pf.update(pos.id, 1.12, liquidity_usd=100000)
        self.pf.update(pos.id, 1.13, liquidity_usd=100000)
        pos = self.pf.positions[pos.id]
        self.assertFalse(pos.scaled_in)
        self.assertEqual(jup.calls, [], "aucun devis hors fenêtre")
        skips = [e for e in self._events() if e["event"] == "SCALE_IN_SKIPPED"]
        self.assertEqual(len(skips), 1, "dédoublonné tant que la raison ne change pas")
        self.assertEqual(skips[0]["reason"], "above_max")

    def test_retour_dans_la_fenetre_autorise(self):
        jup = FakeJupiter(observed=1.08)
        pos = self._open2(jup)
        self.pf.update(pos.id, 1.15, liquidity_usd=100000)
        self.assertFalse(self.pf.positions[pos.id].scaled_in)
        self.pf.update(pos.id, 1.08, liquidity_usd=100000)
        self.assertTrue(self.pf.positions[pos.id].scaled_in)

    def test_refus_si_prix_effectif_au_dela_de_10(self):
        jup = FakeJupiter(observed=1.09, buy_premium=0.03)  # effectif +12,3 %
        pos = self._open2(jup)
        self.pf.update(pos.id, 1.09, liquidity_usd=100000)
        self.assertFalse(self.pf.positions[pos.id].scaled_in)
        ev = self._events()[-1]
        self.assertEqual(ev["reason"], "above_max")
        self.assertIn("effectif", ev["detail"])

    def test_refus_si_devis_echoue(self):
        jup = FakeJupiter(observed=1.06, fail_buy=True)
        pos = self._open2(jup)
        self.pf.update(pos.id, 1.06, liquidity_usd=100000)
        self.assertFalse(self.pf.positions[pos.id].scaled_in)
        self.assertEqual(self._events()[-1]["reason"], "quote_failed")
        # pas de nouveau devis avant le délai de réessai
        n = len(jup.calls)
        self.pf.update(pos.id, 1.06, liquidity_usd=100000)
        self.assertEqual(len(jup.calls), n)
        self.assertAlmostEqual(self.pf.positions[pos.id].size_usd, 30.0, places=3)

    def test_refus_si_securite_echoue_honeypot(self):
        jup = FakeJupiter(observed=1.06, no_sell_route=True)
        pos = self._open2(jup)
        self.pf.update(pos.id, 1.06, liquidity_usd=100000)
        self.assertFalse(self.pf.positions[pos.id].scaled_in)
        self.assertEqual(self._events()[-1]["reason"], "safety_failed")

    def test_refus_si_securite_echoue_liquidite(self):
        jup = FakeJupiter(observed=1.06)
        pos = self._open2(jup)
        self.pf.update(pos.id, 1.06, liquidity_usd=20000)
        self.assertFalse(self.pf.positions[pos.id].scaled_in)
        self.assertEqual(self._events()[-1]["reason"], "safety_failed")
        self.assertEqual(jup.calls, [], "liquidité jugée avant tout devis")

    def test_refus_si_liquidite_inconnue(self):
        jup = FakeJupiter(observed=1.06)
        pos = self._open2(jup)
        self.pf.update(pos.id, 1.06, liquidity_usd=None)
        self.assertFalse(self.pf.positions[pos.id].scaled_in)
        self.assertEqual(self._events()[-1]["reason"], "safety_failed")

    def test_refus_si_cout_total_trop_eleve(self):
        jup = FakeJupiter(observed=1.06, buy_impact=2.0, sell_impact=3.5)
        pos = self._open2(jup)
        self.pf.update(pos.id, 1.06, liquidity_usd=100000)
        self.assertFalse(self.pf.positions[pos.id].scaled_in)
        ev = self._events()[-1]
        self.assertEqual(ev["reason"], "cost_too_high")
        self.assertAlmostEqual(ev["round_trip_total_pct"], 5.5)

    def test_plafond_sans_verificateur_fail_closed(self):
        pos = self._open2(None)
        self.pf.update(pos.id, 1.06, liquidity_usd=100000)
        self.assertFalse(self.pf.positions[pos.id].scaled_in)
        self.assertEqual(self._events()[-1]["reason"], "quote_failed")

    def test_exception_du_verificateur_ne_casse_rien(self):
        pos = self._open2(None)
        def boom(*a):
            raise RuntimeError("réseau")
        self.pf.scale_in_checker = boom
        self.assertEqual(self.pf.update(pos.id, 1.06, liquidity_usd=100000), [])
        self.assertFalse(self.pf.positions[pos.id].scaled_in)

    def test_migration_des_positions_ouvertes(self):
        pos = self.pf.open(_candidate(), _params(True), 90.0)  # ancien format, sans plafond
        self.assertEqual(pos.scale_in_max_pct, 0.0)
        self.assertEqual(self.pf.apply_scale_in_max(10.0), 1)
        self.assertEqual(self.pf.positions[pos.id].scale_in_max_pct, 10.0)


class TestBrasExistantsV2(_Base):
    def test_autres_bras_sans_verificateur_ni_appel(self):
        jup = FakeJupiter(observed=1.06)
        self.pf.scale_in_checker = _checker(jup)
        pos = self.pf.open(_candidate(), _params(False), 90.0)
        self.pf.update(pos.id, 1.06, liquidity_usd=100000)
        self.assertEqual(jup.calls, [])
        self.assertEqual(self.pf.positions[pos.id].size_usd, 90.0)
        self.assertEqual(self._events(), [])

    def test_attach_ne_donne_le_verificateur_qu_au_bras_scaled(self):
        from src.core.arm import attach_portfolios

        class P:
            def __init__(self, d):
                self.d = d
            def get(self, path, default=None):
                return _Params(self.d).get(path, default)

        tmp = tempfile.mkdtemp()
        arms = [
            SimpleNamespace(name="temoin_test", notify="all", journal=TradeJournal(os.path.join(tmp, "a.jsonl")),
                            params=P(_params(False)), portfolio=None),
            SimpleNamespace(name="scaled_be", notify="none", journal=TradeJournal(os.path.join(tmp, "b.jsonl")),
                            params=P(_params_v2()), portfolio=None),
        ]
        from src import settings
        saved = settings.ARMS_DATA_DIR
        settings.ARMS_DATA_DIR = tmp
        try:
            attach_portfolios(arms, 7000.0, scale_in_checker_factory=lambda arm: (lambda *a: None))
        finally:
            settings.ARMS_DATA_DIR = saved
        self.assertIsNone(arms[0].portfolio.scale_in_checker)
        self.assertIsNotNone(arms[1].portfolio.scale_in_checker)

if __name__ == "__main__":
    unittest.main()
