"""Helius épuisé : coupe-circuit, bascule Chainstack, plafond des scripts. Hors réseau.

Contexte : le 16 nov., Helius répondait HTTP 429 « max usage reached » sur toutes
les méthodes, et le bot réessayait ~650 fois par heure. Ces tests verrouillent
la coupure, la bascule limitée aux deux méthodes compatibles, et le plafond.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from src.apis.helius import QUOTA_COOLDOWN_SECONDS, HeliusAPI  # noqa: E402
from helius_budget import ENHANCED_CREDITS_PER_CALL, CreditBudget  # noqa: E402

HELIUS = "https://mainnet.helius-rpc.com/?api-key=cle-de-test"
CHAINSTACK = "https://chainstack.test/rpc"


class Reponse:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text
        self.headers = {}

    def json(self):
        return self._body


class Session:
    """Remplace requests.Session : enregistre les URL et rend les réponses prévues."""

    def __init__(self, reponses):
        self.reponses = list(reponses)
        self.urls = []

    def post(self, url, json=None, timeout=None, auth=None):
        self.urls.append(url)
        if not self.reponses:
            return Reponse(500, None, "")
        return self.reponses.pop(0)


def client(reponses):
    horloge = [0.0]
    helius = HeliusAPI(
        "cle-de-test",
        max_per_second=10,
        clock=lambda: horloge[0],
        sleep=lambda s: horloge.__setitem__(0, horloge[0] + s),
        rng=lambda: 0.5,
    )
    session = Session(reponses)
    helius.session = session
    return helius, session, horloge


QUOTA = Reponse(429, None, "max usage reached")
CHAINSTACK_OK = Reponse(200, {"result": {"value": {"uiAmount": 1000}}})
CHAINSTACK_ENDPOINT = [(CHAINSTACK, None, "chainstack")]


class TestCoupeCircuitQuota(unittest.TestCase):
    def test_quota_epuise_coupe_helius_pendant_le_refroidissement(self):
        """Après « max usage reached », plus aucun appel Helius pendant une heure."""
        helius, session, horloge = client([QUOTA])
        with mock.patch("src.apis.helius.ordered_endpoints", return_value=[]):
            helius.get_supply("mint")
            helius.count_holders("mint")
        helius_urls = [u for u in session.urls if "helius" in u]
        self.assertEqual(len(helius_urls), 1, "un seul appel doit partir avant la coupure")
        self.assertTrue(helius.quota_exhausted)

    def test_helius_est_sonde_a_nouveau_apres_le_refroidissement(self):
        """Une seule sonde par heure : après la pause, Helius est de nouveau appelé."""
        helius, session, horloge = client([QUOTA, Reponse(200, {"result": {"value": {"uiAmount": 5}}})])
        with mock.patch("src.apis.helius.ordered_endpoints", return_value=[]):
            helius.get_supply("mint")
        horloge[0] = QUOTA_COOLDOWN_SECONDS + 1
        self.assertFalse(helius.quota_exhausted)

    def test_429_de_rafale_ne_coupe_pas_helius(self):
        """Une rafale (texte différent) garde l'ancien comportement : pas de coupure d'une heure."""
        helius, session, horloge = client([Reponse(429, None, "Too Many Requests")])
        with mock.patch("src.apis.helius.ordered_endpoints", return_value=[]):
            helius.get_supply("mint")
        self.assertFalse(helius.quota_exhausted)


class TestBasculeChainstack(unittest.TestCase):
    def test_get_supply_bascule_sur_chainstack_quand_helius_est_coupe(self):
        """getTokenSupply : Chainstack répond, le supply revient quand même."""
        helius, session, horloge = client([QUOTA, CHAINSTACK_OK])
        with mock.patch("src.apis.helius.ordered_endpoints", return_value=CHAINSTACK_ENDPOINT):
            helius.get_supply("mint")  # déclenche la coupure
            valeur = helius.get_supply("mint")
        self.assertEqual(valeur, 1000.0)
        self.assertEqual(session.urls[-1], CHAINSTACK)
        self.assertEqual(helius.fallback_calls, 1)

    def test_detenteurs_sans_substitut_rendent_none_quand_helius_est_coupe(self):
        """getTokenAccounts n'a pas d'équivalent Chainstack : pas d'appel, pas de rejet."""
        helius, session, horloge = client([QUOTA])
        with mock.patch("src.apis.helius.ordered_endpoints", return_value=CHAINSTACK_ENDPOINT):
            helius.get_supply("mint")
            session.urls.clear()
            resultat = helius._rpc("getTokenAccounts", {"mint": "mint", "limit": 1000})
        self.assertIsNone(resultat)
        self.assertEqual(session.urls, [], "ni Helius ni Chainstack ne doivent être appelés")

    def test_plus_gros_comptes_restent_sans_substitut(self):
        """getTokenLargestAccounts est refusé par Chainstack (403) : jamais tenté."""
        helius, session, horloge = client([QUOTA])
        with mock.patch("src.apis.helius.ordered_endpoints", return_value=CHAINSTACK_ENDPOINT):
            helius.get_supply("mint")
            session.urls.clear()
            self.assertEqual(helius.get_top_holders("mint", supply=1000.0), [])
        self.assertEqual(session.urls, [], "getTokenLargestAccounts ne doit partir nulle part")


class TestPlafondScripts(unittest.TestCase):
    def test_plafond_stoppe_les_appels_enhanced_avant_depassement(self):
        """Au-delà du plafond, charge() refuse : aucun crédit n'est compté au-dessus."""
        budget = CreditBudget(name="test", cap=250)
        self.assertTrue(budget.charge())
        self.assertTrue(budget.charge())
        self.assertFalse(budget.charge(), "le troisième appel dépasserait 250 crédits")
        self.assertEqual(budget.spent, 2 * ENHANCED_CREDITS_PER_CALL)

    def test_variable_d_environnement_remplace_le_plafond(self):
        """HELIUS_SCRIPT_CREDIT_CAP permet de relever le plafond sans toucher au code."""
        with mock.patch.dict(os.environ, {"HELIUS_SCRIPT_CREDIT_CAP": "1000"}):
            self.assertEqual(CreditBudget(name="test").cap, 1000)
        with mock.patch.dict(os.environ, {"HELIUS_SCRIPT_CREDIT_CAP": "pas-un-nombre"}):
            self.assertEqual(CreditBudget(name="test").cap, 50_000)


if __name__ == "__main__":
    unittest.main()
