"""Pont : faire tourner les tests écrits pour pytest avec la suite unittest.

LE TROU. La commande officielle est `python -m unittest discover -s tests`.
Six fichiers (`test_onchain_observer`, `test_insider_obs`,
`test_top_signal_antirug`, `test_chainstack_rpc`, `test_copy_smart_manual`,
`test_rug_pnl_floor`) sont écrits en fonctions nues avec `monkeypatch` et
`parametrize` : unittest n'y trouve aucune classe `TestCase` et affiche
« Ran 0 » pour chacun. 56 tests — dont le respect du ban GMGN, la liste
blanche RPC en lecture seule et « décision identique avec ou sans
l'observateur » — ne protégeaient donc rien depuis leur écriture.

Ce test les découvre AUTOMATIQUEMENT (tout `tests/test_*.py` qui définit un
`def test_` au niveau du module) et les lance avec pytest dans un
sous-processus. Un nouveau fichier écrit dans ce style est couvert sans y
penser. pytest est épinglé dans requirements.txt ; s'il manque, ce test
ÉCHOUE au lieu de se taire.
"""

import os
import re
import subprocess
import sys
import unittest

DOSSIER = os.path.dirname(os.path.abspath(__file__))
RACINE = os.path.dirname(DOSSIER)
FONCTION_NUE = re.compile(r"^def test_", re.MULTILINE)


def fichiers_style_pytest() -> list[str]:
    fichiers = []
    for nom in sorted(os.listdir(DOSSIER)):
        if not (nom.startswith("test_") and nom.endswith(".py")) or nom == os.path.basename(__file__):
            continue
        with open(os.path.join(DOSSIER, nom), encoding="utf-8") as fh:
            if FONCTION_NUE.search(fh.read()):
                fichiers.append(os.path.join("tests", nom))
    return fichiers


class TestPontPytest(unittest.TestCase):
    def test_les_tests_style_pytest_passent_vraiment(self):
        fichiers = fichiers_style_pytest()
        self.assertTrue(fichiers, "plus aucun fichier style pytest : ce pont peut être retiré")
        resultat = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *fichiers],
            cwd=RACINE, capture_output=True, text=True, timeout=600,
        )
        sortie = (resultat.stdout + resultat.stderr)[-3000:]
        self.assertNotIn("No module named pytest", sortie, "pytest absent : pip install -r requirements.txt")
        self.assertEqual(resultat.returncode, 0, f"tests style pytest en échec :\n{sortie}")


if __name__ == "__main__":
    unittest.main()
