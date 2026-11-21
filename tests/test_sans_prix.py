"""Alerte « position ouverte sans prix » (src/core/sans_prix.py + `_monitor_arm`).

Bug d'origine : OGTA est resté ouvert 31 h 47 sans prix, le stop à -25 % n'a jamais
joué et personne n'a été prévenu. Hors réseau, horloge simulée.
"""

import os
import sys
import time
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.sans_prix import SANS_PRIX_ALERTE_MINUTES, SuiviSansPrix
from tests import (
    test_positions_zombies as zombies,
)


class Horloge:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t

    def avance(self, minutes):
        self.t += minutes * 60


class TestSuiviSansPrix(unittest.TestCase):
    def setUp(self):
        self.horloge = Horloge()
        self.suivi = SuiviSansPrix(horloge=self.horloge)

    def test_le_seuil_par_defaut_est_deux_heures(self):
        self.assertEqual(SANS_PRIX_ALERTE_MINUTES, 120)

    def test_pas_d_alerte_avant_le_seuil(self):
        self.assertIsNone(self.suivi.prix_absent("p1"))
        self.horloge.avance(SANS_PRIX_ALERTE_MINUTES - 1)
        self.assertIsNone(self.suivi.prix_absent("p1"))

    def test_une_seule_alerte_au_franchissement_du_seuil(self):
        self.suivi.prix_absent("p1")
        self.horloge.avance(SANS_PRIX_ALERTE_MINUTES + 1)
        minutes = self.suivi.prix_absent("p1")
        self.assertAlmostEqual(minutes, SANS_PRIX_ALERTE_MINUTES + 1, delta=0.1)
        self.horloge.avance(30)
        self.assertIsNone(self.suivi.prix_absent("p1"))  # déjà prévenu

    def test_le_prix_revenu_remet_le_compteur_a_zero_et_reprevient_ensuite(self):
        self.suivi.prix_absent("p1")
        self.horloge.avance(SANS_PRIX_ALERTE_MINUTES + 1)
        self.assertIsNotNone(self.suivi.prix_absent("p1"))
        self.suivi.prix_present("p1")
        self.assertIsNone(self.suivi.minutes("p1"))
        self.suivi.prix_absent("p1")
        self.horloge.avance(SANS_PRIX_ALERTE_MINUTES + 1)
        self.assertIsNotNone(self.suivi.prix_absent("p1"))

    def test_minutes_est_none_sans_coupure(self):
        self.assertIsNone(self.suivi.minutes("inconnue"))
        self.suivi.prix_absent("p1")
        self.horloge.avance(10)
        self.assertAlmostEqual(self.suivi.minutes("p1"), 10, delta=0.01)

    def test_deux_positions_sont_suivies_separement(self):
        self.suivi.prix_absent("a")
        self.horloge.avance(100)
        self.suivi.prix_absent("b")
        self.horloge.avance(25)
        self.assertIsNotNone(self.suivi.prix_absent("a"))  # 125 min
        self.assertIsNone(self.suivi.prix_absent("b"))  # 25 min

    def test_purge_oublie_les_positions_fermees(self):
        self.suivi.prix_absent("ouverte")
        self.suivi.prix_absent("fermee")
        self.suivi.purge(["ouverte"])
        self.assertIsNotNone(self.suivi.minutes("ouverte"))
        self.assertIsNone(self.suivi.minutes("fermee"))


class FauxTelegramEnregistreur:
    def __init__(self):
        self.messages = []

    def send(self, texte, *args, **kwargs):
        self.messages.append(texte)


class TestAlerteDansLeMoniteur(unittest.TestCase):
    """Même banc que les zombies (emprunté, pas hérité : sinon ses tests seraient rejoués ici) ;
    on pilote l'horloge du suivi pour passer 2 h sans prix."""

    def setUp(self):
        banc = zombies.TestPositionsSansPrix("test_position_inconnue_ne_leve_pas")
        banc.setUp()
        self.loop, self.arm, self.marche = banc.loop, banc.arm, banc.marche
        self.portfolio, self.journal, self.position = banc.portfolio, banc.journal, banc.position
        self.horloge = Horloge()
        self.loop._sans_prix = SuiviSansPrix(horloge=self.horloge)
        self.telegram = FauxTelegramEnregistreur()
        self.loop.telegram = self.telegram
        # Âge sous le délai zombie (240 + 30 min) : la position reste ouverte, seule l'alerte joue.
        position = self.portfolio.positions[self.position.id]
        self.portfolio.positions[self.position.id] = replace(position, entry_time=time.time() - 60 * 60)

    def _tick(self):
        self.loop._monitor_arm(self.arm, verbose=False)

    def test_sans_prix_deux_heures_previent_une_fois_par_telegram(self):
        self._tick()
        self.assertEqual(self.telegram.messages, [])
        self.horloge.avance(SANS_PRIX_ALERTE_MINUTES + 1)
        self._tick()
        self.assertEqual(len(self.telegram.messages), 1)
        message = self.telegram.messages[0]
        self.assertIn("ZOMB", message)
        self.assertIn("sans prix", message.lower())
        self.horloge.avance(30)
        self._tick()
        self.assertEqual(len(self.telegram.messages), 1)  # pas de répétition à chaque tick

    def test_l_alerte_ne_ferme_rien(self):
        self._tick()
        self.horloge.avance(SANS_PRIX_ALERTE_MINUTES + 1)
        self._tick()
        self.assertIn(self.position.id, self.portfolio.positions)
        self.assertEqual(self.journal.read_all(), [])

    def test_le_prix_revenu_arrete_le_compteur(self):
        self._tick()
        self.horloge.avance(SANS_PRIX_ALERTE_MINUTES + 1)
        self.marche.price_value = 1.0
        self._tick()
        self.assertIsNone(self.loop._sans_prix.minutes(self.position.id))
        self.assertEqual(self.telegram.messages, [])

    def test_le_suivi_existe_meme_sans_init_complet(self):
        # Le banc de test construit la boucle sans __init__ : le suivi doit se créer seul.
        del self.loop._sans_prix
        self._tick()
        self.assertIsNotNone(self.loop._suivi_sans_prix().minutes(self.position.id))


if __name__ == "__main__":
    unittest.main()
