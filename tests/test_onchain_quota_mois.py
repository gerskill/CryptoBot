"""Quota Helius du mois épuisé (`429 max usage reached`, mesuré le 2026-11-21).

Avant : chaque candidat refaisait ses appels, tous refusés, et l'entrée était notée « error ».
Verrouillé ici : le premier 429 « max usage » met l'observateur en pause une heure (aucun appel
réseau), les entrées sont notées « budget » (quota), et l'observateur réessaie après la pause.
Un 429 de débit ordinaire (sans « max usage ») ne déclenche pas la pause.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents import onchain_observer as oo


class Reponse:
    def __init__(self, statut, texte="", corps=None):
        self.status_code = statut
        self.text = texte
        self._corps = corps if corps is not None else {"jsonrpc": "2.0", "id": 1, "result": {"ok": 1}}

    def json(self):
        return self._corps


class Session:
    def __init__(self, reponses):
        self.reponses = list(reponses)
        self.appels = 0

    def post(self, *args, **kwargs):
        self.appels += 1
        return self.reponses.pop(0) if self.reponses else Reponse(200)


class TestQuotaDuMois(unittest.TestCase):
    def setUp(self):
        self.t = [1_000_000.0]
        self.session = Session([Reponse(429, "max usage reached")])
        self.obs = oo.OnchainObserver("k", session=self.session, start_thread=False,
                                      rdap_enabled=False, clock=lambda: self.t[0])

    def _appel(self):
        return self.obs._rpc("getAccountInfo", ["mint"], 1)

    def test_le_429_max_usage_met_en_pause_et_compte_en_budget(self):
        with redirect_stdout(io.StringIO()), self.assertRaises(oo._BudgetExceeded):
            self._appel()
        with self.assertRaises(oo._BudgetExceeded):
            self._appel()
        self.assertEqual(self.session.appels, 1)  # le second n'a pas touché le réseau

    def test_apres_la_pause_l_observateur_retente(self):
        with redirect_stdout(io.StringIO()), self.assertRaises(oo._BudgetExceeded):
            self._appel()
        self.t[0] += oo.QUOTA_MOIS_PAUSE_S + 1
        self.assertEqual(self._appel(), {"ok": 1})
        self.assertEqual(self.session.appels, 2)

    def test_un_seul_message_par_coupure(self):
        sortie = io.StringIO()
        self.session.reponses = [Reponse(429, "max usage reached"), Reponse(429, "max usage reached")]
        with redirect_stdout(sortie):
            for _ in range(2):
                with self.assertRaises(oo._BudgetExceeded):
                    self._appel()
                self.t[0] += oo.QUOTA_MOIS_PAUSE_S + 1
        self.assertEqual(sortie.getvalue().count("quota Helius du mois"), 1)

    def test_un_429_de_debit_ordinaire_ne_met_pas_en_pause(self):
        self.session.reponses = [Reponse(429, "rate limited"), Reponse(200)]
        self.assertIsNone(self._appel())
        self.assertEqual(self._appel(), {"ok": 1})


if __name__ == "__main__":
    unittest.main()
