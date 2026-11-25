"""Rejets RugCheck/Authority comptés en entier, et entonnoir gardé une semaine (2026-11-24).

Mesure du jour : l'entonnoir n'écrivait la raison des rejets qu'une évaluation sur 10, et une seule
génération ne gardait qu'environ 6 h. Impossible de dire combien de tokens RugCheck écarte sur une
semaine. Verrouillé ici : la porte `securite` est écrite à CHAQUE évaluation, les autres rejets
restent échantillonnés, et la rotation garde `FUNNEL_GENERATIONS` générations sans toucher aux
autres journaux (défaut à 1).
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import funnel
from tests.test_funnel import _FakeCandidate, _FakeEvaluation, _FakeResult


class TestPorteSecurite(unittest.TestCase):
    def test_chaque_evaluation_ecrit_les_rejets_de_securite(self):
        with tempfile.TemporaryDirectory() as dossier:
            enregistreur = funnel.FunnelRecorder(os.path.join(dossier, "funnel.jsonl"))
            rejets = [
                _FakeCandidate("a", "A", "risque critique RugCheck : ('Mint Authority still enabled',)"),
                _FakeCandidate("b", "B", "rugcheck 67.0 < 70"),
                _FakeCandidate("c", "C", "liquidité 100$ < 60000$"),
            ]
            for _ in range(funnel.FILTER_SAMPLE_EVERY - 1):  # aucune de ces évaluations n'est échantillonnée
                enregistreur.record_evaluation("sniper", _FakeEvaluation(_FakeResult(rejected=rejets)), threshold=75)
            securite = [r for r in enregistreur.rows if r["gate"] == "securite"]
            filtres = [r for r in enregistreur.rows if r["gate"] == "filtres"]
        self.assertEqual(len(securite), 2 * (funnel.FILTER_SAMPLE_EVERY - 1))
        self.assertEqual({r["token"] for r in securite}, {"a", "b"})
        self.assertEqual(filtres, [])  # le reste reste échantillonné

    def test_la_porte_securite_ne_compte_pas_deux_fois(self):
        rows = [
            {"arm": "sniper", "gate": "filtres_total", "kept": 0, "rejected": 2},
            {"arm": "sniper", "gate": "securite", "passed": False, "token": "a", "reason": "rugcheck 40 < 70"},
        ]
        self.assertEqual(funnel.funnel_by_arm(rows), {"sniper": {"filtres": {"passed": 0, "failed": 2}}})

    def test_la_porte_securite_ne_gonfle_pas_les_motifs(self):
        rows = [{"arm": "sniper", "gate": "securite", "passed": False, "reason": "rugcheck 40 < 70"}]
        self.assertEqual(funnel.top_reasons(rows), [])

    def test_un_rejet_ordinaire_n_est_pas_de_securite(self):
        for raison in ("liquidité 100$ < 60000$", "", None, "insiders 0.5 > 0.3"):
            self.assertFalse(funnel._est_securite(raison))


class TestGenerations(unittest.TestCase):
    def _ecrire(self, chemin, texte):
        with open(chemin, "w", encoding="utf-8") as fh:
            fh.write(texte)

    def test_la_rotation_decale_les_generations_et_borne_le_total(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = os.path.join(dossier, "funnel.jsonl")
            for i in range(5):
                self._ecrire(chemin, f"g{i}\n" * 10)
                self.assertTrue(funnel.rotate(chemin, max_bytes=1, generations=3))
            contenus = []
            for c in funnel.funnel_generations(chemin, generations=3):
                with open(c, encoding="utf-8") as fh:
                    contenus.append(fh.readline().strip())
        self.assertEqual(contenus, ["g2", "g3", "g4"])  # les 3 plus récentes, de la plus ancienne à la plus récente

    def test_par_defaut_une_seule_generation_pour_les_autres_journaux(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = os.path.join(dossier, "autre.jsonl")
            for i in range(3):
                self._ecrire(chemin, f"g{i}\n")
                funnel.rotate(chemin, max_bytes=1)
            self.assertFalse(os.path.exists(funnel.generation_path(chemin, 2)))
            with open(funnel.rotated_path(chemin), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "g2\n")

    def test_flush_garde_une_semaine(self):
        self.assertGreaterEqual(funnel.FUNNEL_GENERATIONS * 6, 7 * 24)  # ~6 h par génération de 8 Mo


if __name__ == "__main__":
    unittest.main()
