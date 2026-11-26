"""Étapes du cycle chronométrées (2026-11-25).

Mesure du Mac : 91 % des cycles dépassaient le créneau de 45 s (médiane 52 s, pire 491 s) alors que
le scan seul prend 7 à 13 s, et rien ne disait où partait le reste. Verrouillé ici : les durées
s'additionnent par étape (même sur exception), la ligne du journal montre les plus longues et le
temps hors étapes, et la boucle l'écrit à chaque cycle sans jamais rien changer d'autre.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.main as main_module
from src.core.chrono import ETAPES_AFFICHEES, Chrono
from tests.test_cycle_duration import FakeLoop


def horloge(*instants):
    suite = iter(instants)
    return lambda: next(suite)


class TestChrono(unittest.TestCase):
    def test_les_durees_s_additionnent_par_etape(self):
        chrono = Chrono(horloge(0.0, 2.0, 10.0, 13.5))
        with chrono.etape("collecte"):
            pass
        with chrono.etape("collecte"):
            pass
        self.assertEqual(chrono.resume(), {"collecte": 5.5})

    def test_une_exception_est_quand_meme_chronometree(self):
        chrono = Chrono(horloge(0.0, 4.0))
        with self.assertRaises(RuntimeError), chrono.etape("entrées/sniper"):
            raise RuntimeError("api")
        self.assertEqual(chrono.resume(), {"entrées/sniper": 4.0})

    def test_ligne_triee_avec_le_temps_hors_etapes(self):
        chrono = Chrono(horloge(0.0, 1.0, 1.0, 21.0))
        with chrono.etape("collecte"):
            pass
        with chrono.etape("entrées/sniper"):
            pass
        ligne = chrono.ligne(25.0, cycle=7)
        self.assertEqual(ligne, "⏱️ étapes cycle 7 (total 25.0s) : entrées/sniper 20.0s | collecte 1.0s "
                                "| hors étapes 4.0s")

    def test_au_dela_du_plafond_les_plus_courtes_sont_regroupees(self):
        chrono = Chrono()
        chrono.durees = {f"e{i}": float(i) for i in range(ETAPES_AFFICHEES + 2)}
        ligne = chrono.ligne(sum(chrono.durees.values()))
        self.assertIn("2 autres 1.0s", ligne)
        self.assertNotIn("hors étapes", ligne)

    def test_aucune_etape(self):
        self.assertIn("aucune", Chrono().ligne(0.0))


class TestBoucle(unittest.TestCase):
    def setUp(self):
        main_module._running = True
        self.addCleanup(setattr, main_module, "_running", True)

    def test_la_boucle_ecrit_la_ligne_et_garde_les_etapes(self):
        fake = FakeLoop()

        def cycle():
            fake._chrono = Chrono(horloge(0.0, 3.0))
            with fake._chrono.etape("collecte"):
                pass
        fake._cycle = cycle
        temps = iter([100.0, 105.0])
        sortie = io.StringIO()
        with patch("time.monotonic", side_effect=lambda: next(temps)), redirect_stdout(sortie):
            main_module.AlphaLoop.run(fake)
        self.assertEqual(fake._last_cycle_steps, {"collecte": 3.0})
        self.assertIn("⏱️ étapes cycle 1 (total 5.0s) : collecte 3.0s | hors étapes 2.0s", sortie.getvalue())


if __name__ == "__main__":
    unittest.main()


class TestSousEtape(unittest.TestCase):
    """Sous-étapes « ↳ » des entrées (2026-11-26) : 1 cycle sur 10 passait plus de 80 s dans les entrées."""

    def test_sans_cycle_en_cours_rien_n_est_mesure(self):
        bot = object.__new__(main_module.AlphaLoop)
        with bot._sous_etape("↳ technique"):
            pass  # ne lève pas, ne crée rien
        self.assertFalse(hasattr(bot, "_chrono"))

    def test_dans_un_cycle_la_sous_etape_est_comptee(self):
        bot = object.__new__(main_module.AlphaLoop)
        bot._chrono = Chrono(horloge(0.0, 2.5))
        with bot._sous_etape("↳ technique"):
            pass
        self.assertEqual(bot._chrono.resume(), {"↳ technique": 2.5})


class TestVerdictEnCache(unittest.TestCase):
    """« publication état » : 21,6 s de verdicts recalculés à chaque cycle (Mac, 2026-11-26)."""

    def setUp(self):
        self.bot = object.__new__(main_module.AlphaLoop)
        self.appels = []

        def calcul(positions, reference, comparisons):
            self.appels.append(len(positions))
            return {"n": len(positions)}
        self.calcul = calcul
        self.ref = [{"position_id": "r1", "timestamp_exit": "t1"}]

    def test_meme_journal_meme_verdict_sans_recalcul(self):
        pos = [{"position_id": "a1", "timestamp_exit": "t1"}]
        for _ in range(3):
            self.bot._verdict_en_cache("sniper", pos, self.ref, 5, self.calcul)
        self.assertEqual(self.appels, [1])

    def test_une_position_fermee_force_le_recalcul(self):
        pos = [{"position_id": "a1", "timestamp_exit": "t1"}]
        self.bot._verdict_en_cache("sniper", pos, self.ref, 5, self.calcul)
        pos2 = [*pos, {"position_id": "a2", "timestamp_exit": "t2"}]
        self.assertEqual(self.bot._verdict_en_cache("sniper", pos2, self.ref, 5, self.calcul), {"n": 2})
        ref2 = [*self.ref, {"position_id": "r2", "timestamp_exit": "t3"}]
        self.bot._verdict_en_cache("sniper", pos2, ref2, 5, self.calcul)
        self.bot._verdict_en_cache("sniper", pos2, ref2, 6, self.calcul)  # comparaisons changées
        self.assertEqual(self.appels, [1, 2, 2, 2])

    def test_un_cache_par_bras(self):
        pos = [{"position_id": "a1", "timestamp_exit": "t1"}]
        self.bot._verdict_en_cache("sniper", pos, self.ref, 5, self.calcul)
        self.bot._verdict_en_cache("scalp", pos, self.ref, 5, self.calcul)
        self.assertEqual(len(self.appels), 2)
