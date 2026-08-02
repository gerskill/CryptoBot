"""Branchement des agents de mesure dans la boucle — bout en bout.

CE QUI EST VERROUILLÉ ICI, et pourquoi ça ne pouvait pas l'être par les tests
unitaires de chaque agent.

`PriceHistory` vit en MÉMOIRE : rien ne l'écrit sur disque, il disparaît à
chaque redémarrage. Les snapshots d'avant l'entrée n'existent donc que pendant
le cycle qui ouvre la position. Si `_measure_entry` était appelée ailleurs — au
prochain cycle, à la clôture, dans un rapport périodique — le contrefactuel de
timing n'aurait plus rien à reconstituer et journaliserait des `null` à vie.

Un journal de `null` se lit « pas d'avantage de timing », pas « pas de
données ». C'est le mode de panne silencieux que ce fichier interdit.
"""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from src.agents import (  # noqa: E402
    CounterfactualTimingAgent,
    DevHistoryAgent,
    MicrostructureAgent,
    RSIAgent,
    VolatilityAgent,
)
from src.agents.counterfactual_timing import SCAN_CYCLE_SECONDS  # noqa: E402
from src.analysis.technical import PriceHistory  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.main import AlphaLoop  # noqa: E402

CHEMINS = (
    "DEV_HISTORY_LOG_PATH", "RSI_LOG_PATH", "VOLATILITY_LOG_PATH",
    "MICROSTRUCTURE_LOG_PATH", "COUNTERFACTUAL_LOG_PATH",
)


class _Position:
    def __init__(self, prix: float):
        self.token_address = "mint"
        self.symbol = "TOK"
        self.entry_price = prix
        self.size_usd = 20.0


class MesureALEntree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = {k: getattr(settings, k) for k in CHEMINS}
        for cle in CHEMINS:
            setattr(settings, cle, os.path.join(self.tmp, f"{cle.lower()}.jsonl"))
        self.addCleanup(
            lambda: [setattr(settings, k, v) for k, v in self._saved.items()]
        )

        self.loop = type("L", (), {})()
        self.loop.history = PriceHistory()
        self.loop.dev_history = DevHistoryAgent(
            log_path=settings.DEV_HISTORY_LOG_PATH)
        self.loop.rsi = RSIAgent(log_path=settings.RSI_LOG_PATH)
        self.loop.volatility = VolatilityAgent(
            log_path=settings.VOLATILITY_LOG_PATH)
        self.loop.microstructure = MicrostructureAgent(
            log_path=settings.MICROSTRUCTURE_LOG_PATH)
        self.loop.counterfactual = CounterfactualTimingAgent(
            log_path=settings.COUNTERFACTUAL_LOG_PATH)

    def _historique(self, cycles: int = 40) -> None:
        """Un token suivi depuis `cycles` tours de scan, prix en hausse."""
        maintenant = time.time()
        for index in range(cycles):
            self.loop.history.record_raw(
                "mint",
                price=1.0 + 0.02 * index,
                liquidity=20000 + 100 * index,
                ts=maintenant - (cycles - index) * SCAN_CYCLE_SECONDS,
            )

    def _lignes(self, cle: str) -> list[dict]:
        chemin = getattr(settings, cle)
        if not os.path.exists(chemin):
            return []
        with open(chemin, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def _candidat(self, **kwargs) -> Candidate:
        base = dict(
            token_address="mint", symbol="TOK", name="", chain="solana",
            price_usd=1.8, liquidity_usd=24000.0,
        )
        base.update(kwargs)
        return Candidate(**base)

    def test_les_cinq_agents_ecrivent_une_ligne(self):
        self._historique()
        AlphaLoop._measure_entry(self.loop, _Position(1.80), self._candidat())
        for cle in CHEMINS:
            self.assertEqual(len(self._lignes(cle)), 1, cle)

    def test_le_contrefactuel_reconstitue_vraiment_les_decalages(self):
        """LE TEST QUI COMPTE. Avec les décalages d'origine (-30/-10/+10 s),
        plus fins que la cadence de scan, les trois sortaient `null`."""
        self._historique()
        AlphaLoop._measure_entry(self.loop, _Position(1.80), self._candidat())

        ligne = self._lignes("COUNTERFACTUAL_LOG_PATH")[0]
        self.assertTrue(
            all(v is not None for v in ligne["deltas_pct"].values()),
            f"décalages non reconstitués : {ligne['deltas_pct']}",
        )
        self.assertIsNotNone(ligne["best_edge_pct"])

    def test_sur_un_prix_qui_montait_entrer_plus_tot_etait_moins_cher(self):
        self._historique()
        AlphaLoop._measure_entry(self.loop, _Position(1.80), self._candidat())

        deltas = self._lignes("COUNTERFACTUAL_LOG_PATH")[0]["deltas_pct"]
        self.assertGreater(deltas["-270s"], deltas["-90s"])
        self.assertGreater(deltas["-270s"], 0)

    def test_sans_historique_les_agents_journalisent_leur_ignorance(self):
        """Ne rien écrire ferait disparaître le cas ; écrire zéro le
        travestirait en mesure."""
        AlphaLoop._measure_entry(self.loop, _Position(1.80), self._candidat())

        self.assertIsNone(self._lignes("RSI_LOG_PATH")[0]["rsi"])
        self.assertIsNone(
            self._lignes("VOLATILITY_LOG_PATH")[0]["volatility_hourly_pct"])
        self.assertIsNone(
            self._lignes("COUNTERFACTUAL_LOG_PATH")[0]["best_edge_pct"])

    def test_un_agent_qui_tombe_nemporte_pas_les_autres(self):
        """La position est DÉJÀ ouverte quand ces mesures tournent : la perdre
        de vue serait bien pire que perdre une mesure."""
        class Casse:
            def observe(self, *args, **kwargs):
                raise RuntimeError("boum")

        self._historique()
        self.loop.rsi = Casse()
        AlphaLoop._measure_entry(self.loop, _Position(1.80), self._candidat())

        self.assertEqual(len(self._lignes("COUNTERFACTUAL_LOG_PATH")), 1)
        self.assertEqual(len(self._lignes("DEV_HISTORY_LOG_PATH")), 1)

    def test_la_mesure_ne_peut_pas_annuler_la_position(self):
        """Aucun agent ne rend de verdict exploitable par l'appelant : avec 93
        trades clôturés, brancher une décision sur un indicateur neuf serait
        du surapprentissage."""
        self._historique()
        suspect = self._candidat(
            creator_token_status="creator_sell_all",
            twitter_create_token_count=11,
            rug_ratio=0.9, dev_wallet_pct=40.0,
        )
        self.assertIsNone(
            AlphaLoop._measure_entry(self.loop, _Position(1.80), suspect)
        )
        self.assertGreater(self._lignes("DEV_HISTORY_LOG_PATH")[0]["dev_score"], 45)


if __name__ == "__main__":
    unittest.main()
