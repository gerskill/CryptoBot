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
        self.id = "pos-1"
        self.high_water_pct = 42.0

    def duration_minutes(self) -> float:
        return 31.5


class _Loop:
    """Boucle minimale portant les VRAIES méthodes de mesure.

    Emprunter `_run_agents` plutôt que le réécrire : c'est lui qui isole les
    pannes d'agent, et un test qui le simulerait ne testerait pas la protection
    réellement en place.
    """

    _run_agents = AlphaLoop._run_agents
    _measure_entry = AlphaLoop._measure_entry
    _measure_hold = AlphaLoop._measure_hold


class MesureBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._saved = {k: getattr(settings, k) for k in CHEMINS}
        for cle in CHEMINS:
            setattr(settings, cle, os.path.join(self.tmp, f"{cle.lower()}.jsonl"))
        self.addCleanup(
            lambda: [setattr(settings, k, v) for k, v in self._saved.items()]
        )

        self.loop = _Loop()
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

    def _detention(self, minutes: float = 30.0, tick: float = 5.0) -> None:
        """Snapshots du monitoring : cadence 5 s, prix qui oscille."""
        maintenant = time.time()
        points = int(minutes * 60 / tick)
        for index in range(points):
            self.loop.history.record_raw(
                "mint",
                price=1.80 * (1 + 0.01 * (1 if index % 2 else -1)),
                liquidity=24000 + 10 * index,
                ts=maintenant - (points - index) * tick,
            )


class MesureALEntree(MesureBase):
    """Ce qui n'a de sens QU'À L'OUVERTURE : contrefactuel et dev_history."""

    def test_seuls_les_agents_dentree_ecrivent(self):
        """RSI, volatilité et microstructure ont été DÉPLACÉS à la clôture.
        Mesuré sur les deux premières entrées réelles : 1 et 3 clôtures pour un
        RSI qui en demande 15, 1 et 5 rendements pour une volatilité qui en
        demande 8. Le bot entre vite, `PriceHistory` est presque vide."""
        self._historique()
        self.loop._measure_entry(_Position(1.80), self._candidat())

        self.assertEqual(len(self._lignes("COUNTERFACTUAL_LOG_PATH")), 1)
        self.assertEqual(len(self._lignes("DEV_HISTORY_LOG_PATH")), 1)
        for cle in ("RSI_LOG_PATH", "VOLATILITY_LOG_PATH", "MICROSTRUCTURE_LOG_PATH"):
            self.assertEqual(self._lignes(cle), [], cle)

    def test_le_contrefactuel_reconstitue_vraiment_les_decalages(self):
        """LE TEST QUI COMPTE. Avec les décalages d'origine (-30/-10/+10 s),
        plus fins que la cadence de scan, les trois sortaient `null`."""
        self._historique()
        self.loop._measure_entry(_Position(1.80), self._candidat())

        ligne = self._lignes("COUNTERFACTUAL_LOG_PATH")[0]
        self.assertTrue(
            all(v is not None for v in ligne["deltas_pct"].values()),
            f"décalages non reconstitués : {ligne['deltas_pct']}",
        )
        self.assertIsNotNone(ligne["best_edge_pct"])

    def test_sur_un_prix_qui_montait_entrer_plus_tot_etait_moins_cher(self):
        self._historique()
        self.loop._measure_entry(_Position(1.80), self._candidat())

        deltas = self._lignes("COUNTERFACTUAL_LOG_PATH")[0]["deltas_pct"]
        self.assertGreater(deltas["-270s"], deltas["-90s"])
        self.assertGreater(deltas["-270s"], 0)

    def test_sans_historique_lignorance_est_journalisee(self):
        """Ne rien écrire ferait disparaître le cas ; écrire zéro le
        travestirait en mesure."""
        self.loop._measure_entry(_Position(1.80), self._candidat())
        self.assertIsNone(
            self._lignes("COUNTERFACTUAL_LOG_PATH")[0]["best_edge_pct"])

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
        self.assertIsNone(self.loop._measure_entry(_Position(1.80), suspect))
        self.assertGreater(self._lignes("DEV_HISTORY_LOG_PATH")[0]["dev_score"], 45)


class MesureSurLaDetention(MesureBase):
    """Ce qui mesure une ACCUMULATION : à la clôture, pas à l'ouverture."""

    def _bras(self):
        return type("A", (), {"name": "sniper"})()

    def _row(self):
        return {"position_id": "pos-1", "pnl_pct": -12.3, "is_final_exit": True}

    def test_la_volatilite_devient_mesurable(self):
        """LE TEST QUI JUSTIFIE LE DÉPLACEMENT. À l'ouverture elle voyait 1 à 5
        rendements pour un minimum de 8. Sur 30 min à 5 s par tick, ~360."""
        self._detention()
        self.loop._measure_hold(self._bras(), _Position(1.80), self._row())

        ligne = self._lignes("VOLATILITY_LOG_PATH")[0]
        self.assertIsNotNone(ligne["volatility_hourly_pct"])
        self.assertGreater(ligne["samples"], 100)

    def test_la_microstructure_devient_mesurable(self):
        self._detention()
        self.loop._measure_hold(self._bras(), _Position(1.80), self._row())

        ligne = self._lignes("MICROSTRUCTURE_LOG_PATH")[0]
        self.assertIsNotNone(ligne["liquidity_drift_pct"])

    def test_les_lignes_se_joignent_au_journal_de_trades(self):
        """Une volatilité sans son P&L ne dit rien : c'est leur mise en regard
        qui répondra à « le stop est-il trop serré pour ce régime ? »."""
        self._detention()
        self.loop._measure_hold(self._bras(), _Position(1.80), self._row())

        for cle in ("VOLATILITY_LOG_PATH", "MICROSTRUCTURE_LOG_PATH", "RSI_LOG_PATH"):
            ligne = self._lignes(cle)[0]
            self.assertEqual(ligne["position_id"], "pos-1", cle)
            self.assertEqual(ligne["arm"], "sniper", cle)
            self.assertEqual(ligne["pnl_pct"], -12.3, cle)
            self.assertEqual(ligne["peak_pct"], 42.0, cle)

    def test_une_detention_courte_laisse_le_rsi_inconnu(self):
        """Les bougies font 180 s : 15 clôtures demandent 45 min. Sur une
        position courte, « inconnu » est la réponse honnête plutôt qu'un RSI
        calculé sur trois points."""
        self._detention(minutes=4.0)
        self.loop._measure_hold(self._bras(), _Position(1.80), self._row())

        ligne = self._lignes("RSI_LOG_PATH")[0]
        self.assertIsNone(ligne["rsi"])
        self.assertEqual(ligne["zone"], "inconnu")

    def test_un_agent_qui_tombe_nemporte_pas_les_autres(self):
        """Le trade est DÉJÀ écrit au journal quand ces mesures tournent :
        perdre la boucle de vue serait bien pire que perdre une mesure."""
        class Casse:
            def observe(self, *args, **kwargs):
                raise RuntimeError("boum")

        self._detention()
        self.loop.volatility = Casse()
        self.loop._measure_hold(self._bras(), _Position(1.80), self._row())

        self.assertEqual(len(self._lignes("MICROSTRUCTURE_LOG_PATH")), 1)
        self.assertEqual(len(self._lignes("RSI_LOG_PATH")), 1)


if __name__ == "__main__":
    unittest.main()
