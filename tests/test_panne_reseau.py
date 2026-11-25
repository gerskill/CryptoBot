"""Coupure Internet générale ou token mort ? (B1 bis, préparé le 2026-11-22). Hors réseau."""

import os
import sys
import time
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.panne_reseau import DetecteurPanne  # noqa: E402
from src.main import ZOMBIE_GRACE_MINUTES  # noqa: E402
from tests import test_positions_zombies as zombies  # noqa: E402


class Horloge:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class SondeComptee:
    def __init__(self, ok):
        self.ok = ok
        self.appels = 0

    def __call__(self):
        self.appels += 1
        if isinstance(self.ok, Exception):
            raise self.ok
        return self.ok


class TestDetecteur(unittest.TestCase):
    def setUp(self):
        self.h = Horloge()

    def test_un_prix_suffit_pour_dire_reseau_ok(self):
        sonde = SondeComptee(False)
        d = DetecteurPanne(sonde=sonde, horloge=self.h)
        self.assertIsNone(d.observer_tick(prix_ok=1, prix_manquants=5))
        self.assertFalse(d.en_panne)
        self.assertEqual(sonde.appels, 0)  # aucune sonde quand des prix arrivent

    def test_aucun_prix_et_sonde_en_echec_panne_generale(self):
        d = DetecteurPanne(sonde=SondeComptee(False), horloge=self.h)
        self.assertEqual(d.observer_tick(0, 3), "debut")
        self.assertTrue(d.en_panne)
        self.h.t += 600
        self.assertAlmostEqual(d.duree_minutes(), 10.0)

    def test_aucun_prix_mais_sonde_ok_tokens_morts(self):
        d = DetecteurPanne(sonde=SondeComptee(True), horloge=self.h)
        self.assertIsNone(d.observer_tick(0, 1))
        self.assertFalse(d.en_panne)

    def test_sonde_qui_plante_compte_comme_panne(self):
        d = DetecteurPanne(sonde=SondeComptee(OSError("dns")), horloge=self.h)
        self.assertEqual(d.observer_tick(0, 2), "debut")

    def test_sonde_au_plus_une_fois_par_minute(self):
        sonde = SondeComptee(False)
        d = DetecteurPanne(sonde=sonde, horloge=self.h)
        for _ in range(12):  # 12 ticks de 5 s
            d.observer_tick(0, 2)
            self.h.t += 5
        self.assertEqual(sonde.appels, 1)
        self.h.t += 60
        d.observer_tick(0, 2)
        self.assertEqual(sonde.appels, 2)

    def test_retour_du_reseau(self):
        d = DetecteurPanne(sonde=SondeComptee(False), horloge=self.h)
        d.observer_tick(0, 2)
        self.assertEqual(d.observer_tick(2, 0), "fin")
        self.assertFalse(d.en_panne)

    def test_sans_sonde_jamais_de_panne(self):
        d = DetecteurPanne(sonde=None, horloge=self.h)
        self.assertIsNone(d.observer_tick(0, 9))
        self.assertFalse(d.en_panne)


def _fabrique(sonde_ok):
    banc = zombies.TestPositionsSansPrix("setUp")
    banc.setUp()
    banc.loop._panne = DetecteurPanne(sonde=lambda: sonde_ok)
    banc.loop._sans_prix = None
    return banc


class TestBoucle(unittest.TestCase):
    def test_coupure_generale_aucune_fermeture_meme_bien_apres_le_time_stop(self):
        banc = _fabrique(sonde_ok=False)
        banc._vieillir(30 * 60)  # 30 h, comme MANDY/OGTA/mihao
        banc.loop._observer_panne(0, 1)
        self.assertTrue(banc.loop._panne.en_panne)
        banc.loop._monitor_arm(banc.arm, verbose=False)
        self.assertIn(banc.position.id, banc.portfolio.positions)
        self.assertEqual(banc.journal.read_all(), [])

    def test_token_mort_isole_toujours_ferme_a_moins_100(self):
        banc = _fabrique(sonde_ok=True)  # le réseau et le SOL répondent
        banc._vieillir(banc.position.max_hold_time_minutes + ZOMBIE_GRACE_MINUTES + 1)
        banc.loop._observer_panne(0, 1)
        self.assertFalse(banc.loop._panne.en_panne)
        banc.loop._monitor_arm(banc.arm, verbose=False)
        self.assertNotIn(banc.position.id, banc.portfolio.positions)
        self.assertEqual(banc.journal.read_all()[0]["exit_reason"], zombies.RAISON_ZOMBIE)

    def test_token_mort_parmi_d_autres_qui_ont_un_prix(self):
        banc = _fabrique(sonde_ok=False)  # sonde jamais appelée : d'autres prix arrivent
        banc._vieillir(banc.position.max_hold_time_minutes + ZOMBIE_GRACE_MINUTES + 1)
        banc.loop._observer_panne(3, 1)
        banc.loop._monitor_arm(banc.arm, verbose=False)
        self.assertNotIn(banc.position.id, banc.portfolio.positions)

    def test_retour_du_reseau_sorties_normales_au_premier_vrai_prix(self):
        banc = _fabrique(sonde_ok=False)
        envoyes = []
        banc.loop.telegram.send = lambda texte, **k: envoyes.append(texte)
        banc._vieillir(30 * 60)
        banc.loop._observer_panne(0, 1)
        banc.loop._monitor_arm(banc.arm, verbose=False)
        self.assertIn(banc.position.id, banc.portfolio.positions)
        # Le réseau revient : le prix est là, -10 % -> time stop normal, pas -100 %.
        banc.marche.price_value = 0.9
        banc.loop._observer_panne(1, 0)
        self.assertFalse(banc.loop._panne.en_panne)
        self.assertTrue(any("Réseau revenu" in t for t in envoyes))
        banc.loop._monitor_arm(banc.arm, verbose=False)
        ligne = banc.journal.read_all()[0]
        self.assertTrue(ligne["exit_reason"].startswith("TIME_STOP"))
        self.assertGreater(ligne["pnl_pct"], -20)

    def test_retour_du_reseau_stop_loss_normal_si_effondre(self):
        banc = _fabrique(sonde_ok=False)
        pos = banc.portfolio.positions[banc.position.id]
        banc.portfolio.positions[banc.position.id] = replace(pos, entry_time=time.time() - 60)
        banc.loop._observer_panne(0, 1)
        banc.loop._monitor_arm(banc.arm, verbose=False)
        banc.marche.price_value = 0.5  # -50 % au retour
        banc.loop._observer_panne(1, 0)
        banc.loop._monitor_arm(banc.arm, verbose=False)
        self.assertTrue(banc.journal.read_all()[0]["exit_reason"].startswith("STOP_LOSS"))

    def test_banc_sans_jupiter_comportement_b1_inchange(self):
        banc = zombies.TestPositionsSansPrix("setUp")
        banc.setUp()
        self.assertFalse(banc.loop._detecteur_panne().en_panne)


if __name__ == "__main__":
    unittest.main()
