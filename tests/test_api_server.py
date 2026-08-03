"""API du dashboard — trois défauts trouvés en vérifiant une courbe d'équité.

CONTEXTE. Le dashboard affichait $5 944,95 dans l'en-tête mais $5 992,06 au
point le plus récent de la courbe d'équité — un écart visible directement sur
une capture d'écran envoyée par l'utilisateur. En creusant :

  1. `/api/shadow` calculait `total` sur des lignes DÉJÀ tronquées par
     `limit` (défaut 100). Affichait « 100 jugés » en plafond structurel,
     quelle que soit la taille réelle du journal (730 lignes mesurées).
  2. `/api/trades?arm=all` faisait la même chose pour reconstruire la courbe :
     `limit=100` sur 210+ trades réels, donc les ~110 plus anciens
     n'existaient pas pour le calcul cumulatif.
  3. `arm=all` mélangeait des bras DÉSACTIVÉS (`narrative`, 4 trades réels sur
     disque) alors qu'ils sont invisibles partout ailleurs dans le dashboard
     — `load_manifest()` ne filtre pas `enabled`, contrairement à
     `bootstrap_arms()`.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402


class ApiTestCase(unittest.TestCase):
    """Redirige tous les chemins vers un dossier jetable, comme ArmsTestCase."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = {
            key: getattr(settings, key)
            for key in ("PARAMS_PATH", "TRADES_LOG_PATH", "SHADOW_LOG_PATH",
                        "STRATEGIES_PATH", "ARMS_CONFIG_DIR", "ARMS_DATA_DIR")
        }
        settings.PARAMS_PATH = os.path.join(self.tmp, "params.json")
        settings.TRADES_LOG_PATH = os.path.join(self.tmp, "trades_log.jsonl")
        settings.SHADOW_LOG_PATH = os.path.join(self.tmp, "shadow_log.jsonl")
        settings.STRATEGIES_PATH = os.path.join(self.tmp, "strategies.json")
        settings.ARMS_CONFIG_DIR = os.path.join(self.tmp, "arms_config")
        settings.ARMS_DATA_DIR = os.path.join(self.tmp, "arms_data")
        with open(settings.PARAMS_PATH, "w", encoding="utf-8") as fh:
            json.dump({"version": "test"}, fh)
        # Importé APRÈS la redirection : le module lit certains chemins au
        # niveau global au premier import, mais les fonctions elles-mêmes
        # relisent `settings.X` à l'appel — sûr de réimporter à chaque test.
        import importlib

        import api.server as server_module
        importlib.reload(server_module)
        self.server = server_module

    def tearDown(self):
        for key, value in self._saved.items():
            setattr(settings, key, value)

    def _manifest(self, arms):
        with open(settings.STRATEGIES_PATH, "w", encoding="utf-8") as fh:
            json.dump({"arms": arms}, fh)

    def _ecrire_jsonl(self, path, rows):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def _trade(self, pid, arm_dir, ts, pnl):
        return {
            "position_id": pid, "token": pid, "pnl_usd": pnl, "pnl_pct": pnl,
            "timestamp_exit": ts, "is_final_exit": True,
        }


class TestShadowTotalNonTronque(ApiTestCase):
    def test_total_compte_tout_le_journal_pas_la_page_affichee(self):
        self._manifest([{"name": "baseline", "capital_pct": 1.0}])
        self._ecrire_jsonl(
            settings.SHADOW_LOG_PATH,
            [{"token": f"t{i}", "would_have_won": i % 5 == 0} for i in range(250)],
        )
        reponse = self.server.get_shadow(limit=100)
        self.assertEqual(reponse["total"], 250)

    def test_shadow_affiche_reste_borne_par_limit(self):
        self._manifest([{"name": "baseline", "capital_pct": 1.0}])
        self._ecrire_jsonl(
            settings.SHADOW_LOG_PATH,
            [{"token": f"t{i}", "would_have_won": False} for i in range(250)],
        )
        reponse = self.server.get_shadow(limit=100)
        self.assertEqual(len(reponse["shadow"]), 100)

    def test_missed_rate_se_calcule_sur_le_total_pas_la_page(self):
        """1 gagnant sur 250 doit rendre 0,4 %, pas un taux gonflé parce que
        le gagnant unique se trouve dans les 100 dernières lignes."""
        self._manifest([{"name": "baseline", "capital_pct": 1.0}])
        rows = [{"token": f"t{i}", "would_have_won": False} for i in range(250)]
        rows[-1]["would_have_won"] = True
        self._ecrire_jsonl(settings.SHADOW_LOG_PATH, rows)
        reponse = self.server.get_shadow(limit=100)
        self.assertEqual(reponse["missed"], 1)
        self.assertAlmostEqual(reponse["missed_rate"], 100 / 250, places=1)


class TestTradesEquitySeries(ApiTestCase):
    def test_equity_series_nest_jamais_tronquee(self):
        """LE BUG QUI CAUSAIT L'ÉCART VISIBLE SUR LA CAPTURE. `trades` peut
        rester borné, `equity_series` doit porter TOUT l'historique pour que
        la courbe reconstruite recolle avec le capital affiché."""
        self._manifest([{"name": "baseline", "capital_pct": 1.0}])
        rows = [self._trade(f"p{i}", None, f"2026-01-01T00:{i:02d}:00", 1.0)
                for i in range(150)]
        self._ecrire_jsonl(settings.TRADES_LOG_PATH, rows)

        reponse = self.server.get_trades(limit=100, arm="all")

        self.assertEqual(len(reponse["trades"]), 100)
        self.assertEqual(len(reponse["equity_series"]), 150)
        self.assertAlmostEqual(sum(reponse["equity_series"]), 150.0)

    def test_un_bras_desactive_est_exclu_de_arm_all(self):
        """`narrative` avait 4 trades réels sur disque et restait invisible
        partout ailleurs dans le dashboard — sauf ici, où il se glissait dans
        la courbe et le tableau."""
        self._manifest([
            {"name": "baseline", "capital_pct": 0.7},
            {"name": "narrative", "capital_pct": 0.3, "enabled": False},
        ])
        self._ecrire_jsonl(settings.TRADES_LOG_PATH, [
            self._trade("b1", None, "2026-01-01T00:00:00", 5.0),
        ])
        narrative_path = settings.arm_paths("narrative")["trades"]
        self._ecrire_jsonl(narrative_path, [
            self._trade("n1", None, "2026-01-01T00:01:00", 999.0),
        ])

        reponse = self.server.get_trades(limit=100, arm="all")

        symboles = {row["position_id"] for row in reponse["trades"]}
        self.assertNotIn("n1", symboles)
        self.assertAlmostEqual(sum(reponse["equity_series"]), 5.0)

    def test_un_bras_desactive_reste_interrogeable_directement(self):
        """Exclu de `arm=all`, mais toujours consultable par son nom : les
        données de `narrative` restent réelles, pas supprimées."""
        self._manifest([
            {"name": "baseline", "capital_pct": 0.7},
            {"name": "narrative", "capital_pct": 0.3, "enabled": False},
        ])
        narrative_path = settings.arm_paths("narrative")["trades"]
        self._ecrire_jsonl(narrative_path, [
            self._trade("n1", None, "2026-01-01T00:01:00", 42.0),
        ])
        reponse = self.server.get_trades(limit=100, arm="narrative")
        self.assertEqual(len(reponse["trades"]), 1)

    def test_equity_series_est_dans_lordre_chronologique(self):
        self._manifest([{"name": "baseline", "capital_pct": 1.0}])
        self._ecrire_jsonl(settings.TRADES_LOG_PATH, [
            self._trade("p1", None, "2026-01-01T00:00:00", -3.0),
            self._trade("p2", None, "2026-01-01T00:01:00", 7.0),
        ])
        reponse = self.server.get_trades(limit=100, arm="all")
        self.assertEqual(reponse["equity_series"], [-3.0, 7.0])


if __name__ == "__main__":
    unittest.main()
