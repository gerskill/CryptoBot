"""`scripts/voir_bras.py` — inspecter un bras sans jeton, sans ouvrir l'API.

Le jeton protège `/api/params` derrière le tunnel Cloudflare. L'accès local
passe par la lecture directe des fichiers ; ces tests verrouillent ce qui
rendrait ce script dangereux ou trompeur.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from scripts import voir_bras  # noqa: E402


class VoirBrasTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = {k: getattr(settings, k) for k in ("PARAMS_PATH", "STRATEGIES_PATH", "ARMS_CONFIG_DIR")}
        settings.PARAMS_PATH = os.path.join(self.tmp, "params.json")
        settings.STRATEGIES_PATH = os.path.join(self.tmp, "strategies.json")
        settings.ARMS_CONFIG_DIR = os.path.join(self.tmp, "arms")
        os.makedirs(settings.ARMS_CONFIG_DIR)
        self._ecrire(settings.STRATEGIES_PATH, {"arms": [
            {"name": "baseline", "capital_pct": 0.5},
            {"name": "quality", "capital_pct": 0.5},
        ]})
        self._ecrire(settings.PARAMS_PATH, {
            "exit_rules": {"stop_loss_pct": -25, "take_profit_1": 150, "_note": "commentaire"},
            "filters": {"min_holders": 50},
        })
        self._ecrire(os.path.join(settings.ARMS_CONFIG_DIR, "quality.json"), {
            "exit_rules": {"stop_loss_pct": -25, "take_profit_1": 15, "_note": "autre commentaire"},
            "filters": {"min_holders": 300},
        })

    def tearDown(self):
        for key, value in self._saved.items():
            setattr(settings, key, value)

    def _ecrire(self, chemin, contenu):
        with open(chemin, "w", encoding="utf-8") as fh:
            json.dump(contenu, fh)


class TestResolution(VoirBrasTestCase):
    def test_un_chemin_hors_manifeste_nouvre_rien(self):
        """`../../.env` ne doit jamais devenir un chemin de fichier lu."""
        with self.assertRaises(SystemExit):
            voir_bras.resoudre_bras("../../.env")

    def test_un_bras_du_manifeste_est_resolu(self):
        self.assertEqual(voir_bras.resoudre_bras("quality")["name"], "quality")


class TestDifferences(VoirBrasTestCase):
    def test_seules_les_regles_qui_different_du_temoin_sont_listees(self):
        ecarts = voir_bras.differences("quality")
        self.assertEqual(ecarts, {
            "exit_rules.take_profit_1": (150, 15),
            "filters.min_holders": (50, 300),
        })

    def test_les_notes_ne_sont_pas_des_regles(self):
        self.assertNotIn("exit_rules._note", voir_bras.aplatir(voir_bras.parametres("quality")))


class TestResumeAjustement(unittest.TestCase):
    def test_un_ajustement_de_section_naffiche_que_les_cles_modifiees(self):
        avant = {"a": 1, "b": 2, "_note": "x"}
        apres = {"a": 1, "b": 3, "_note": "y"}
        self.assertEqual(voir_bras._resume_valeur(avant, apres), "b 2→3")

    def test_un_ajustement_simple_reste_lisible(self):
        self.assertEqual(voir_bras._resume_valeur(85.0, 70.0), "85.0 → 70.0")


if __name__ == "__main__":
    unittest.main()
