"""La ligne 🧠 « recherche de sorties » nomme son bras.

Constaté sur le Mac le 2026-11-21 : « 🧠 recherche de sorties — rien à ajuster : … » sans nom
de bras, alors que 19 bras apprennent. Impossible de savoir lequel avait été jugé.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.learning import LearningEngine  # noqa: E402


class TestLibelle(unittest.TestCase):
    def _ligne(self, moteur):
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            moteur._fin_recherche("rien à ajuster", "détail")
        return sortie.getvalue()

    def test_le_nom_du_bras_apparait(self):
        moteur = object.__new__(LearningEngine)
        moteur.label = "[baseline]"
        self.assertIn("🧠[baseline] recherche de sorties — rien à ajuster", self._ligne(moteur))

    def test_sans_libelle_la_ligne_reste_lisible(self):
        moteur = object.__new__(LearningEngine)
        self.assertIn("🧠 recherche de sorties", self._ligne(moteur))

    def test_le_constructeur_accepte_le_libelle(self):
        import inspect
        self.assertIn("label", inspect.signature(LearningEngine.__init__).parameters)


if __name__ == "__main__":
    unittest.main()
