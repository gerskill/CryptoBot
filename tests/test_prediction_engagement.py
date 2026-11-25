"""Bras de prédiction — limite d'engagement total (demande de Killian, 2026-11-22).

Ce que ces tests verrouillent :
  - un bras n'engage jamais plus de 25 % de son compte en paris ouverts
    (mises + frais d'achat) ; un pari qui ferait dépasser est refusé
    avec le motif « engagement_total_max » ;
  - la base est la plus prudente : min(capital de départ, capital + réglé,
    équité au marché du cycle précédent) ;
  - les paris déjà ouverts au-delà ne sont jamais fermés ;
  - sans réglage, rien ne change (compatibilité).
Aucun test ne touche le réseau.
"""

import json
import os
import sys
import unittest
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from src.prediction.paper import BetStore, account, bet_is_long, engagement_limit  # noqa: E402
from src.prediction.runner import load_config, summary  # noqa: E402
from tests.test_prediction import NOW, TEMOIN, make_meta  # noqa: E402
from tests.test_prediction_saison3 import Saison3Case  # noqa: E402

LIMITE = {"engagement_total_max_pct": 0.25, "engagement_long_max_pct": 0.10, "engagement_long_heures": 24}


HELD = 201  # issue d'un pari déjà ouvert : vivante, pas encore échue


def ouvert(id_, stake, fee=0.0, heures=1.0):
    """Pari déjà ouvert ; `heures` = durée entrée → règlement (None = inconnue)."""
    entree = NOW - timedelta(minutes=1)
    return {"id": id_, "coin": f"#{10 * HELD}", "shares": stake * 2, "stake_usd": stake,
            "fee_entry_usd": fee, "outcome": HELD, "side_index": 0,
            "entry_time": entree.isoformat(),
            "expiry": (entree + timedelta(hours=heures)).isoformat() if heures is not None else None}


class TestCalculLimite(unittest.TestCase):
    def test_la_limite_vaut_25_pourcent_du_capital_de_depart(self):
        compte = account(500.0, [], [ouvert("a", 100.0, 1.0)])
        lim = engagement_limit(compte, 0.25)
        self.assertEqual(lim["limite"], 125.0)
        self.assertEqual(lim["engage"], 101.0)
        self.assertAlmostEqual(lim["disponible"], 24.0)
        self.assertFalse(lim["depasse"])

    def test_les_pertes_reglees_baissent_la_base(self):
        compte = account(500.0, [{"id": "s", "status": "regle", "pnl_net": -100.0}], [])
        self.assertEqual(engagement_limit(compte, 0.25)["limite"], 100.0)

    def test_les_gains_ne_montent_pas_la_base_au_dessus_du_capital(self):
        compte = account(500.0, [{"id": "s", "status": "regle", "pnl_net": 200.0}], [])
        self.assertEqual(engagement_limit(compte, 0.25)["base"], 500.0)

    def test_une_equite_au_marche_plus_basse_baisse_la_base(self):
        compte = account(500.0, [], [ouvert("a", 100.0)], {"a": {"valeur": 0.0, "methode": "carnet"}})
        self.assertEqual(engagement_limit(compte, 0.25)["base"], 400.0)

    def test_duree_d_un_pari(self):
        self.assertFalse(bet_is_long(ouvert("a", 1, heures=24)))
        self.assertTrue(bet_is_long(ouvert("a", 1, heures=24.1)))
        self.assertTrue(bet_is_long(ouvert("a", 1, heures=None)))      # inconnue = long

    def test_sans_reglage_pas_de_limite(self):
        self.assertIsNone(engagement_limit(account(500.0, [], []), None))


class TestLimiteDansLeCycle(Saison3Case):
    def test_un_pari_qui_ferait_depasser_25_pourcent_est_refuse(self):
        BetStore(self.tmp, "pm_temoin").add(ouvert("deja", 115.0, 1.0))   # 116 $ engagés
        meta, mids, books = self.marche(0.5)
        result = self.runner(self._api(meta, mids, books), [TEMOIN], **LIMITE).cycle()
        self.assertEqual(result["ouverts_ce_cycle"], 0)
        self.assertEqual(result["raisons"]["pm_temoin:engagement_total_max"], 1)

    def test_sous_la_limite_le_pari_entre(self):
        BetStore(self.tmp, "pm_temoin").add(ouvert("deja", 100.0, 0.9))   # 100,9 + 10,09 < 125
        meta, mids, books = self.marche(0.5)
        result = self.runner(self._api(meta, mids, books), [TEMOIN], **LIMITE).cycle()
        self.assertEqual(result["ouverts_ce_cycle"], 1)

    def test_le_cout_reel_apres_taille_minimale_est_controle(self):
        # Cible 1 % = 5 $ passe, mais le minimum à 0,80 coûte 40 $ : refusé.
        BetStore(self.tmp, "pm_temoin").add(ouvert("deja", 100.0))
        meta, mids, books = self.marche(0.80)
        result = self.runner(self._api(meta, mids, books), [TEMOIN], **LIMITE).cycle()
        self.assertEqual(result["raisons"]["pm_temoin:engagement_total_max"], 1)

    def test_les_paris_deja_au_dela_ne_sont_jamais_fermes(self):
        store = BetStore(self.tmp, "pm_temoin")
        for i in range(5):
            store.add(ouvert(f"gros{i}", 99.0, 0.4))                    # 497 $ engagés
        meta, mids, books = self.marche(0.5)
        result = self.runner(self._api(meta, mids, books), [TEMOIN], **LIMITE).cycle()
        self.assertEqual(result["raisons"]["pm_temoin:engagement_total_max"], 1)
        self.assertEqual(len(store.open_bets()), 5)
        self.assertAlmostEqual(sum(b["stake_usd"] for b in store.open_bets()), 495.0)

    def test_l_equite_au_marche_du_cycle_precedent_rend_la_limite_plus_prudente(self):
        BetStore(self.tmp, "pm_temoin").add(ouvert("deja", 100.0))
        with open(os.path.join(self.tmp, "marks.json"), "w") as f:
            json.dump({"bets": {"deja": {"valeur": 0.0, "methode": "carnet"}}}, f)
        meta, mids, books = self.marche(0.5)
        result = self.runner(self._api(meta, mids, books), [TEMOIN], **LIMITE).cycle()
        # base = 400 $ -> limite 100 $ ; 100 + 10,09 dépasse.
        self.assertEqual(result["raisons"]["pm_temoin:engagement_total_max"], 1)

    def test_sans_reglage_le_comportement_est_inchange(self):
        BetStore(self.tmp, "pm_temoin").add(ouvert("deja", 300.0))
        meta, mids, books = self.marche(0.5)
        result = self.runner(self._api(meta, mids, books), [TEMOIN]).cycle()
        self.assertEqual(result["ouverts_ce_cycle"], 1)

    def test_le_resume_montre_engage_et_limite(self):
        BetStore(self.tmp, "pm_temoin").add(ouvert("deja", 200.0))
        meta, mids, books = self.marche(0.5)
        runner = self.runner(self._api(meta, mids, books), [TEMOIN], **LIMITE)
        runner.cycle()
        row = summary(runner.base_config, self.tmp)[0]
        self.assertEqual(row["engagement"]["limite"], 125.0)
        self.assertTrue(row["engagement"]["depasse"])

    def test_le_fichier_de_config_fixe_25_pourcent(self):
        config = load_config(settings.PREDICTION_ARMS_PATH)
        self.assertEqual(config["engagement_total_max_pct"], 0.25)
        self.assertEqual(config["engagement_long_max_pct"], 0.1)
        self.assertEqual(config["engagement_long_heures"], 24)

    def _marche_long(self, ask, expiry):
        meta, mids, books = self.marche(ask)
        meta = make_meta(expiry=expiry)
        return meta, mids, books

    def test_un_pari_long_est_refuse_au_dela_de_10_pourcent(self):
        BetStore(self.tmp, "pm_temoin").add(ouvert("long", 45.0, heures=72))   # 45 $ longs
        meta, mids, books = self._marche_long(0.5, NOW.strftime("%Y%m") + f"{NOW.day + 3:02d}-1315")
        result = self.runner(self._api(meta, mids, books), [TEMOIN], **LIMITE).cycle()
        self.assertEqual(result["raisons"]["pm_temoin:engagement_long_max"], 1)

    def test_un_pari_court_passe_meme_si_les_longs_depassent_10_pourcent(self):
        BetStore(self.tmp, "pm_temoin").add(ouvert("long", 60.0, heures=72))   # 60 $ longs > 50 $
        meta, mids, books = self.marche(0.5)                                   # marché de 15 min
        result = self.runner(self._api(meta, mids, books), [TEMOIN], **LIMITE).cycle()
        self.assertEqual(result["ouverts_ce_cycle"], 1)

    def test_les_paris_courts_ne_comptent_pas_dans_la_limite_longue(self):
        BetStore(self.tmp, "pm_temoin").add(ouvert("court", 100.0, heures=2))
        meta, mids, books = self._marche_long(0.5, NOW.strftime("%Y%m") + f"{NOW.day + 3:02d}-1315")
        result = self.runner(self._api(meta, mids, books), [TEMOIN], **LIMITE).cycle()
        self.assertEqual(result["ouverts_ce_cycle"], 1)

    def test_le_resume_montre_l_engagement_long(self):
        store = BetStore(self.tmp, "pm_temoin")
        store.add(ouvert("long", 30.0, heures=48))
        store.add(ouvert("inconnu", 25.0, heures=None))
        store.add(ouvert("court", 40.0, heures=3))
        meta, mids, books = self.marche(0.5)
        runner = self.runner(self._api(meta, mids, books), [TEMOIN], **LIMITE)
        row = summary(runner.base_config, self.tmp)[0]
        self.assertEqual(row["engagement"]["limite_long"], 50.0)
        self.assertEqual(row["engagement"]["engage_long"], 55.0)
        self.assertTrue(row["engagement"]["depasse_long"])

    def _api(self, meta, mids, books):
        from tests.test_prediction import FakeApi
        meta = dict(meta, outcomes=meta["outcomes"] + [
            {"outcome": HELD, "name": "Tenu", "description": "", "sideSpecs": [{"name": "Yes"}, {"name": "No"}]}])
        return FakeApi(meta, mids, books)


if __name__ == "__main__":
    unittest.main()
