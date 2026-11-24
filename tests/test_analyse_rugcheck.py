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
    def test_comptes_et_trop_peu_pour_juger(self):
        lignes = ([{"reason": "rugcheck 40 < 70", "would_have_won": True}] * 3
                  + [{"reason": "liquidité", "would_have_won": False}] * 12)
        r = ar.resume_rejets(lignes)
        self.assertEqual((r["total"], r["rugcheck"], r["score"], r["critique"]), (15, 3, 3, 0))
        self.assertIsNone(r["gagnants_rugcheck_pct"])  # 3 < MIN_REJETS
        self.assertEqual(r["gagnants_autres_pct"], 0.0)

    def test_lecture_shadow_fenetre_et_doublons(self):
        with tempfile.TemporaryDirectory() as dossier:
            chemin = os.path.join(dossier, "shadow.jsonl")
            ligne = {"timestamp": 2000.0, "token_address": "m", "reason": "rugcheck 40 < 70"}
            with open(chemin, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(ligne) + "\n" + json.dumps(ligne) + "\n")
                fh.write(json.dumps({**ligne, "timestamp": 10.0}) + "\npas du json\n")
            lues = ar.lire_shadow([chemin, chemin, os.path.join(dossier, "absent")], depuis=1000.0)
        self.assertEqual(len(lues), 1)


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
