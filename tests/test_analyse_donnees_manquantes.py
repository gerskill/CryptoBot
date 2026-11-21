"""Mesure « entrées sans donnée » (scripts/analyse_donnees_manquantes.py), ADR 022.

Ce qui est verrouillé : aucun verdict sous 30 positions par groupe, comparaison par intervalles
disjoints (jamais par une simple différence de moyennes), prévenir quand les deux groupes sont
dans des périodes différentes, 0 = valeur présente.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import analyse_donnees_manquantes as adm

DEBUT = datetime(2026, 11, 1, tzinfo=timezone.utc)


def position(pnl, jour=0, **champs):
    base = {"pnl_pct": pnl, "timestamp_entry": (DEBUT + timedelta(days=jour)).isoformat(),
            "entry_bundler_rate": 0.1}
    base.update(champs)
    return base


def lot(n, pnl, jour=0, **champs):
    return [position(pnl + (i % 5) * 0.1, jour, **champs) for i in range(n)]


class TestVerdict(unittest.TestCase):
    def test_sous_30_positions_par_groupe_aucun_verdict(self):
        positions = lot(29, -50, entry_bundler_rate=None) + lot(100, 10)
        self.assertEqual(adm.comparer(positions, "entry_bundler_rate")["verdict"], "échantillon trop court")

    def test_groupes_nettement_differents_donnent_un_verdict(self):
        positions = lot(40, -30, entry_bundler_rate=None) + lot(40, 10)
        self.assertEqual(adm.comparer(positions, "entry_bundler_rate")["verdict"], "sans donnée : moins bon")

    def test_groupes_semblables_sont_indistinguables(self):
        sans = [position(v, entry_bundler_rate=None) for v in ([-10, 15, -5, 20, -8, 12] * 8)]
        avec = [position(v) for v in ([-9, 14, -6, 19, -7, 13] * 8)]
        self.assertEqual(adm.comparer(sans + avec, "entry_bundler_rate")["verdict"], "indistinguable")

    def test_la_meilleure_moyenne_sans_intervalles_disjoints_ne_conclut_pas(self):
        # Une moyenne plus haute ne suffit pas : c'est l'intervalle qui décide.
        sans = [position(v, entry_bundler_rate=None) for v in ([40, -30, 25, -35, 30, -20] * 6)]
        avec = [position(v) for v in ([5, -4, 6, -5, 4, -3] * 6)]
        self.assertEqual(adm.comparer(sans + avec, "entry_bundler_rate")["verdict"], "indistinguable")


class TestGroupes(unittest.TestCase):
    def test_zero_est_une_valeur_presente(self):
        positions = [position(5, entry_sniper_count=0), position(5, entry_sniper_count=None)]
        r = adm.comparer(positions, "entry_sniper_count")
        self.assertEqual((r["sans"]["n"], r["avec"]["n"]), (1, 1))

    def test_resume_groupe_vide_ne_leve_pas(self):
        g = adm.resume_groupe([])
        self.assertEqual(g["n"], 0)
        self.assertIsNone(g["pnl_moyen_pct"])
        self.assertIsNone(g["du"])

    def test_pnl_illisible_est_ignore(self):
        g = adm.resume_groupe([{"pnl_pct": None}, {"pnl_pct": "x"}, position(10)])
        self.assertEqual(g["n"], 1)


class TestEpoque(unittest.TestCase):
    def test_periodes_disjointes_avertissent(self):
        positions = lot(40, -30, jour=20, entry_bundler_rate=None) + lot(40, 10, jour=0)
        avert = adm.comparer(positions, "entry_bundler_rate")["avertissements"]
        self.assertTrue(avert)
        self.assertIn("époque", avert[0])

    def test_memes_periodes_n_avertissent_pas(self):
        positions = lot(40, -30, jour=3, entry_bundler_rate=None) + lot(40, 10, jour=3)
        self.assertEqual(adm.comparer(positions, "entry_bundler_rate")["avertissements"], [])

    def test_recouvrement_de_deux_groupes_identiques_vaut_un(self):
        g = adm.resume_groupe(lot(5, 1, jour=2))
        self.assertEqual(adm.recouvrement(g, g), 1.0)


class TestRapport(unittest.TestCase):
    def test_le_rapport_dit_quand_une_donnee_n_est_jamais_vide(self):
        texte = "\n".join(adm.rapport("sniper", lot(5, 1)))
        self.assertIn("jamais vide", texte)

    def test_bras_sans_position_ne_leve_pas(self):
        self.assertIn("aucune position", "\n".join(adm.rapport("sniper", [])))


if __name__ == "__main__":
    unittest.main()
