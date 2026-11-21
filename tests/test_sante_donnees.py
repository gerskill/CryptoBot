"""Santé des données (src/core/sante_donnees.py).

Bug d'origine : contrôles on-chain en `error` pendant des jours, quota Birdeye épuisé, entrées
parties avec bundlers/insiders/snipers vides, et personne ne le voyait (« une donnée absente ne
rejette jamais »). Ces tests verrouillent la mesure, pas un comportement du bot.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import sante_donnees as sd

MAINTENANT = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)


def entree(onchain="ok", **champs):
    base = {
        "is_final_exit": True,
        "timestamp_entry": (MAINTENANT - timedelta(hours=3)).isoformat(),
        "onchain_status": onchain,
        "holders_at_entry": 120,
        "entry_bundler_rate": 0.1,
        "entry_insider_rate": 0.0,
        "entry_sniper_count": 2,
        "entry_entrapment_ratio": 0.05,
        "entry_smart_money_buys_30m": 0,
    }
    base.update(champs)
    return base


def lot(n, **kw):
    return [entree(**kw) for _ in range(n)]


class TestEtatDepuisPct(unittest.TestCase):
    def test_trop_peu_d_entrees_donne_inconnu_meme_a_100_pct(self):
        self.assertEqual(sd.etat_depuis_pct(100.0, sd.MIN_ENTREES - 1), "inconnu")

    def test_seuils(self):
        n = sd.MIN_ENTREES
        self.assertEqual(sd.etat_depuis_pct(0, n), "ok")
        self.assertEqual(sd.etat_depuis_pct(29.9, n), "ok")
        self.assertEqual(sd.etat_depuis_pct(30, n), "degrade")
        self.assertEqual(sd.etat_depuis_pct(79.9, n), "degrade")
        self.assertEqual(sd.etat_depuis_pct(80, n), "coupe")


class TestChampsVides(unittest.TestCase):
    def test_zero_est_une_valeur_valide_none_est_vide(self):
        entrees = [entree(entry_smart_money_buys_30m=0), entree(entry_smart_money_buys_30m=None)]
        par_champ = {c["champ"]: c["vide_pct"] for c in sd.champs_vides(entrees)}
        self.assertEqual(par_champ["smart money"], 50.0)
        self.assertEqual(par_champ["holders"], 0.0)

    def test_champ_absent_du_journal_compte_comme_vide(self):
        ligne = entree()
        del ligne["entry_bundler_rate"]
        par_champ = {c["champ"]: c["vide_pct"] for c in sd.champs_vides([ligne])}
        self.assertEqual(par_champ["bundlers"], 100.0)

    def test_trie_du_plus_vide_au_moins_vide(self):
        entrees = lot(4, entry_bundler_rate=None) + lot(4, entry_bundler_rate=0.2, entry_sniper_count=None)
        pourcentages = [c["vide_pct"] for c in sd.champs_vides(entrees)]
        self.assertEqual(pourcentages, sorted(pourcentages, reverse=True))

    def test_aucune_entree_donne_une_liste_vide(self):
        self.assertEqual(sd.champs_vides([]), [])


class TestSourceOnchain(unittest.TestCase):
    def _ligne(self, entrees):
        return sd._source_onchain(entrees)

    def test_presque_tout_en_erreur_est_coupe(self):
        ligne = self._ligne(lot(12, onchain="error") + lot(2, onchain="ok"))
        self.assertEqual(ligne["etat"], "coupe")
        self.assertIn("12/14", ligne["detail"])
        self.assertIn("erreur 12", ligne["detail"])

    def test_part_notable_en_erreur_est_degrade(self):
        ligne = self._ligne(lot(5, onchain="error") + lot(10, onchain="ok"))
        self.assertEqual(ligne["etat"], "degrade")

    def test_tout_va_bien(self):
        self.assertEqual(self._ligne(lot(15, onchain="ok"))["etat"], "ok")

    def test_modules_coupes_exprès_et_entrees_d_avant_ne_sont_pas_des_pannes(self):
        entrees = lot(20, onchain="disabled") + lot(20, onchain=None)
        ligne = self._ligne(entrees)
        self.assertEqual(ligne["etat"], "inconnu")

    def test_quota_et_absente_comptent_comme_defauts(self):
        ligne = self._ligne(lot(6, onchain="budget") + lot(6, onchain="missing") + lot(1, onchain="ok"))
        self.assertEqual(ligne["etat"], "coupe")
        self.assertIn("quota 6", ligne["detail"])
        self.assertIn("absente 6", ligne["detail"])

    def test_trop_peu_d_entrees_donne_inconnu(self):
        ligne = self._ligne(lot(3, onchain="error"))
        self.assertEqual(ligne["etat"], "inconnu")
        self.assertIn("trop peu", ligne["detail"])


class TestSourceGmgn(unittest.TestCase):
    def test_signaux_tous_vides_est_coupe(self):
        entrees = lot(12, entry_bundler_rate=None, entry_insider_rate=None, entry_sniper_count=None,
                      entry_entrapment_ratio=None)
        ligne = sd._source_gmgn(sd.champs_vides(entrees), len(entrees))
        self.assertEqual(ligne["etat"], "coupe")

    def test_signaux_remplis_est_ok(self):
        entrees = lot(12)
        self.assertEqual(sd._source_gmgn(sd.champs_vides(entrees), 12)["etat"], "ok")

    def test_holders_et_smart_money_ne_pesent_pas_sur_gmgn(self):
        entrees = lot(12, holders_at_entry=None, entry_smart_money_buys_30m=None)
        self.assertEqual(sd._source_gmgn(sd.champs_vides(entrees), 12)["etat"], "ok")


class TestCapacites(unittest.TestCase):
    def test_une_capacite_sans_source_est_coupe(self):
        lignes = sd._sources_capacites([{"capability": "holders", "using": None, "degraded": True,
                                         "available": [], "down": ["birdeye", "helius"], "blind": True}])
        self.assertEqual(lignes[0]["etat"], "coupe")

    def test_un_fournisseur_mort_avec_repli_est_degrade_et_nomme(self):
        lignes = sd._sources_capacites([{"capability": "bougies", "using": "gmgn", "degraded": True,
                                         "available": ["gmgn"], "down": ["birdeye"], "blind": False}])
        self.assertEqual(lignes[0]["etat"], "degrade")
        self.assertIn("gmgn", lignes[0]["detail"])
        self.assertIn("birdeye", lignes[0]["detail"])

    def test_tout_vivant_est_ok(self):
        lignes = sd._sources_capacites([{"capability": "prix", "using": "jupiter", "degraded": False,
                                         "available": ["jupiter", "dex"], "down": [], "blind": False}])
        self.assertEqual(lignes[0]["etat"], "ok")

    def test_une_valeur_illisible_est_ignoree_sans_lever(self):
        self.assertEqual(sd._sources_capacites("n'importe quoi"), [])
        self.assertEqual(sd._sources_capacites([None, {"pas": "de capacité"}]), [])


class TestBudgets(unittest.TestCase):
    def test_un_quota_epuise_est_degrade_jamais_coupe(self):
        # Twitter est épuisé depuis des semaines : « coupe » ferait sonner la veille sans fin.
        lignes = sd._source_budgets({"twitter": {"pct_used": 100.0}})
        self.assertEqual(lignes[0]["etat"], "degrade")
        self.assertIn("épuisé", lignes[0]["detail"])

    def test_quota_confortable_est_ok(self):
        self.assertEqual(sd._source_budgets({"twitter": {"pct_used": 12.0}})[0]["etat"], "ok")

    def test_budget_illisible_est_ignore(self):
        self.assertEqual(sd._source_budgets({"twitter": {}, "x": None}), [])


class TestResume(unittest.TestCase):
    def test_resume_complet(self):
        etat = {
            "capabilities": [{"capability": "prix", "using": "jupiter", "degraded": False,
                              "available": ["jupiter"], "down": [], "blind": False}],
            "budgets": {"twitter": {"pct_used": 100.0}},
        }
        r = sd.resume(etat, lot(12, onchain="error"))
        self.assertEqual(r["fenetre_h"], 24)
        self.assertEqual(r["entrees"], 12)
        noms = [s["nom"] for s in r["sources"]]
        self.assertEqual(noms[0], "prix")
        self.assertTrue(any("on-chain" in n for n in noms))
        self.assertTrue(any("GMGN" in n for n in noms))
        self.assertTrue(r["champs_vides"])

    def test_sans_state_ni_entrees_ne_leve_pas_et_dit_inconnu(self):
        r = sd.resume(None, [])
        self.assertEqual(r["entrees"], 0)
        self.assertEqual({s["etat"] for s in r["sources"]}, {"inconnu"})
        self.assertEqual(r["champs_vides"], [])

    def test_sources_coupees_ne_garde_que_les_coupees(self):
        r = sd.resume(None, lot(12, onchain="error"))
        coupees = sd.sources_coupees(r)
        self.assertEqual([s["etat"] for s in coupees], ["coupe"])
        self.assertIn("on-chain", coupees[0]["nom"])

    def test_rien_de_sensible_ne_sort(self):
        ligne = entree(token_address="HXT1UpmDkHmphFDrs3JWR3WHpsnoueCyABArTo7Apump", stop_loss_pct=-25)
        texte = json.dumps(sd.resume(None, [ligne] * 12))
        for interdit in ("HXT1Upm", "stop_loss", "token_address"):
            self.assertNotIn(interdit, texte)


class TestChargerEntrees(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _journal(self, nom, lignes, brut_en_plus=""):
        chemin = os.path.join(self.tmp, f"{nom}.jsonl")
        with open(chemin, "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(ligne) + "\n" for ligne in lignes)
            fh.write(brut_en_plus)
        return chemin

    def _charger(self, bras):
        return sd.charger_entrees(MAINTENANT, bras, lambda nom: os.path.join(self.tmp, f"{nom}.jsonl"))

    def test_seules_les_sorties_finales_des_24h_comptent(self):
        ancien = entree(timestamp_entry=(MAINTENANT - timedelta(hours=30)).isoformat())
        partielle = entree(is_final_exit=False)
        self._journal("baseline", [entree(), ancien, partielle])
        self.assertEqual(len(self._charger(["baseline"])), 1)

    def test_les_bras_demandes_seulement(self):
        self._journal("baseline", [entree()])
        self._journal("eteint", [entree(), entree()])
        self.assertEqual(len(self._charger(["baseline"])), 1)

    def test_journal_absent_ou_ligne_corrompue_ne_leve_pas(self):
        self._journal("baseline", [entree()], brut_en_plus="{pas du json\n")
        self.assertEqual(len(self._charger(["baseline", "inexistant"])), 1)

    def test_seule_la_fin_du_fichier_est_lue_et_la_ligne_coupee_est_ecartee(self):
        lignes = [entree(onchain="ok") for _ in range(50)] + [entree(onchain="error") for _ in range(5)]
        chemin = self._journal("baseline", lignes)
        taille_ligne = len(json.dumps(lignes[0])) + 1
        lues = sd._fin_du_journal(chemin, max_octets=int(taille_ligne * 5.5))
        statuts = [ligne["onchain_status"] for ligne in lues]
        # 5,5 lignes demandées : la moitié de ligne du début (un « ok ») est écartée, les 5 entières restent.
        self.assertEqual(statuts, ["error"] * 5)

    def test_entree_sans_horodatage_est_ignoree(self):
        sans = entree()
        del sans["timestamp_entry"]
        self._journal("baseline", [sans])
        self.assertEqual(self._charger(["baseline"]), [])


if __name__ == "__main__":
    unittest.main()
