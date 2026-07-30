"""Tests du suivi des rejets, des bornes et du backtest de validation."""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.journal import TradeJournal  # noqa: E402
from src.core.learning import LearningEngine  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.core.params import ParamsStore  # noqa: E402
from src.core.shadow import ShadowTracker, reason_family  # noqa: E402

PARAMS = {
    "version": "2.0",
    "risk_per_trade": 0.03,
    "filters": {
        "min_age_hours": 0.5, "max_age_hours": 6, "min_liquidity_usd": 15000,
        "min_holders": 75, "min_social_mentions_1h": 0,
    },
    "exit_rules": {"stop_loss_pct": -25, "max_hold_time_minutes": 240},
    "scoring_weights": {"liquidity": 0.2, "volume_momentum": 0.25, "rugcheck": 0.15},
    "learning": {},
}


def rejected_candidate(symbol="REJ", reason="liquidité 12000 < 15000", price=1.0):
    return Candidate(
        token_address=f"addr_{symbol}", symbol=symbol, name=symbol, chain="solana",
        price_usd=price, liquidity_usd=12000, rejected_reason=reason,
    )


def _fake_verdict(index, family, won):
    """Objet minimal accepté par ShadowTracker._append."""
    return type("V", (), {
        "symbol": f"T{index}", "token_address": f"a{family}{index}",
        "reason": family, "reason_family": family,
        "entry_price": 1.0, "peak_price": 3.0 if won else 1.0,
        "last_price": 1.0, "peak_gain_pct": 200.0 if won else 0.0,
        "would_have_won": won, "minutes_tracked": 60.0,
    })()


class TestReasonFamily(unittest.TestCase):
    def test_classe_les_motifs(self):
        self.assertEqual(reason_family("liquidité 12000 < 15000"), "liquidity")
        self.assertEqual(reason_family("holders 40 < 75"), "holders")
        self.assertEqual(reason_family("top wallet 25.0% > 20%"), "concentration")
        self.assertEqual(reason_family("risque critique RugCheck"), "rugcheck")
        self.assertEqual(reason_family("mentions sociales 3 < 15"), "social")


class TestShadowTracker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tracker = ShadowTracker(os.path.join(self.tmp, "shadow.jsonl"))

    def test_enregistre_les_rejets(self):
        self.assertEqual(self.tracker.record_rejections([rejected_candidate()]), 1)
        self.assertEqual(self.tracker.stats["tracked"], 1)

    def test_ignore_les_rejets_securite(self):
        # On ne relâchera jamais un filtre honeypot, quel que soit le manque à gagner.
        added = self.tracker.record_rejections(
            [rejected_candidate(reason="risque critique RugCheck : ('Honeypot',)")]
        )
        self.assertEqual(added, 0)

    def test_pas_de_doublon(self):
        self.tracker.record_rejections([rejected_candidate()])
        self.tracker.record_rejections([rejected_candidate()])
        self.assertEqual(self.tracker.stats["tracked"], 1)

    def test_suit_le_pic_de_prix(self):
        self.tracker.record_rejections([rejected_candidate(price=1.0)])
        address = self.tracker.tracked_addresses[0]
        self.tracker.update_price(address, 3.0)
        self.tracker.update_price(address, 1.5)  # redescend

        self.tracker._tracked[address]["rejected_at"] = time.time() - 5 * 3600
        verdicts = self.tracker.expire()
        self.assertEqual(len(verdicts), 1)
        self.assertAlmostEqual(verdicts[0].peak_gain_pct, 200.0)
        self.assertTrue(verdicts[0].would_have_won)

    def test_taux_de_manque_par_famille(self):
        for index in range(20):
            self.tracker._append(
                _fake_verdict(index, "liquidity", won=index < 10),
                {"alpha_absolute": 70, "liquidity": 12000, "age_hours": 1},
            )
        stats = self.tracker.missed_rate_by_family(min_sample=10)
        self.assertEqual(stats["liquidity"]["sample"], 20)
        self.assertEqual(stats["liquidity"]["missed_rate"], 50.0)


class TestBornesEtRelachement(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.params_path = os.path.join(self.tmp, "params.json")
        with open(self.params_path, "w", encoding="utf-8") as fh:
            json.dump(PARAMS, fh)
        self.params = ParamsStore(self.params_path)
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))
        self.tracker = ShadowTracker(os.path.join(self.tmp, "shadow.jsonl"))
        self.engine = LearningEngine(self.params, self.journal, shadow=self.tracker)

    def _shadow_rows(self, count, family, won):
        for index in range(count):
            self.tracker._append(
                _fake_verdict(index, family, won),
                {"alpha_absolute": 70, "liquidity": 12000, "age_hours": 1},
            )

    def test_borne_empeche_le_cliquet_infini(self):
        # min_age_hours plafonné à 3.0 : au-delà, plus aucun ajustement.
        self.params.set("filters.min_age_hours", 3.0, log=False)
        self.assertIsNone(self.engine._bounded_set("filters.min_age_hours", 3.5, "test", 10))
        self.assertEqual(self.params.get("filters.min_age_hours"), 3.0)

    def test_valeur_clampee_a_la_borne(self):
        change = self.engine._bounded_set("filters.min_liquidity_usd", 500000, "test", 10)
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 100000)
        self.assertIn("100000", change)

    def test_relache_un_filtre_qui_rejette_des_gagnants(self):
        self._shadow_rows(15, "liquidity", won=True)
        changes = self.engine._relax_from_shadow()
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 10000)
        self.assertTrue(changes)

    def test_ne_relache_pas_si_les_rejets_etaient_justes(self):
        self._shadow_rows(20, "liquidity", won=False)
        self.assertEqual(self.engine._relax_from_shadow(), [])
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 15000)

    def test_ne_relache_pas_sous_le_seuil_dechantillon(self):
        self._shadow_rows(5, "liquidity", won=True)
        self.assertEqual(self.engine._relax_from_shadow(), [])
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 15000)


class TestBacktestValidation(unittest.TestCase):
    """Étape 6.4 : un ajustement de filtres doit prouver son gain."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.params_path = os.path.join(self.tmp, "params.json")
        with open(self.params_path, "w", encoding="utf-8") as fh:
            json.dump(PARAMS, fh)
        self.params = ParamsStore(self.params_path)
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))
        self.engine = LearningEngine(self.params, self.journal)

    def _trade(self, pnl_usd, liquidity, age=2.0):
        self.journal._append({
            "is_final_exit": True, "pnl_usd": pnl_usd, "pnl_pct": pnl_usd,
            "liquidity_at_entry": liquidity, "age_hours_at_entry": age,
            "holders_at_entry": 500, "social_score": None, "rugcheck_score": 90,
            "exit_reason": "STOP_LOSS" if pnl_usd < 0 else "TAKE_PROFIT_1",
        })

    def test_simulate_exclut_les_trades_hors_filtres(self):
        for _ in range(10):
            self._trade(-20, liquidity=12000)
        for _ in range(10):
            self._trade(+50, liquidity=60000)

        rows = self.journal.read_final_exits()
        large = self.engine.simulate_filters(rows, {"min_liquidity_usd": 15000})
        self.assertEqual(large["trades"], 10)
        self.assertEqual(large["total_pnl_usd"], 500)
        self.assertEqual(large["win_rate"], 100.0)

    def test_annule_un_changement_sans_amelioration(self):
        for _ in range(20):
            self._trade(+10, liquidity=60000)
        avant = self.params.get("filters")
        self.params.set("filters.min_liquidity_usd", 20000, log=False)

        self.assertIn("annulés", self.engine.validate_filter_changes(avant))
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 15000)

    def test_valide_un_changement_qui_ecarte_les_perdants(self):
        # Les perdants doivent être AU-DESSUS du filtre d'origine (15000),
        # sinon ils n'auraient jamais été pris et le backtest ne voit rien.
        for _ in range(10):
            self._trade(-30, liquidity=20000)
        for _ in range(10):
            self._trade(+40, liquidity=60000)
        avant = self.params.get("filters")
        self.params.set("filters.min_liquidity_usd", 30000, log=False)

        self.assertIn("validés", self.engine.validate_filter_changes(avant))
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 30000)

    def test_annule_des_filtres_trop_restrictifs(self):
        for _ in range(20):
            self._trade(+10, liquidity=20000)
        avant = self.params.get("filters")
        self.params.set("filters.min_liquidity_usd", 90000, log=False)  # ne garde rien

        self.assertIn("trop restrictifs", self.engine.validate_filter_changes(avant))
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 15000)

    def test_pas_de_verdict_sous_20_trades(self):
        for _ in range(5):
            self._trade(+10, liquidity=60000)
        self.assertIsNone(self.engine.validate_filter_changes(self.params.get("filters")))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestVerrouModeReel(unittest.TestCase):
    """Le passage en LIVE exige une autorisation explicite du propriétaire."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.params_path = os.path.join(self.tmp, "params.json")
        self.journal_path = os.path.join(self.tmp, "trades.jsonl")
        self.journal = TradeJournal(self.journal_path)
        self._write_params(authorized=False)

    def _write_params(self, authorized):
        data = dict(PARAMS)
        data["live_mode_authorized_by_owner"] = authorized
        with open(self.params_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        self.params = ParamsStore(self.params_path)
        self.engine = LearningEngine(self.params, self.journal)

    def _excellent_track_record(self):
        # 25 trades, 80% de réussite, profit factor très supérieur à 1.5
        for index in range(25):
            self.journal._append({
                "is_final_exit": True,
                "pnl_usd": 100 if index % 5 else -20,
                "pnl_pct": 100 if index % 5 else -20,
                "exit_reason": "TAKE_PROFIT_1",
            })

    def test_refuse_meme_avec_des_statistiques_excellentes(self):
        self._excellent_track_record()
        allowed, why = self.engine.live_mode_allowed()
        self.assertFalse(allowed)
        self.assertIn("verrou propriétaire", why)

    def test_autorise_seulement_si_le_drapeau_est_leve_ET_les_stats_bonnes(self):
        self._excellent_track_record()
        self._write_params(authorized=True)
        allowed, why = self.engine.live_mode_allowed()
        self.assertTrue(allowed, why)

    def test_drapeau_leve_mais_stats_insuffisantes_reste_bloque(self):
        for _ in range(5):
            self.journal._append({"is_final_exit": True, "pnl_usd": -10,
                                  "pnl_pct": -10, "exit_reason": "STOP_LOSS"})
        self._write_params(authorized=True)
        allowed, why = self.engine.live_mode_allowed()
        self.assertFalse(allowed)
        self.assertIn("20 trades", why)
