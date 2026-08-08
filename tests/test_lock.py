"""Verrou d'instance unique — au-delà des cas déjà couverts dans test_core.py.

`TestInstanceLock` dans test_core.py verrouille déjà : première acquisition,
refus d'une deuxième instance, reprise d'un verrou périmé (PID mort) ou
corrompu, suppression au `release()`. Ce fichier ajoute les cas restants :
comportement de `release()` avant toute acquisition, non-suppression du
verrou d'un SUCCESSEUR, la sémantique de `_is_alive`, et la création du
dossier parent.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.lock import InstanceLock  # noqa: E402


class TestReleaseSansAcquisition(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "loop.pid")

    def test_release_sans_acquisition_prealable_ne_leve_pas(self):
        lock = InstanceLock(self.path)
        lock.release()  # ne doit pas planter même si acquire() n'a jamais réussi
        self.assertFalse(lock.acquired)

    def test_release_sans_acquisition_ne_touche_pas_a_un_fichier_existant(self):
        # Un fichier de verrou déjà présent (pris par un autre process) ne
        # doit pas disparaître juste parce qu'on appelle release() dessus.
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("424242")
        InstanceLock(self.path).release()
        self.assertTrue(os.path.exists(self.path))


class TestReleaseNeSupprimeQueSonPropreVerrou(unittest.TestCase):
    """Un process qui a repris un verrou périmé ne doit pas effacer celui
    de son successeur — c'est exactement le scénario documenté dans le
    docstring du module."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "loop.pid")

    def test_release_preserve_le_verrou_dun_successeur(self):
        lock = InstanceLock(self.path)
        self.assertIsNone(lock.acquire())
        self.assertTrue(lock.acquired)

        # Un autre process a repris ce fichier entre-temps (verrou périmé
        # puis relance) : le PID sur disque n'est plus le nôtre.
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("999999999")

        lock.release()
        # Le contenu écrit par le successeur doit survivre.
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), "999999999")


class TestIsAlive(unittest.TestCase):
    def test_pid_courant_est_vivant(self):
        self.assertTrue(InstanceLock._is_alive(os.getpid()))

    def test_pid_extreme_improbable_est_mort(self):
        # PID hors de la plage utilisable par le système : ProcessLookupError.
        self.assertFalse(InstanceLock._is_alive(2**30))


class TestAcquireCreeLeDossierParent(unittest.TestCase):
    def test_dossier_parent_absent_est_cree(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "sous_dossier_absent", "loop.pid")
        lock = InstanceLock(path)
        self.assertIsNone(lock.acquire())
        self.assertTrue(os.path.exists(path))


class TestAcquireEcritLePidCourant(unittest.TestCase):
    def test_le_fichier_contient_le_pid_de_ce_process(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "loop.pid")
        InstanceLock(path).acquire()
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(int(fh.read().strip()), os.getpid())


if __name__ == "__main__":
    unittest.main(verbosity=2)
