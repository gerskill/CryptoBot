"""Le piège du capital_pct des bras désactivés.

`bootstrap_arms` ne somme QUE les bras actifs, et c'est le bon choix : un bras
désactivé ne prend pas de capital. Mais rien ne signalait que la somme sur tout
le manifeste avait dérivé, si bien qu'un simple `enabled: true` — un geste sans
rapport apparent avec l'allocation — faisait échouer le démarrage sur un
`ManifestError` déroutant.

Constaté le 2026-08-30 : total 1,05, `narrative` (0,05) éteint depuis le
2026-08-03, `sniper_young` (0,10525) ajouté le 2026-08-09 sans rééquilibrage.
Le dépôt ne démarrait que parce que `narrative` restait éteint.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from src.core.arm import ManifestError, bootstrap_arms  # noqa: E402

BASE = {
    "version": "2.0",
    "mode": "PAPER",
    "filters": {"min_liquidity_usd": 25000, "min_holders": 75},
    "exit_rules": {"stop_loss_pct": -10},
    "scan": {"alpha_score_entry_threshold": 70},
    "learning": {},
}


class TestCapitalDesBrasDormants(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = {
            key: getattr(settings, key)
            for key in ("PARAMS_PATH", "TRADES_LOG_PATH", "SHADOW_LOG_PATH",
                        "POSITIONS_PATH", "STRATEGIES_PATH", "ARMS_CONFIG_DIR",
                        "ARMS_DATA_DIR", "FUNNEL_LOG_PATH")
        }
        settings.FUNNEL_LOG_PATH = os.path.join(self.tmp, "funnel_log.jsonl")
        settings.PARAMS_PATH = os.path.join(self.tmp, "params.json")
        settings.TRADES_LOG_PATH = os.path.join(self.tmp, "trades_log.jsonl")
        settings.SHADOW_LOG_PATH = os.path.join(self.tmp, "shadow_log.jsonl")
        settings.POSITIONS_PATH = os.path.join(self.tmp, "open_positions.json")
        settings.STRATEGIES_PATH = os.path.join(self.tmp, "strategies.json")
        settings.ARMS_CONFIG_DIR = os.path.join(self.tmp, "arms")
        settings.ARMS_DATA_DIR = os.path.join(self.tmp, "data_arms")
        with open(settings.PARAMS_PATH, "w", encoding="utf-8") as fh:
            json.dump(BASE, fh)

    def tearDown(self):
        for key, value in self._saved.items():
            setattr(settings, key, value)

    def _manifest(self, arms):
        with open(settings.STRATEGIES_PATH, "w", encoding="utf-8") as fh:
            json.dump({"arms": arms}, fh)

    def _bootstrap(self):
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            arms = bootstrap_arms(with_journals=False)
        return arms, sortie.getvalue()

    def test_avertit_quand_reactiver_un_bras_casserait_le_demarrage(self):
        self._manifest([
            {"name": "baseline", "capital_pct": 1.0},
            {"name": "narrative", "capital_pct": 0.05, "enabled": False},
        ])
        arms, sortie = self._bootstrap()

        self.assertEqual([a.name for a in arms], ["baseline"])
        self.assertIn("narrative", sortie)
        self.assertIn("1.05", sortie)

    def test_ne_dit_rien_quand_le_manifeste_entier_somme_a_un(self):
        """Un bras éteint dont la part a été redistribuée est le cas SAIN.
        Avertir là-dessus apprendrait à ignorer l'avertissement."""
        self._manifest([
            {"name": "baseline", "capital_pct": 1.0},
            {"name": "narrative", "capital_pct": 0.0, "enabled": False},
        ])
        _, sortie = self._bootstrap()
        self.assertNotIn("⚠️", sortie)

    def test_ne_dit_rien_sans_bras_desactive(self):
        self._manifest([{"name": "baseline", "capital_pct": 1.0}])
        _, sortie = self._bootstrap()
        self.assertNotIn("⚠️", sortie)

    def test_lavertissement_ne_remplace_pas_le_refus_sur_les_bras_actifs(self):
        """Anti-régression : du capital inventé entre bras ACTIFS reste une
        erreur fatale, pas un avertissement."""
        self._manifest([
            {"name": "baseline", "capital_pct": 0.8},
            {"name": "runner", "capital_pct": 0.5},
            {"name": "narrative", "capital_pct": 0.3, "enabled": False},
        ])
        with self.assertRaises(ManifestError):
            bootstrap_arms(with_journals=False)

    def test_le_manifeste_reel_du_depot_est_dans_ce_cas(self):
        """Le constat qui a motivé ce garde-fou, verrouillé sur le vrai
        fichier : si quelqu'un rééquilibre les parts, ce test le dira."""
        # Chemin dérivé du dépôt, PAS de `settings` : d'autres modules de test
        # laissent `STRATEGIES_PATH` pointé sur leur répertoire temporaire, et
        # ce test doit lire le vrai manifeste quel que soit l'ordre d'exécution.
        reel = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "strategies.json",
        )
        with open(reel, encoding="utf-8") as fh:
            arms = json.load(fh)["arms"]
        actifs = [a for a in arms if a.get("enabled", True)]
        total_actifs = sum(a.get("capital_pct", 0) for a in actifs)
        total_tous = sum(a.get("capital_pct", 0) for a in arms)

        self.assertAlmostEqual(total_actifs, 1.0, places=6)
        self.assertGreater(total_tous, 1.0)


if __name__ == "__main__":
    unittest.main()
