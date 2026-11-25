"""Bras de test `anti_rug` et `mature_liquid` (2026-10-11).

Verrouille :
  - la porte anti-arnaque (chaque critère, données manquantes réévaluables,
    repli sur les autorités du candidat, instantané trop vieux) ;
  - le top 10 des WALLETS hors pool/courbe (comptes détenus par un programme) ;
  - l'opt-in : un bras sans `anti_rug_enabled` ne passe jamais par la porte ;
  - la source « mature » : hors enveloppe de découverte, hors watchlist et hors
    arbitrage social, donc les autres bras voient EXACTEMENT le même lot ;
  - le manifeste de production : 14 bras actifs, les 2 nouveaux en rôle test.
"""

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import main as main_module  # noqa: E402
from src.agents.onchain_observer import SYSTEM_PROGRAM, wallet_concentration  # noqa: E402
from src.apis.dexscreener import DexScreenerAPI  # noqa: E402
from src.core.anti_rug import DEFAULTS, anti_rug_gate  # noqa: E402
from src.core.arm import TEST, bootstrap_arms  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.pipeline import CollectedBatch, discovery_envelope  # noqa: E402
from tests.test_arms import ArmsTestCase  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _snap(**over):
    snap = {
        "onchain_status": "ok", "onchain_age_at_decision_sec": 30.0,
        "onchain_mint_authority_revoked": True, "onchain_freeze_authority_revoked": True,
        "onchain_top10_wallets_pct": 12.0, "onchain_creator_pct_top20": 0.0,
        "onchain_dev_out_transfers": 2, "onchain_dev_sold": True,
        "onchain_launch_slot_buyers": 2, "onchain_fresh_top10": 0,
        "onchain_fresh_first_buyers": 0,
    }
    snap.update(over)
    return snap


def _cand(addr="T", alpha=80.0, **kw):
    return Candidate(token_address=addr, symbol=addr, name=addr, chain="solana", **kw).with_fields(
        alpha_score_absolute=alpha, alpha_score=alpha)


class TestPorteAntiRug(unittest.TestCase):
    def test_token_propre_passe(self):
        v = anti_rug_gate(_snap(), _cand())
        self.assertTrue(v.passed, v.reason)
        self.assertFalse(v.missing)

    def test_chaque_critere_refuse(self):
        cases = {
            "autorité mint": dict(onchain_mint_authority_revoked=False),
            "autorité freeze": dict(onchain_freeze_authority_revoked=False),
            "top10 wallets": dict(onchain_top10_wallets_pct=20.5),
            "créateur": dict(onchain_creator_pct_top20=6.0),
            "transferts sortants": dict(onchain_dev_out_transfers=6),
            "slot de lancement": dict(onchain_launch_slot_buyers=5),
            "top 10 >": dict(onchain_fresh_top10=2),
            "premiers acheteurs": dict(onchain_fresh_first_buyers=2),
        }
        for needle, over in cases.items():
            v = anti_rug_gate(_snap(**over), _cand())
            self.assertFalse(v.passed, needle)
            self.assertFalse(v.missing, needle)
            self.assertIn(needle, v.reason)

    def test_seuils_inclusifs(self):
        v = anti_rug_gate(_snap(onchain_top10_wallets_pct=20.0, onchain_launch_slot_buyers=4,
                                onchain_dev_out_transfers=5, onchain_fresh_top10=1,
                                onchain_fresh_first_buyers=1, onchain_creator_pct_top20=5.0),
                          _cand())
        self.assertTrue(v.passed, v.reason)

    def test_vente_du_dev_stricte_en_option(self):
        self.assertTrue(anti_rug_gate(_snap(onchain_dev_sold=True), _cand()).passed)
        v = anti_rug_gate(_snap(onchain_dev_sold=True), _cand(),
                          {"anti_rug_reject_any_dev_sale": True})
        self.assertFalse(v.passed)
        self.assertIn("dev a vendu", v.reason)

    def test_donnees_pas_pretes_sont_manquantes(self):
        for status in ("pending", "missing", "disabled", "budget", "error", None):
            v = anti_rug_gate({"onchain_status": status}, _cand())
            self.assertFalse(v.passed)
            self.assertTrue(v.missing, status)
            self.assertIn("données manquantes", v.reason)

    def test_champ_absent_est_manquant(self):
        v = anti_rug_gate(_snap(onchain_top10_wallets_pct=None), _cand())
        self.assertFalse(v.passed)
        self.assertTrue(v.missing)
        self.assertIn("top10_wallets_pct", v.reason)

    def test_mauvaise_donnee_presente_prime_sur_le_manque(self):
        v = anti_rug_gate(_snap(onchain_top10_wallets_pct=None, onchain_launch_slot_buyers=8),
                          _cand())
        self.assertFalse(v.passed)
        self.assertFalse(v.missing)

    def test_autorites_du_candidat_en_repli(self):
        snap = _snap(onchain_mint_authority_revoked=None, onchain_freeze_authority_revoked=None)
        self.assertTrue(anti_rug_gate(snap, _cand(mint_authority_revoked=True,
                                                  freeze_authority_revoked=True)).passed)
        v = anti_rug_gate(snap, _cand(mint_authority_revoked=False, freeze_authority_revoked=True))
        self.assertFalse(v.passed)
        self.assertFalse(v.missing)
        self.assertTrue(anti_rug_gate(snap, _cand()).missing)

    def test_instantane_trop_vieux(self):
        v = anti_rug_gate(_snap(onchain_age_at_decision_sec=4000), _cand())
        self.assertFalse(v.passed)
        self.assertTrue(v.missing)

    def test_defauts_documentes(self):
        self.assertEqual(DEFAULTS["anti_rug_max_top10_wallets_pct"], 20.0)
        self.assertEqual(DEFAULTS["anti_rug_max_launch_slot_buyers"], 4)


class TestTop10Wallets(unittest.TestCase):
    def test_exclut_pool_et_burn(self):
        ranked = [("POOL", 50.0), ("W1", 10.0), ("1nc1nerator11111111111111111111111111111111", 5.0),
                  ("W2", 4.0), ("VIDE", 1.0)]
        programs = {"POOL": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "W1": SYSTEM_PROGRAM,
                    "1nc1nerator11111111111111111111111111111111": SYSTEM_PROGRAM,
                    "W2": SYSTEM_PROGRAM, "VIDE": None}
        out = wallet_concentration(ranked, programs, 100.0)
        self.assertEqual(out["onchain_top10_wallets_pct"], 15.0)
        self.assertEqual(out["onchain_program_holders_top20"], 2)
        self.assertEqual(out["onchain_program_holders_pct"], 55.0)


class TestTryEntriesAntiRug(unittest.TestCase):
    def _run(self, params, snapshots, candidates):
        recorded = []
        funnel = SimpleNamespace(record=lambda *a, **k: recorded.append(a))
        arm = SimpleNamespace(
            name="anti_rug", role=TEST, entry_threshold=55, max_positions=3,
            params=SimpleNamespace(get=lambda k, d=None: params.get(k, d)),
            portfolio=SimpleNamespace(capital=7000.0), label="[anti_rug]",
        )
        onchain = mock.Mock()
        onchain.snapshot.side_effect = lambda token, ts=None: snapshots[token]
        fake = SimpleNamespace(
            funnel=funnel, mode="PAPER", learning=None, params={}, onchain=onchain,
            max_sector_positions=3, max_sector_exposure_pct=50,
            _fleet_exposure=lambda: {}, _last_evaluations={}, arms=[], voters=[],
        )
        evaluation = SimpleNamespace(result=SimpleNamespace(candidates=candidates))
        with mock.patch.object(main_module, "pre_trade_check", return_value="STOP test") as ptc, \
                mock.patch.object(main_module, "is_soft_reject", return_value=True):
            main_module.AlphaLoop._try_entries(fake, evaluation, arm, {})
        return recorded, ptc, onchain

    ON = {"entry_rules.anti_rug_enabled": True, "entry_rules": {}}

    def test_donnees_manquantes_puis_reevaluation(self):
        # Cycle 1 : pas prêt -> refus « manquant », jamais pre_trade, mise en file.
        rec, ptc, onchain = self._run(self.ON, {"T": {"onchain_status": "missing"}}, [_cand("T")])
        ptc.assert_not_called()
        onchain.request_many.assert_called_once()
        row = [r for r in rec if r[3] == "anti_rug"][0]
        self.assertFalse(row[4])
        self.assertTrue(row[6]["missing"])
        # Cycle suivant, même token toujours candidat : données prêtes -> passe.
        rec, ptc, _ = self._run(self.ON, {"T": _snap()}, [_cand("T")])
        ptc.assert_called_once()
        self.assertTrue([r for r in rec if r[3] == "anti_rug"][0][4])

    def test_refus_puis_candidat_suivant_evalue(self):
        rec, ptc, _ = self._run(
            self.ON, {"RUG": _snap(onchain_launch_slot_buyers=8), "OK": _snap()},
            [_cand("RUG", 90), _cand("OK", 80)])
        self.assertEqual(ptc.call_count, 1)
        self.assertEqual(ptc.call_args.kwargs["candidate"].token_address, "OK")

    def test_bras_sans_option_ne_lit_rien(self):
        rec, ptc, onchain = self._run({}, {}, [_cand("T")])
        onchain.snapshot.assert_not_called()
        ptc.assert_called_once()
        self.assertNotIn("anti_rug", [r[3] for r in rec])


def _arm(name, filters, discovery="main", social=True, baseline=False):
    return SimpleNamespace(
        name=name, label=f"[{name}]", is_baseline=baseline, entry_threshold=55,
        discovery=discovery, social_wishlist=social,
        filters=lambda f=filters: dict(f),
        params=SimpleNamespace(get=lambda k, d=None: {} if k == "scan.mature_source" else d),
    )


class TestSourceMature(unittest.TestCase):
    def test_lot_des_autres_bras_inchange(self):
        main_batch = CollectedBatch(enriched=(_cand("A"),))
        mature_batch = CollectedBatch(enriched=(_cand("OLD"),))
        envelopes, evaluated, cache_calls, wish = [], {}, [], []

        def evaluate(batch, params=None, label="", social=None, verbose=False):
            evaluated.setdefault(label, []).append(batch)
            addr = {c.token_address for c in batch.enriched}
            return SimpleNamespace(
                result=SimpleNamespace(candidates=batch.enriched),
                kept_addresses=frozenset(addr), rejected_addresses=frozenset(),
                wishlist=tuple(addr))

        pipeline = SimpleNamespace(
            collect=lambda env: envelopes.append(env) or main_batch,
            collect_mature=mock.Mock(return_value=mature_batch),
            evaluate=evaluate,
            enrich_social=lambda enriched, addresses: wish.append(addresses) or {},
            apply_cache_effects=lambda kept, rejected, bl: cache_calls.append(set(kept)),
        )
        base = _arm("baseline", {"max_age_hours": 16.5, "min_liquidity_usd": 80000}, baseline=True)
        anti = _arm("anti_rug", {"max_age_hours": 16.5, "min_liquidity_usd": 80000}, social=False)
        mature = _arm("mature_liquid", {"min_age_hours": 24, "max_age_hours": 720,
                                        "min_liquidity_usd": 250000}, "mature", False)
        fake = SimpleNamespace(
            arms=[base, anti, mature], pipeline=pipeline, params={},
            _observe_wallets=lambda b: None, quota_agent=mock.Mock(),
            voters={"baseline"}, baseline=base, _print_arms=lambda e, p: None,
        )
        with mock.patch.object(main_module, "build_confluence", return_value={}):
            main_module.AlphaLoop._scan_all_arms(fake)
        # Enveloppe = celle des bras « main » seuls (identique sans le bras mature).
        self.assertEqual(envelopes[0], discovery_envelope([base.filters(), anti.filters()]))
        self.assertEqual(envelopes[0]["max_age_hours"], 16.5)
        self.assertTrue(all(b is main_batch for b in evaluated["[baseline]"]))
        self.assertTrue(all(b is mature_batch for b in evaluated["[mature_liquid]"]))
        # Watchlist partagée : jamais les tokens du lot mature.
        self.assertEqual(cache_calls, [{"A"}])
        # Arbitrage social : seul le témoin (les 2 nouveaux n'y entrent pas).
        fake.quota_agent.record_demand.assert_called_once_with("twitter", wanted=1, served=1)

    def test_candidates_for_tokens_ne_touche_pas_le_cache(self):
        cache = mock.Mock()
        api = DexScreenerAPI(cache=cache)
        pair = {"chainId": "solana", "pairAddress": "P", "liquidity": {"usd": 300000},
                "volume": {"h1": 30000}, "pairCreatedAt": 1, "baseToken": {"symbol": "OLD"}}
        with mock.patch.object(api, "get_tokens_data", return_value={"OLD": [pair]}):
            out = api.candidates_for_tokens(["OLD"], min_liquidity=250000, min_volume_1h=20000,
                                            min_age_hours=24, max_age_hours=1e9)
        self.assertEqual([c.token_address for c in out], ["OLD"])
        cache.mark_seen.assert_not_called()


class TestManifesteNouveauxBras(ArmsTestCase):
    def test_production(self):
        with open(os.path.join(ROOT, "config", "strategies.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        active = [a for a in doc["arms"] if a.get("enabled", True)]
        # 19 + 8 familles d'entrée (2026-11-23). Les 7 doublons restent
        # `enabled` (positions ouvertes suivies) mais `paused`.
        self.assertEqual(len(active), 27)  # +tp1_20_vl2 (2026-10-24), +copy_smart (2026-10-29), +fixes3 (2026-11-02), +consensus_af (2026-11-02), +sniper_ar (2026-11-02)
        arms = {a["name"]: a for a in active}
        for name in ("anti_rug", "mature_liquid"):
            e = arms[name]
            self.assertEqual(e["role"], "test")
            self.assertIs(e["learning"], False)
            self.assertEqual(e["capital_pct"], 0.0)
            self.assertEqual(e["notify"], "none")
            self.assertIs(e["social_wishlist"], False)
        self.assertEqual(arms["mature_liquid"]["discovery"], "mature")
        for name, e in arms.items():
            if name not in ("anti_rug", "copy_smart"):  # copy_smart reutilise la porte anti_rug (2026-10-29)
                self.assertFalse(any(k.startswith("entry_rules.anti_rug")
                                     for k in e.get("overrides", {})))
            if name not in ("mature_liquid", "entree_mature"):
                self.assertEqual(e.get("discovery", "main"), "main")
        self.assertAlmostEqual(sum(float(a.get("capital_pct", 0)) for a in active), 1.0)

    def test_fork_du_temoin(self):
        self._manifest([
            {"name": "baseline", "role": "voter", "capital_pct": 1.0, "notify": "all"},
            {"name": "anti_rug", "role": "test", "capital_pct": 0.0, "learning": False,
             "social_wishlist": False, "overrides": {"entry_rules.anti_rug_enabled": True}},
            {"name": "mature_liquid", "role": "test", "capital_pct": 0.0, "learning": False,
             "discovery": "mature", "social_wishlist": False,
             "overrides": {"filters.min_age_hours": 24.0, "filters.max_age_hours": 720.0}},
        ])
        arms = {a.name: a for a in bootstrap_arms(with_journals=False)}
        base, anti, mat = arms["baseline"], arms["anti_rug"], arms["mature_liquid"]
        self.assertEqual(base.discovery, "main")
        self.assertTrue(base.social_wishlist)
        self.assertEqual(mat.discovery, "mature")
        self.assertFalse(anti.social_wishlist)
        for arm in (anti, mat):
            self.assertIsNone(arm.learning)
            self.assertEqual(arm.entry_threshold, base.entry_threshold)
            self.assertEqual(arm.params.get("exit_rules"), base.params.get("exit_rules"))
        self.assertEqual(anti.params.get("filters"), base.params.get("filters"))
        self.assertFalse(base.params.get("entry_rules.anti_rug_enabled", False))

    def test_discovery_inconnue_refusee(self):
        from src.core.arm import ManifestError
        self._manifest([
            {"name": "baseline", "role": "voter", "capital_pct": 1.0, "notify": "all",
             "discovery": "lune"},
        ])
        with self.assertRaises(ManifestError):
            bootstrap_arms(with_journals=False)


if __name__ == "__main__":
    unittest.main()
