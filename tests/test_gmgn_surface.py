"""Surface GMGN complète : KOL, traders par token, concentration, socials.

Trois méthodes existaient sans aucun appelant (`token_security`,
`token_holders`, `token_info`) et deux commandes étaient autorisées sans code
du tout (`track kol`, `token traders`). Ces tests couvrent le câblage, et
surtout les pièges de format qui rendraient le signal FAUX sans lever
d'erreur : une fraction prise pour un pourcentage, un top plafonné pris pour
un total, un flux KOL confondu avec le flux smart money.
"""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.apis.gmgn import ForbiddenCommand, GmgnAPI  # noqa: E402
from src.core.cache import TokenCache  # noqa: E402
from src.core.params import ParamsStore  # noqa: E402
from src.pipeline import ScanPipeline  # noqa: E402
from tests.test_pipeline_split import (  # noqa: E402
    BASE_PARAMS, Disabled, candidate,
)
from src.core.capabilities import (  # noqa: E402
    CANDLES, HOLDERS, PRICE, SECURITY, SMART_MONEY, SOCIAL, build_registry,
)


def trade(age_minutes=1.0, side="buy", usd=100.0, wallet="w1", token="tok", tags=("smart_degen",)):
    return {
        "maker": wallet,
        "base_address": token,
        "timestamp": time.time() - age_minutes * 60,
        "side": side,
        "amount_usd": usd,
        "maker_info": {"tags": list(tags)},
    }


def api(canned=None):
    """Client GMGN dont `_run` rend des réponses préparées, sans sous-processus.

    `canned` est indexé par (commande, sous-commande) — des clés tuple, donc
    un dict positionnel et non des kwargs.
    """
    reponses = canned or {}
    client = GmgnAPI(enabled=False)
    client.enabled = True
    client._run = lambda *args: reponses.get((args[0], args[1]))
    return client


class TestFluxKOL(unittest.TestCase):
    def test_kol_et_smart_money_ne_partagent_pas_leur_cache(self):
        client = api()
        client._run = lambda *a: [trade(token="KOLTOK")] if a[1] == "kol" else [trade(token="SMTOK")]

        kol = client.kol_activity_by_token()
        smart = client.activity_by_token()

        self.assertIn("KOLTOK", kol)
        self.assertNotIn("SMTOK", kol, "le flux KOL ne doit pas voir le flux smart money")
        self.assertIn("SMTOK", smart)
        self.assertNotIn("KOLTOK", smart)

    def test_un_seul_appel_pour_tout_le_lot(self):
        appels = []
        client = api()
        client._run = lambda *a: appels.append(a[:2]) or [trade(token="A"), trade(token="B", wallet="w2")]

        activite = client.kol_activity_by_token()

        self.assertEqual(len(appels), 1, "le flux KOL est global : 1 requête, pas 1 par token")
        self.assertEqual(set(activite), {"A", "B"})

    def test_les_bots_sont_exclus_du_flux_kol_aussi(self):
        client = api()
        client._run = lambda *a: [trade(tags=("kol", "arbitrager"))]
        self.assertEqual(client.kol_activity_by_token(), {})

    def test_agregation_partagee_compte_achats_et_ventes(self):
        client = api()
        client._run = lambda *a: [
            trade(side="buy", wallet="w1"),
            trade(side="buy", wallet="w2"),
            trade(side="sell", wallet="w3"),
        ]
        stats = client.kol_activity_by_token()["tok"]
        self.assertEqual((stats.buys, stats.sells, stats.unique_wallets), (2, 1, 3))

    def test_module_coupe_rend_une_liste_vide(self):
        client = GmgnAPI(enabled=False)
        self.assertEqual(client.fetch_kol_trades(), [])
        self.assertEqual(client.kol_activity_by_token(), {})


class TestConcentration(unittest.TestCase):
    def test_fraction_convertie_en_pourcentage(self):
        # 0.0731 est une FRACTION : 7.31 %, pas 0.0731 %.
        client = api({("token", "holders"): [{"amount_percentage": 0.0731}]})
        self.assertAlmostEqual(client.holder_concentration("t").top_holder_pct, 7.31, places=2)

    def test_pourcentage_deja_en_centiemes_est_laisse_tel_quel(self):
        client = api({("token", "holders"): [{"amount_percentage": 42.0}]})
        self.assertAlmostEqual(client.holder_concentration("t").top_holder_pct, 42.0, places=2)

    def test_top1_est_le_plus_gros_quel_que_soit_l_ordre_recu(self):
        client = api({("token", "holders"): [
            {"amount_percentage": 5.0}, {"amount_percentage": 30.0}, {"amount_percentage": 12.0},
        ]})
        concentration = client.holder_concentration("t")
        self.assertEqual(concentration.top_holder_pct, 30.0)
        self.assertEqual(concentration.top10_holder_pct, 47.0)

    def test_top10_ne_somme_que_les_dix_premiers(self):
        client = api({("token", "holders"): [{"amount_percentage": 1.0} for _ in range(15)]})
        self.assertEqual(client.holder_concentration("t").top10_holder_pct, 10.0)

    def test_aucun_compte_de_holders_n_est_expose(self):
        # Le top est plafonné à --limit : `len(liste)` serait un total faux.
        client = api({("token", "holders"): [{"amount_percentage": 1.0} for _ in range(20)]})
        concentration = client.holder_concentration("t")
        self.assertFalse(hasattr(concentration, "holder_count"))
        self.assertEqual(concentration.sample_size, 20)

    def test_petits_pourcentages_ne_sont_pas_pris_pour_des_fractions(self):
        # 15 porteurs à 0.5 % : chaque valeur est < 1, mais la SOMME (7.5)
        # tranche — c'est déjà du pourcentage, il ne faut pas multiplier.
        client = api({("token", "holders"): [{"amount_percentage": 0.5} for _ in range(15)]})
        self.assertAlmostEqual(client.holder_concentration("t").top_holder_pct, 0.5, places=3)

    def test_vraies_fractions_multiples_sont_converties(self):
        client = api({("token", "holders"): [
            {"amount_percentage": 0.05}, {"amount_percentage": 0.03}, {"amount_percentage": 0.02},
        ]})
        concentration = client.holder_concentration("t")
        self.assertAlmostEqual(concentration.top_holder_pct, 5.0, places=3)
        self.assertAlmostEqual(concentration.top10_holder_pct, 10.0, places=3)

    def test_un_porteur_unique_a_1_est_lu_comme_100_pourcent(self):
        # Ambiguïté assumée : se tromper vers le haut rejette un token sain,
        # se tromper vers le bas laisse passer un wallet qui tient tout.
        client = api({("token", "holders"): [{"amount_percentage": 1.0}]})
        self.assertEqual(client.holder_concentration("t").top_holder_pct, 100.0)

    def test_les_parts_ne_depassent_jamais_cent(self):
        client = api({("token", "holders"): [{"amount_percentage": 80.0},
                                             {"amount_percentage": 70.0}]})
        self.assertEqual(client.holder_concentration("t").top10_holder_pct, 100.0)

    def test_liste_vide_ou_illisible_rend_none(self):
        self.assertIsNone(api({("token", "holders"): []}).holder_concentration("t"))
        self.assertIsNone(api({("token", "holders"): [{"autre": 1}]}).holder_concentration("t"))


class TestSocials(unittest.TestCase):
    def test_presence_detectee_a_la_racine_et_dans_social_links(self):
        self.assertTrue(api({("token", "info"): {"twitter_username": "abc"}}).has_socials("t"))
        self.assertTrue(
            api({("token", "info"): {"social_links": {"telegram": "t.me/x"}}}).has_socials("t")
        )

    def test_champs_vides_rendent_false_pas_none(self):
        client = api({("token", "info"): {"twitter_username": "", "website": "   "}})
        self.assertIs(client.has_socials("t"), False)

    def test_information_indisponible_rend_none(self):
        # None ≠ False : « je ne sais pas » ne doit pas compter comme « aucun réseau ».
        self.assertIsNone(api({("token", "info"): None}).has_socials("t"))


class TestTradersEtGardeFou(unittest.TestCase):
    def test_token_traders_passe_la_liste_blanche(self):
        client = api({("token", "traders"): [{"maker": "w1"}]})
        self.assertEqual(client.token_traders("t"), [{"maker": "w1"}])

    def test_les_commandes_d_ecriture_restent_interdites(self):
        client = GmgnAPI(enabled=False)
        client.enabled = True
        for interdite in (("swap", "buy"), ("order", "create"), ("cooking", "launch")):
            with self.assertRaises(ForbiddenCommand):
                client._run(*interdite)


class TestChainesDeCapacites(unittest.TestCase):
    """Les capacités nommées en tête de module doivent être ENREGISTRÉES.

    `SECURITY`, `SOCIAL` et `SMART_MONEY` étaient déclarées comme constantes
    sans qu'aucune chaîne existe : `blind_spots` ne pouvait pas les signaler.
    """

    def setUp(self):
        self.registry = build_registry(
            birdeye=None, gmgn=None, helius=None, jupiter=None,
            dex=None, rugcheck=None, twitter=None,
        )

    def test_les_six_capacites_sont_enregistrees(self):
        self.assertEqual(
            set(self.registry.capabilities),
            {CANDLES, HOLDERS, PRICE, SECURITY, SOCIAL, SMART_MONEY},
        )

    def test_gmgn_ferme_la_chaine_holders(self):
        noms = [p.name for p in self.registry.capabilities[HOLDERS].providers]
        self.assertEqual(noms, ["birdeye/overview", "helius", "gmgn/holders"])

    def test_birdeye_ferme_la_chaine_prix(self):
        noms = [p.name for p in self.registry.capabilities[PRICE].providers]
        self.assertEqual(noms[-1], "birdeye/price", "coût linéaire : dernier recours")

    def test_rugcheck_precede_gmgn_pour_la_securite(self):
        noms = [p.name for p in self.registry.capabilities[SECURITY].providers]
        self.assertEqual(noms, ["rugcheck", "gmgn/security"])

    def test_twitter_precede_le_signal_binaire_gmgn(self):
        noms = [p.name for p in self.registry.capabilities[SOCIAL].providers]
        self.assertEqual(noms, ["twitter", "gmgn/token-info"])

    def test_flux_global_avant_les_kol(self):
        noms = [p.name for p in self.registry.capabilities[SMART_MONEY].providers]
        self.assertEqual(noms, ["gmgn/track-smartmoney", "gmgn/track-kol"])

    def test_tout_est_aveugle_sans_aucune_source(self):
        self.assertEqual(len(self.registry.blind_spots), 6)


class TestRepliConcentrationDansLePipeline(unittest.TestCase):
    """GMGN ne parle QUE quand Birdeye, Helius et RugCheck se sont tus.

    Sans ce maillon, ces trois sources mortes laissaient
    `max_top_wallet_concentration` sans donnée — et un filtre sans donnée ne
    rejette rien : la garde devenait silencieusement inactive.
    """

    def _pipeline(self, gmgn):
        tmp = tempfile.mkdtemp()
        params_path = os.path.join(tmp, "params.json")
        with open(params_path, "w", encoding="utf-8") as fh:
            json.dump(BASE_PARAMS, fh)
        return ScanPipeline(
            params=ParamsStore(params_path),
            cache=TokenCache(os.path.join(tmp, "cache.json")),
            dex=None, helius=Disabled(), rugcheck=Disabled(), gmgn=gmgn,
        )

    def test_concentration_recuperee_quand_les_autres_sont_morts(self):
        gmgn = api({("token", "holders"): [{"amount_percentage": 0.42},
                                           {"amount_percentage": 0.10}]})
        enrichi = self._pipeline(gmgn)._enrich_one(candidate("SEUL"), BASE_PARAMS["filters"])
        self.assertAlmostEqual(enrichi.top_holder_pct, 42.0, places=2)
        self.assertAlmostEqual(enrichi.top10_holder_pct, 52.0, places=2)

    def test_aucun_compte_de_holders_nest_invente(self):
        gmgn = api({("token", "holders"): [{"amount_percentage": 0.42}]})
        enrichi = self._pipeline(gmgn)._enrich_one(candidate("SEUL"), BASE_PARAMS["filters"])
        self.assertIsNone(enrichi.holders, "GMGN ne connaît pas le total : ne rien écrire")

    def test_gmgn_ne_recouvre_pas_une_donnee_deja_connue(self):
        appels = []
        gmgn = api({("token", "holders"): [{"amount_percentage": 0.90}]})
        gmgn._run = lambda *a: appels.append(a[:2]) or [{"amount_percentage": 0.90}]

        deja_connu = candidate("CONNU").with_fields(top_holder_pct=12.0)
        enrichi = self._pipeline(gmgn)._enrich_one(deja_connu, BASE_PARAMS["filters"])

        self.assertEqual(enrichi.top_holder_pct, 12.0)
        self.assertEqual(appels, [], "1 requête par token : ne pas la dépenser pour rien")

    def test_module_gmgn_coupe_ne_casse_pas_l_enrichissement(self):
        enrichi = self._pipeline(GmgnAPI(enabled=False))._enrich_one(
            candidate("MUET"), BASE_PARAMS["filters"]
        )
        self.assertIsNone(enrichi.top_holder_pct)


if __name__ == "__main__":
    unittest.main()


class FakeHeliusDevWallet:
    """Helius activé, dev_wallet_pct et get_supply sous contrôle du test."""

    enabled = True

    def __init__(self, dev_pct=None, supply=None):
        self._dev_pct = dev_pct
        self._supply = supply
        self.dev_wallet_calls = []

    def get_dev_wallet_pct(self, mint):
        self.dev_wallet_calls.append(mint)
        return self._dev_pct

    def get_supply(self, mint):
        return self._supply

    def get_holder_stats(self, mint, min_required=None):
        return None


class FakeBirdeyeConcentration:
    """Birdeye activé : get_concentration sous contrôle, appels comptés."""

    enabled = True

    def __init__(self, pct=None):
        self._pct = pct
        self.concentration_calls = []

    def get_overview(self, address):
        return None

    def get_concentration(self, address, supply):
        self.concentration_calls.append((address, supply))
        return self._pct


class TestRepliHeliusDevWalletPct(unittest.TestCase):
    """`HeliusAPI.get_dev_wallet_pct` existait sans aucun appelant."""

    def _pipeline(self, helius, gmgn=None):
        tmp = tempfile.mkdtemp()
        params_path = os.path.join(tmp, "params.json")
        with open(params_path, "w", encoding="utf-8") as fh:
            json.dump(BASE_PARAMS, fh)
        return ScanPipeline(
            params=ParamsStore(params_path),
            cache=TokenCache(os.path.join(tmp, "cache.json")),
            dex=None, helius=helius, rugcheck=Disabled(), gmgn=gmgn,
        )

    def test_repli_quand_rugcheck_ne_donne_rien(self):
        helius = FakeHeliusDevWallet(dev_pct=17.5)
        enrichi = self._pipeline(helius)._enrich_one(candidate("X"), BASE_PARAMS["filters"])
        self.assertEqual(enrichi.dev_wallet_pct, 17.5)
        self.assertEqual(helius.dev_wallet_calls, ["addr_X"])

    def test_pas_dappel_si_deja_connu(self):
        helius = FakeHeliusDevWallet(dev_pct=99.0)
        deja_connu = candidate("X").with_fields(dev_wallet_pct=3.0)
        enrichi = self._pipeline(helius)._enrich_one(deja_connu, BASE_PARAMS["filters"])
        self.assertEqual(enrichi.dev_wallet_pct, 3.0)
        self.assertEqual(helius.dev_wallet_calls, [], "RugCheck avait déjà répondu (implicite via le champ)")

    def test_helius_desactive_ne_casse_rien(self):
        helius = FakeHeliusDevWallet(dev_pct=50.0)
        helius.enabled = False
        enrichi = self._pipeline(helius)._enrich_one(candidate("X"), BASE_PARAMS["filters"])
        self.assertIsNone(enrichi.dev_wallet_pct)

    def test_zero_pourcent_est_une_vraie_reponse_pas_une_absence(self):
        # 0.0 = dev absent du top 20 -> valeur légitime, ne doit pas être
        # confondue avec "donnée manquante".
        helius = FakeHeliusDevWallet(dev_pct=0.0)
        enrichi = self._pipeline(helius)._enrich_one(candidate("X"), BASE_PARAMS["filters"])
        self.assertEqual(enrichi.dev_wallet_pct, 0.0)


class TestRepliBirdeyeConcentration(unittest.TestCase):
    """`BirdeyeAPI.get_concentration` existait sans aucun appelant.

    Doit rester le TOUT DERNIER recours : GMGN rend la même information
    gratuitement, Birdeye est le goulot documenté du bot (1 req/s, quota
    mensuel qui s'épuise).
    """

    def _pipeline(self, birdeye, helius, gmgn=None):
        tmp = tempfile.mkdtemp()
        params_path = os.path.join(tmp, "params.json")
        with open(params_path, "w", encoding="utf-8") as fh:
            json.dump(BASE_PARAMS, fh)
        return ScanPipeline(
            params=ParamsStore(params_path),
            cache=TokenCache(os.path.join(tmp, "cache.json")),
            dex=None, helius=helius, rugcheck=Disabled(),
            birdeye=birdeye, gmgn=gmgn,
        )

    def test_birdeye_ne_se_declenche_pas_si_gmgn_a_deja_repondu(self):
        birdeye = FakeBirdeyeConcentration(pct=80.0)
        helius = FakeHeliusDevWallet(supply=1_000_000)
        gmgn = api({("token", "holders"): [{"amount_percentage": 0.20}]})

        enrichi = self._pipeline(birdeye, helius, gmgn)._enrich_one(
            candidate("X"), BASE_PARAMS["filters"]
        )

        self.assertAlmostEqual(enrichi.top_holder_pct, 20.0, places=2)
        self.assertEqual(birdeye.concentration_calls, [], "GMGN suffisait, Birdeye ne doit pas payer")

    def test_birdeye_se_declenche_quand_gmgn_est_absent(self):
        birdeye = FakeBirdeyeConcentration(pct=33.0)
        helius = FakeHeliusDevWallet(supply=1_000_000)

        enrichi = self._pipeline(birdeye, helius, gmgn=None)._enrich_one(
            candidate("X"), BASE_PARAMS["filters"]
        )

        self.assertEqual(enrichi.top_holder_pct, 33.0)
        self.assertEqual(birdeye.concentration_calls, [("addr_X", 1_000_000)])

    def test_sans_supply_helius_birdeye_nest_pas_appele(self):
        birdeye = FakeBirdeyeConcentration(pct=33.0)
        helius = FakeHeliusDevWallet(supply=None)
        enrichi = self._pipeline(birdeye, helius, gmgn=None)._enrich_one(
            candidate("X"), BASE_PARAMS["filters"]
        )
        self.assertIsNone(enrichi.top_holder_pct)
        self.assertEqual(birdeye.concentration_calls, [])

    def test_deja_connu_naugmente_aucun_appel(self):
        birdeye = FakeBirdeyeConcentration(pct=33.0)
        helius = FakeHeliusDevWallet(supply=1_000_000)
        deja_connu = candidate("X").with_fields(top_holder_pct=5.0)
        enrichi = self._pipeline(birdeye, helius, gmgn=None)._enrich_one(
            deja_connu, BASE_PARAMS["filters"]
        )
        self.assertEqual(enrichi.top_holder_pct, 5.0)
        self.assertEqual(birdeye.concentration_calls, [])
