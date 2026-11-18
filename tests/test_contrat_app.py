"""Le publieur produit exactement les champs que l'app iPhone lit (mobile/contrat.json).

LE BUG QUE ÇA FERME. L'app (mobile/app.js) et son test de fumée vérifient leurs
données de test contre `mobile/contrat.json`, pas contre le publieur. Un champ
renommé dans scripts/publie_supabase.py (« regles » → « paris_regles ») donnait
des écrans vides sur l'iPhone avec tous les tests verts. Ce test relie les deux
bouts : `construire_snapshots()` sur un jeu de données complet (une position,
deux bras, un bras Liquid, une sortie ZOMBIE, un service en panne) doit
produire EXACTEMENT les clés et champs du fichier, ni plus ni moins.
"""

import json
import os
import sys
import unittest
from datetime import timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import publie_supabase as pub  # noqa: E402
from tests.test_publie_supabase import MAINTENANT, DonneesTestCase  # noqa: E402

CONTRAT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mobile", "contrat.json")
PERF = ("perf_7d", "perf_30d", "perf_all")


def _cles(chemin_logique: str) -> list[str]:
    """`perf.bras` → les trois clés perf ; `etat.bot` → `etat`."""
    tete = chemin_logique.split(".")[0]
    return list(PERF) if tete == "perf" else [tete]


class TestContratApp(DonneesTestCase):

    def setUp(self):
        super().setUp()
        with open(CONTRAT_PATH, encoding="utf-8") as fh:
            self.contrat = json.load(fh)
        self._manifeste("baseline", "sniper")
        self._ecrire("state.json", {
            "updated_at": MAINTENANT.timestamp() - 30, "mode": "PAPER",
            "positions": [{"arm": "sniper", "symbol": "PEPE", "pnl_pct": 12.5, "size_usd": 50.0,
                           "duration_min": 14.2, "tp1_hit": True}],
        })
        self._trades("baseline", [self._sortie("a", "TAKE_PROFIT_1", MAINTENANT - timedelta(days=1), pnl=10)])
        self._trades("sniper", [self._sortie("b", "ZOMBIE (sans prix)", MAINTENANT - timedelta(hours=2), pnl=-4)])
        self._ecrire("veille_etat.json", {"liquid_runner": {"ok": False, "depuis": (MAINTENANT - timedelta(minutes=30)).isoformat()}})
        patch = mock.patch.object(pub.server, "get_prediction_summary", return_value={
            "exemple_fictif": False, "updated_at_iso": "2026-11-18T11:59:30+00:00",
            "arms": [{"arm": "pm_temoin", "famille": "transverse", "enabled": True, "ouverts": 2,
                      "compte": {"solde": 41.5}, "stats": {"paris_regles": 5, "gagnes_net": 3}}]})
        patch.start()
        self.addCleanup(patch.stop)
        self.snaps = pub.construire_snapshots(MAINTENANT)

    def test_les_sept_cles_et_leurs_champs_sont_ceux_que_l_app_lit(self):
        self.assertEqual(set(self.snaps), set(self.contrat["cles"]))
        for cle, champs in self.contrat["cles"].items():
            self.assertEqual(set(self.snaps[cle]), set(champs), cle)

    def test_les_objets_imbriques_ont_les_champs_que_l_app_lit(self):
        for chemin, champs in self.contrat["objets"].items():
            champ = chemin.split(".")[1]
            for cle in _cles(chemin):
                self.assertEqual(set(self.snaps[cle][champ]), set(champs), f"{cle}.{champ}")

    def test_chaque_ligne_des_listes_a_les_champs_que_l_app_lit(self):
        for chemin, champs in self.contrat["lignes"].items():
            champ = chemin.split(".")[1]
            for cle in _cles(chemin):
                lignes = self.snaps[cle][champ]
                self.assertTrue(lignes, f"{cle}.{champ} vide : le jeu de test ne vérifierait rien")
                for ligne in lignes:
                    self.assertEqual(set(ligne), set(champs), f"{cle}.{champ}")

    def test_les_alertes_couvrent_sortie_et_panne(self):
        self.assertEqual({a["type"] for a in self.snaps["alertes"]["lignes"]}, {"ZOMBIE", "PANNE"})


if __name__ == "__main__":
    unittest.main()
