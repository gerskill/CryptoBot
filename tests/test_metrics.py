"""Tests de `src/monitoring/metrics.py` et de `GET /api/metrics`.

Même stratégie de test que `test_api_server.py` : tous les chemins de
`settings` sont redirigés vers un dossier jetable, aucun fichier réel n'est
touché. `src.monitoring.metrics` est réimporté après la redirection car ses
fonctions relisent `settings.X` à l'appel (pas de chemin figé à l'import).
"""

import importlib
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402


class MetricsTestCase(unittest.TestCase):
    """Redirige tous les chemins vers un dossier jetable, comme ApiTestCase."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = {
            key: getattr(settings, key)
            for key in (
                "PARAMS_PATH", "TRADES_LOG_PATH", "SHADOW_LOG_PATH", "STATE_PATH",
                "STRATEGIES_PATH", "ARMS_CONFIG_DIR", "ARMS_DATA_DIR",
            )
        }
        settings.PARAMS_PATH = os.path.join(self.tmp, "params.json")
        settings.TRADES_LOG_PATH = os.path.join(self.tmp, "trades_log.jsonl")
        settings.SHADOW_LOG_PATH = os.path.join(self.tmp, "shadow_log.jsonl")
        settings.STATE_PATH = os.path.join(self.tmp, "state.json")
        settings.STRATEGIES_PATH = os.path.join(self.tmp, "strategies.json")
        settings.ARMS_CONFIG_DIR = os.path.join(self.tmp, "arms_config")
        settings.ARMS_DATA_DIR = os.path.join(self.tmp, "arms_data")
        with open(settings.PARAMS_PATH, "w", encoding="utf-8") as fh:
            json.dump({"version": "test"}, fh)

        import src.monitoring.metrics as metrics_module

        importlib.reload(metrics_module)
        self.metrics = metrics_module

    def tearDown(self):
        for key, value in self._saved.items():
            setattr(settings, key, value)

    def _manifest(self, arms):
        with open(settings.STRATEGIES_PATH, "w", encoding="utf-8") as fh:
            json.dump({"arms": arms}, fh)

    def _write_state(self, **overrides):
        state = {
            "updated_at": time.time(),
            "cycle": 42,
            "cycle_duration_sec": 12.3,
            "scan": {"scanned": 87, "cached_skipped": 5, "duration_sec": 3.4},
            "budgets": {
                "twitter": {
                    "monthly_limit": 100,
                    "used": 42,
                    "remaining": 58,
                    "pct_used": 42.0,
                }
            },
            "api_status": {"twitter": True, "birdeye": False},
            "capabilities": [
                {"capability": "bougies", "blind": False},
                {"capability": "social", "blind": True},
            ],
        }
        state.update(overrides)
        with open(settings.STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh)

    def _ecrire_jsonl(self, path, rows):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def _trade(self, pid, ts, pnl):
        return {
            "position_id": pid, "token": pid, "pnl_usd": pnl, "pnl_pct": pnl,
            "timestamp_exit": ts, "is_final_exit": True,
        }


class TestArmTradeMetrics(MetricsTestCase):
    def test_compte_les_trades_et_le_pnl_par_bras(self):
        self._manifest([{"name": "baseline", "capital_pct": 1.0}])
        self._ecrire_jsonl(settings.TRADES_LOG_PATH, [
            self._trade("p1", "2026-01-01T00:00:00", 5.0),
            self._trade("p2", "2026-01-01T00:01:00", -2.0),
        ])

        texte = self.metrics.generate_metrics_text()

        self.assertIn('cryptobbot_arm_trades_total{arm="baseline"} 2.0', texte)
        self.assertIn('cryptobbot_arm_pnl_usd{arm="baseline"} 3.0', texte)
        self.assertIn('cryptobbot_arm_win_rate_pct{arm="baseline"} 50.0', texte)

    def test_bras_desactive_absent_des_metriques(self):
        """Même filtre que `GET /api/trades?arm=all` : un bras désactivé
        (`enabled: False`) est invisible, cohérent avec le reste du dashboard."""
        self._manifest([
            {"name": "baseline", "capital_pct": 0.7},
            {"name": "narrative", "capital_pct": 0.3, "enabled": False},
        ])
        narrative_path = settings.arm_paths("narrative")["trades"]
        self._ecrire_jsonl(narrative_path, [self._trade("n1", "2026-01-01T00:00:00", 999.0)])

        texte = self.metrics.generate_metrics_text()

        self.assertNotIn('arm="narrative"', texte)

    def test_bras_sans_trade_rend_zero_sans_win_rate(self):
        """Zéro trade doit apparaître comme 0, jamais comme une absence de
        donnée — mais un win rate sur zéro trade n'a pas de sens et doit
        rester absent plutôt que d'inventer un 0 % trompeur."""
        self._manifest([{"name": "baseline", "capital_pct": 1.0}])

        texte = self.metrics.generate_metrics_text()

        self.assertIn('cryptobbot_arm_trades_total{arm="baseline"} 0.0', texte)
        self.assertNotIn("cryptobbot_arm_win_rate_pct", texte)


class TestCycleMetrics(MetricsTestCase):
    def test_bot_online_et_cycle_depuis_state_json(self):
        self._manifest([{"name": "baseline", "capital_pct": 1.0}])
        self._write_state()

        texte = self.metrics.generate_metrics_text()

        self.assertIn("cryptobbot_bot_online 1.0", texte)
        self.assertIn("cryptobbot_cycle_total 42.0", texte)
        self.assertIn("cryptobbot_cycle_duration_seconds 12.3", texte)
        self.assertIn("cryptobbot_scan_tokens_scanned 87.0", texte)

    def test_bot_offline_sans_state_json(self):
        self._manifest([{"name": "baseline", "capital_pct": 1.0}])

        texte = self.metrics.generate_metrics_text()

        self.assertIn("cryptobbot_bot_online 0.0", texte)
        self.assertNotIn("cryptobbot_cycle_total", texte)

    def test_state_perime_rend_bot_online_a_zero(self):
        self._manifest([{"name": "baseline", "capital_pct": 1.0}])
        self._write_state(updated_at=time.time() - 999)

        texte = self.metrics.generate_metrics_text()

        self.assertIn("cryptobbot_bot_online 0.0", texte)


class TestQuotaAndAvailabilityMetrics(MetricsTestCase):
    def test_quotas_depuis_state_budgets(self):
        self._manifest([{"name": "baseline", "capital_pct": 1.0}])
        self._write_state()

        texte = self.metrics.generate_metrics_text()

        self.assertIn('cryptobbot_api_quota_used_pct{service="twitter"} 42.0', texte)
        self.assertIn('cryptobbot_api_quota_remaining{service="twitter"} 58.0', texte)
        self.assertIn('cryptobbot_api_quota_monthly_limit{service="twitter"} 100.0', texte)

    def test_service_up_et_capacite_aveugle(self):
        self._manifest([{"name": "baseline", "capital_pct": 1.0}])
        self._write_state()

        texte = self.metrics.generate_metrics_text()

        self.assertIn('cryptobbot_api_service_up{service="twitter"} 1.0', texte)
        self.assertIn('cryptobbot_api_service_up{service="birdeye"} 0.0', texte)
        self.assertIn('cryptobbot_capability_blind{capability="bougies"} 0.0', texte)
        self.assertIn('cryptobbot_capability_blind{capability="social"} 1.0', texte)


class TestPrometheusFormat(MetricsTestCase):
    def test_chaque_metrique_a_son_help_et_son_type(self):
        self._manifest([{"name": "baseline", "capital_pct": 1.0}])
        self._write_state()
        self._ecrire_jsonl(settings.TRADES_LOG_PATH, [
            self._trade("p1", "2026-01-01T00:00:00", 5.0),
        ])

        lignes = self.metrics.generate_metrics_text().splitlines()

        self.assertIn("# HELP cryptobbot_arm_trades_total", "\n".join(lignes))
        self.assertIn("# TYPE cryptobbot_arm_trades_total gauge", "\n".join(lignes))
        # Aucune ligne de donnée ne doit précéder son couple HELP/TYPE.
        vus = set()
        for ligne in lignes:
            if ligne.startswith("# TYPE "):
                vus.add(ligne.split()[2])
            elif ligne and not ligne.startswith("#"):
                nom = ligne.split("{")[0].split(" ")[0]
                self.assertIn(nom, vus, f"{nom} apparaît sans HELP/TYPE au préalable")


class TestMetricsEndpoint(MetricsTestCase):
    """Vérifie que `GET /api/metrics` est bien câblé, sans toucher `/api/health`."""

    def setUp(self):
        super().setUp()
        import api.server as server_module

        importlib.reload(server_module)
        self.server = server_module

    def test_endpoint_metrics_renvoie_du_texte_prometheus(self):
        self._manifest([{"name": "baseline", "capital_pct": 1.0}])
        self._write_state()

        reponse = self.server.get_metrics()

        self.assertEqual(reponse.media_type, "text/plain; version=0.0.4; charset=utf-8")
        corps = reponse.body.decode("utf-8")
        self.assertIn("cryptobbot_bot_online 1.0", corps)

    def test_endpoint_health_toujours_present_et_inchange(self):
        """Garde-fou anti-régression : `/api/health` doit continuer d'exister
        avec sa forme exacte — c'est la route que ce travail n'a pas le droit
        de toucher."""
        self._manifest([{"name": "baseline", "capital_pct": 1.0}])
        self._write_state()

        reponse = self.server.health()

        self.assertEqual(set(reponse.keys()), {"api", "bot_online", "mode", "cycle"})
        self.assertEqual(reponse["api"], "ok")
        self.assertEqual(reponse["cycle"], 42)


if __name__ == "__main__":
    unittest.main()
