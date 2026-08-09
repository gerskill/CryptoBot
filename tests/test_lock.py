"""Verrou d'instance unique — au-delà des cas déjà couverts dans test_core.py.

`TestInstanceLock` dans test_core.py verrouille déjà : première acquisition,
refus d'une deuxième instance, reprise d'un verrou périmé (PID mort) ou
corrompu, suppression au `release()`. Ce fichier ajoute les cas restants :
comportement de `release()` avant toute acquisition, exclusion mutuelle
RÉELLE entre deux instances (le verrou est désormais un `flock` noyau,
atomique — voir le docstring de `src/core/lock.py`), et la création du
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


class TestExclusionMutuelleReelle(unittest.TestCase):
    """Le bug corrigé : deux instances ne doivent JAMAIS acquérir en même
    temps. Avec l'ancien design (lire le PID puis écrire), une fenêtre de
    course existait entre les deux ; `flock` la ferme au niveau noyau."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "loop.pid")

    def test_deuxieme_instance_refusee_tant_que_la_premiere_tient_le_verrou(self):
        first = InstanceLock(self.path)
        second = InstanceLock(self.path)

        self.assertIsNone(first.acquire())
        self.assertTrue(first.acquired)

        holder = second.acquire()
        self.assertIsNotNone(holder)  # occupé : jamais None pour la deuxième
        self.assertFalse(second.acquired)

    def test_liberation_permet_a_la_suivante_dacquerir(self):
        first = InstanceLock(self.path)
        second = InstanceLock(self.path)

        self.assertIsNone(first.acquire())
        first.release()

        self.assertIsNone(second.acquire())
        self.assertTrue(second.acquired)

    def test_release_supprime_le_fichier_de_verrou(self):
        lock = InstanceLock(self.path)
        self.assertIsNone(lock.acquire())
        lock.release()
        self.assertFalse(os.path.exists(self.path))


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
