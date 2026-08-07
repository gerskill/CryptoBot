"""Entonnoir de décision — enregistrement et analyse, hors rotation/tail.

`test_funnel_rotation.py` verrouille déjà `read_funnel(tail=...)` et la
bascule de fichier (`rotate`). Ce fichier couvre le reste du contrat :
`FunnelRecorder` (record/record_evaluation/record_exit/
record_liquidity_alert/flush), et les fonctions d'analyse dérivées
(`funnel_by_arm`, `top_reasons`, `blocking_gate`, `inactivity_snapshot`).
"""

import os
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.funnel import (  # noqa: E402
    FunnelRecorder,
    blocking_gate,
    funnel_by_arm,
    inactivity_snapshot,
    read_funnel,
    top_reasons,
)


@dataclass
class _FakeCandidate:
    token_address: str
    symbol: str
    rejected_reason: str = ""
    alpha_score_absolute: float = 0.0


@dataclass
class _FakeResult:
    candidates: list = field(default_factory=list)
    rejected: list = field(default_factory=list)


@dataclass
class _FakeEvaluation:
    result: _FakeResult


@dataclass
class _FakePosition:
    token_address: str
    symbol: str
    high_water_pct: float = 0.0
    _duration: float = 12.5

    def duration_minutes(self):
        return self._duration


class TestFunnelRecorderRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "funnel_log.jsonl")

    def test_record_accumule_sans_ecrire(self):
        recorder = FunnelRecorder(path=self.path)
        recorder.record("sniper", "addr", "TOK", "seuil_alpha", True)
        self.assertEqual(len(recorder.rows), 1)
        self.assertFalse(os.path.exists(self.path))

    def test_flush_ecrit_puis_vide_le_tampon(self):
        recorder = FunnelRecorder(path=self.path)
        recorder.record("sniper", "addr", "TOK", "seuil_alpha", True)
        written = recorder.flush()
        self.assertEqual(written, 1)
        self.assertEqual(recorder.rows, [])
        self.assertEqual(len(read_funnel(self.path)), 1)

    def test_flush_sur_tampon_vide_necrit_rien(self):
        recorder = FunnelRecorder(path=self.path)
        self.assertEqual(recorder.flush(), 0)
        self.assertFalse(os.path.exists(self.path))

    def test_extra_fusionne_dans_la_ligne(self):
        recorder = FunnelRecorder(path=self.path)
        recorder.record("sniper", "addr", "TOK", "seuil_alpha", True, extra={"alpha": 80})
        self.assertEqual(recorder.rows[0]["alpha"], 80)

    def test_deux_flush_saccumulent_dans_le_meme_fichier(self):
        recorder = FunnelRecorder(path=self.path)
        recorder.record("sniper", "a", "A", "entree", True)
        recorder.flush()
        recorder.record("sniper", "b", "B", "entree", True)
        recorder.flush()
        self.assertEqual(len(read_funnel(self.path)), 2)


class TestRecordEvaluation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "funnel_log.jsonl")
        self.recorder = FunnelRecorder(path=self.path)

    def test_ecrit_le_compteur_filtres_total(self):
        evaluation = _FakeEvaluation(_FakeResult(
            candidates=[_FakeCandidate("a", "A", alpha_score_absolute=80)],
            rejected=[_FakeCandidate("b", "B", rejected_reason="liquidité 100$ < 25000$")],
        ))
        self.recorder.record_evaluation("sniper", evaluation, threshold=75)
        total = next(r for r in self.recorder.rows if r["gate"] == "filtres_total")
        self.assertEqual(total["kept"], 1)
        self.assertEqual(total["rejected"], 1)

    def test_candidats_gardes_franchissent_la_porte_filtres(self):
        evaluation = _FakeEvaluation(_FakeResult(
            candidates=[_FakeCandidate("a", "A", alpha_score_absolute=80)],
        ))
        self.recorder.record_evaluation("sniper", evaluation, threshold=75)
        passes = [r for r in self.recorder.rows if r["gate"] == "filtres" and r["passed"]]
        self.assertEqual(len(passes), 1)

    def test_seuil_alpha_compare_au_score_absolu(self):
        evaluation = _FakeEvaluation(_FakeResult(
            candidates=[_FakeCandidate("a", "A", alpha_score_absolute=60)],
        ))
        self.recorder.record_evaluation("sniper", evaluation, threshold=75)
        seuil = next(r for r in self.recorder.rows if r["gate"] == "seuil_alpha")
        self.assertFalse(seuil["passed"])
        self.assertIn("60", seuil["reason"])

    def test_evaluation_vide_necrit_aucun_compteur(self):
        # Ni gardé ni rejeté : rien à dire pour ce cycle.
        self.recorder.record_evaluation("sniper", _FakeEvaluation(_FakeResult()), threshold=75)
        self.assertEqual(self.recorder.rows, [])

    def test_echantillonnage_des_rejets_de_filtres(self):
        # FILTER_SAMPLE_EVERY = 10 : le détail des rejets n'est écrit qu'un
        # cycle sur dix, mais le compteur `filtres_total`, lui, l'est toujours.
        import src.core.funnel as funnel_module
        recorder = FunnelRecorder(path=self.path)
        evaluation = _FakeEvaluation(_FakeResult(
            rejected=[_FakeCandidate("b", "B", rejected_reason="raison")],
        ))
        for _ in range(funnel_module.FILTER_SAMPLE_EVERY - 1):
            recorder.rows.clear()
            recorder.record_evaluation("sniper", evaluation, threshold=75)
        detail_avant = [r for r in recorder.rows if r["gate"] == "filtres"]
        self.assertEqual(detail_avant, [])

        recorder.rows.clear()
        recorder.record_evaluation("sniper", evaluation, threshold=75)
        detail_apres = [r for r in recorder.rows if r["gate"] == "filtres"]
        self.assertEqual(len(detail_apres), 1)


class TestRecordExit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "funnel_log.jsonl")
        self.recorder = FunnelRecorder(path=self.path)

    def test_calcule_largent_laisse_sur_la_table(self):
        position = _FakePosition("addr", "TOK", high_water_pct=180.0)
        self.recorder.record_exit("sniper", position, price=1.0, reason="TAKE_PROFIT_1",
                                  pnl_pct=5.0)
        row = self.recorder.rows[0]
        self.assertEqual(row["gate"], "sortie")
        self.assertEqual(row["peak_pct"], 180.0)
        self.assertAlmostEqual(row["laisse_sur_table"], 175.0)

    def test_peak_absent_laisse_sur_table_none(self):
        position = _FakePosition("addr", "TOK")
        position.high_water_pct = None
        self.recorder.record_exit("sniper", position, price=1.0, reason="STOP_LOSS",
                                  pnl_pct=-25.0)
        row = self.recorder.rows[0]
        self.assertIsNone(row["peak_pct"])
        self.assertIsNone(row["laisse_sur_table"])


class TestRecordLiquidityAlert(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.recorder = FunnelRecorder(path=os.path.join(self.tmp, "funnel_log.jsonl"))

    def test_chute_suivie_dune_sortie_est_passed(self):
        position = _FakePosition("addr", "TOK")
        self.recorder.record_liquidity_alert("sniper", position, drop_pct=-60.0, acted=True)
        self.assertTrue(self.recorder.rows[0]["passed"])

    def test_chute_non_suivie_dit_sous_le_seuil(self):
        position = _FakePosition("addr", "TOK")
        self.recorder.record_liquidity_alert("sniper", position, drop_pct=-30.0, acted=False)
        row = self.recorder.rows[0]
        self.assertFalse(row["passed"])
        self.assertIn("sous le seuil", row["reason"])


class TestFunnelByArm(unittest.TestCase):
    def test_filtres_total_alimente_le_compteur_filtres(self):
        rows = [
            {"arm": "sniper", "gate": "filtres_total", "kept": 3, "rejected": 7},
            {"arm": "sniper", "gate": "filtres_total", "kept": 2, "rejected": 8},
        ]
        counts = funnel_by_arm(rows)
        self.assertEqual(counts["sniper"]["filtres"]["passed"], 5)
        self.assertEqual(counts["sniper"]["filtres"]["failed"], 15)

    def test_lignes_filtres_detail_ignorees_pour_eviter_le_double_compte(self):
        rows = [
            {"arm": "sniper", "gate": "filtres_total", "kept": 1, "rejected": 1},
            {"arm": "sniper", "gate": "filtres", "passed": False},
        ]
        counts = funnel_by_arm(rows)
        self.assertEqual(counts["sniper"]["filtres"]["failed"], 1)

    def test_autres_portes_comptent_passed_et_failed(self):
        rows = [
            {"arm": "sniper", "gate": "entree", "passed": True},
            {"arm": "sniper", "gate": "entree", "passed": False},
        ]
        counts = funnel_by_arm(rows)
        self.assertEqual(counts["sniper"]["entree"], {"passed": 1, "failed": 1})

    def test_liste_vide_rend_dictionnaire_vide(self):
        self.assertEqual(funnel_by_arm([]), {})


class TestTopReasons(unittest.TestCase):
    def test_normalise_les_valeurs_numeriques(self):
        rows = [
            {"arm": "sniper", "gate": "seuil_alpha", "passed": False,
             "reason": "liquidité 7271$ < 25000$"},
            {"arm": "sniper", "gate": "seuil_alpha", "passed": False,
             "reason": "liquidité 9000$ < 25000$"},
        ]
        top = top_reasons(rows)
        # Le nombre ET son suffixe `$` sont capturés par le même motif : les
        # deux raisons distinctes se réduisent au même gabarit normalisé.
        self.assertEqual(top[0][0], "liquidité N < N")
        self.assertEqual(top[0][1], 2)

    def test_ignore_les_lignes_passees(self):
        rows = [{"arm": "sniper", "gate": "entree", "passed": True, "reason": "ok"}]
        self.assertEqual(top_reasons(rows), [])

    def test_ignore_filtres_total(self):
        rows = [{"arm": "sniper", "gate": "filtres_total", "passed": False, "kept": 0}]
        self.assertEqual(top_reasons(rows), [])

    def test_filtre_par_bras(self):
        rows = [
            {"arm": "sniper", "gate": "entree", "passed": False, "reason": "x"},
            {"arm": "scalp", "gate": "entree", "passed": False, "reason": "y"},
        ]
        top = top_reasons(rows, arm="sniper")
        self.assertEqual(top, [("x", 1)])

    def test_respecte_la_limite(self):
        # Suffixes non numériques : `_normalise` efface les chiffres, des
        # raisons distinctes qui ne différeraient que par un nombre
        # fusionneraient dans le même gabarit et fausseraient le compte.
        lettres = "abcde"
        rows = [
            {"arm": "s", "gate": "entree", "passed": False, "reason": f"raison_{lettre}"}
            for lettre in lettres
        ]
        self.assertEqual(len(top_reasons(rows, limit=2)), 2)


class TestBlockingGate(unittest.TestCase):
    def test_retourne_la_porte_au_plus_gros_volume_de_rejet(self):
        counts = {
            "filtres": {"passed": 100, "failed": 5},
            "confluence": {"passed": 10, "failed": 90},
        }
        gate, failed, passed = blocking_gate(counts)
        self.assertEqual(gate, "confluence")
        self.assertEqual(failed, 90)

    def test_pas_de_rejet_rend_none(self):
        counts = {"filtres": {"passed": 10, "failed": 0}}
        self.assertIsNone(blocking_gate(counts))

    def test_dictionnaire_vide_rend_none(self):
        self.assertIsNone(blocking_gate({}))

    def test_privilegie_le_volume_absolu_pas_le_taux(self):
        # "economie" rejette 100% de 2 candidats, "filtres" rejette 30% de
        # 200 — la correction doit viser le volume, pas le taux.
        counts = {
            "economie": {"passed": 0, "failed": 2},
            "filtres": {"passed": 140, "failed": 60},
        }
        gate, _, _ = blocking_gate(counts)
        self.assertEqual(gate, "filtres")


class TestInactivitySnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "funnel_log.jsonl")

    def _ecrire(self, rows):
        recorder = FunnelRecorder(path=self.path)
        recorder.rows = rows
        recorder.flush()

    def test_bras_jamais_evalue_rend_none_liste_vide(self):
        depuis, motifs = inactivity_snapshot(self.path, "sniper")
        self.assertIsNone(depuis)
        self.assertEqual(motifs, [])

    def test_compte_les_cycles_depuis_la_derniere_entree(self):
        now = time.time()
        rows = [
            {"ts": now - 30, "arm": "sniper", "gate": "filtres_total", "kept": 1,
             "rejected": 0},
            {"ts": now - 20, "arm": "sniper", "gate": "entree", "passed": True},
            {"ts": now - 10, "arm": "sniper", "gate": "filtres_total", "kept": 1,
             "rejected": 0},
            {"ts": now, "arm": "sniper", "gate": "filtres_total", "kept": 0, "rejected": 1},
        ]
        self._ecrire(rows)
        depuis, _ = inactivity_snapshot(self.path, "sniper")
        # Deux cycles filtres_total sont postérieurs à la dernière entrée.
        self.assertEqual(depuis, 2)

    def test_aucune_entree_rend_le_nombre_total_de_cycles_vus(self):
        now = time.time()
        rows = [
            {"ts": now - 20, "arm": "sniper", "gate": "filtres_total", "kept": 0,
             "rejected": 5},
            {"ts": now - 20, "arm": "sniper", "gate": "seuil_alpha", "passed": False,
             "reason": "alpha bas"},
            {"ts": now - 10, "arm": "sniper", "gate": "filtres_total", "kept": 0,
             "rejected": 3},
            {"ts": now - 10, "arm": "sniper", "gate": "seuil_alpha", "passed": False,
             "reason": "alpha bas"},
        ]
        self._ecrire(rows)
        depuis, motifs = inactivity_snapshot(self.path, "sniper")
        self.assertEqual(depuis, 2)
        self.assertTrue(motifs)

    def test_motifs_limites_par_le_parametre(self):
        now = time.time()
        lettres = "abcdefghij"
        rows = [{"ts": now, "arm": "sniper", "gate": "filtres_total", "kept": 0,
                 "rejected": 1}]
        rows += [
            {"ts": now, "arm": "sniper", "gate": "entree", "passed": False,
             "reason": f"motif_{lettre}"}
            for lettre in lettres
        ]
        self._ecrire(rows)
        _, motifs = inactivity_snapshot(self.path, "sniper", motifs=3)
        self.assertEqual(len(motifs), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
