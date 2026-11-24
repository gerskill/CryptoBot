"""Mesure RugCheck (2026-11-24) : 442 entrées sur 446 à score 99, le seuil ne filtrait presque rien.

Verrouillé ici : la lecture des raisons de rejet écrites par le pipeline, le comptage shadow sans
doublon, la répartition des scores, et l'absence de verdict sous 30 positions par groupe.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import analyse_rugcheck as ar


class TestRaisons(unittest.TestCase):
    def test_raisons_du_pipeline(self):
        self.assertEqual(ar.est_rejet_rugcheck("risque critique RugCheck : ('Mint Authority still enabled',)"),
                         "critique")
        self.assertEqual(ar.est_rejet_rugcheck("rugcheck 40 < 70"), "score")
        for autre in ("liquidité 14984$ < 60000$", None, "", "insiders 0.5 > 0.3"):
            self.assertIsNone(ar.est_rejet_rugcheck(autre))


class TestRejets(unittest.TestCase):
    """Le shadow saute exprès les rejets RugCheck (verdict du juge du 2026-11-24) : on lit l'entonnoir."""

    def test_tokens_distincts_pas_les_lignes_par_bras(self):
        lignes = [
            {"arm": "sniper", "token": "a", "reason": "risque critique RugCheck : ('Mint',)"},
            {"arm": "scalp", "token": "a", "reason": "risque critique RugCheck : ('Mint',)"},
            {"arm": "sniper", "token": "b", "reason": "rugcheck 40 < 70"},
            {"arm": "sniper", "token": "c", "reason": "liquidité 100$ < 60000$"},
        ]
        r = ar.resume_rejets(lignes)
        self.assertEqual((r["tokens_rejetes"], r["rugcheck"], r["critique"], r["score"]), (3, 2, 1, 1))

    def test_porte_securite_complete(self):
        lignes = [
            {"gate": "securite", "token": "a", "reason": "rugcheck 40 < 70"},
            {"gate": "securite", "token": "z", "reason": "Freeze authority active"},
        ]
        r = ar.resume_rejets(lignes)
        self.assertTrue(r["complet"])
        self.assertEqual((r["rugcheck"], r["authority"]), (1, 1))
        self.assertFalse(ar.resume_rejets([])["complet"])

    def test_filtre_par_bras(self):
        lignes = [{"arm": "scalp", "token": "a", "reason": "rugcheck 40 < 70"}]
        self.assertEqual(ar.resume_rejets(lignes, ["sniper"])["rugcheck"], 0)

    def test_lecture_entonnoir_garde_seulement_les_refus_filtres_de_la_fenetre(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = os.path.join(dossier, "funnel.jsonl")
            base = {"ts": 2000.0, "arm": "sniper", "token": "a", "gate": "filtres", "passed": False,
                    "reason": "rugcheck 40 < 70"}
            with open(chemin, "w", encoding="utf-8") as fh:
                fh.writelines(json.dumps(ligne) + "\n" for ligne in (base, {**base, "ts": 10.0}, {**base, "passed": True},
                              {**base, "gate": "filtres_total"}))
                fh.write("pas du json\n")
            lues = ar.lire_entonnoir([chemin, chemin, os.path.join(dossier, "absent")], depuis=1000.0)
        self.assertEqual(len(lues), 1)

    def test_rapport_dit_echantillon_et_trop_peu(self):
        texte = "\n".join(ar.rapport_rejets(ar.resume_rejets([]), 7))
        self.assertIn("ÉCHANTILLON", texte)
        self.assertIn("trop peu pour juger", texte)
        self.assertIn("aucune ligne", texte)

    def test_periode_reellement_couverte(self):
        lignes = [{"ts": 0.0, "token": "a", "reason": "x"}, {"ts": 7200.0, "token": "b", "reason": "x"}]
        texte = "\n".join(ar.rapport_rejets(ar.resume_rejets(lignes), 7))
        self.assertIn("2.0 h réellement couvertes", texte)


class TestEntrees(unittest.TestCase):
    def test_repartition_des_scores(self):
        positions = [{"rugcheck_score": 99}, {"rugcheck_score": 80}, {"rugcheck_score": 40}, {}]
        self.assertEqual(ar.repartition_scores(positions),
                         {"99": 1, "seuil-98": 1, "sous le seuil": 1, "absent": 1})

    def test_fenetre(self):
        maintenant = datetime.now(timezone.utc)
        positions = [{"timestamp_entry": (maintenant - timedelta(days=d)).isoformat()} for d in (1, 10)]
        positions.append({"timestamp_entry": "illisible"})
        self.assertEqual(len(ar.dans_la_fenetre(positions, maintenant - timedelta(days=7))), 1)

    def test_pas_de_verdict_sous_trente_positions(self):
        groupe = [{"pnl_pct": 5.0, "timestamp_entry": "2026-11-20T10:00:00"}] * 10
        self.assertEqual(ar.comparer_groupes(groupe, groupe)["verdict"], "échantillon trop court")

    def test_rapport_un_seul_groupe(self):
        positions = [{"pnl_pct": 1.0, "rugcheck_score": 99, "timestamp_entry": "2026-11-20T10:00:00"}]
        texte = "\n".join(ar.rapport_bras("sniper", positions))
        self.assertIn("un seul groupe", texte)


if __name__ == "__main__":
    unittest.main()
