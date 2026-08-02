"""Historique du créateur — accumulation de signaux, jamais un seul.

CE QUI EST VERROUILLÉ ICI. Le risque de cet agent n'est pas de rater un rugger,
c'est de rejeter un token honnête sur une donnée manquante ou sur un signal
isolé. Trois protections, testées une par une :

  1. aucun signal seul n'atteint le seuil de rejet — il faut une concordance ;
  2. un score inconnu ne rejette jamais (invariant du pipeline) ;
  3. une couverture insuffisante ne rejette pas non plus, parce qu'un score
     porté par un signal sur six n'est pas une mesure. C'est le même
     raisonnement que `sub_scores._weights_used` dans le scoring : savoir sur
     quelle fraction on a réellement mesuré.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents import _journal  # noqa: E402
from src.agents.dev_history import POIDS, DevHistoryAgent  # noqa: E402
from src.core.models import Candidate  # noqa: E402


def _candidate(**kwargs):
    base = dict(
        token_address="addr", symbol="TOK", name="Token", chain="solana",
        price_usd=1.0, liquidity_usd=20000.0,
    )
    base.update(kwargs)
    return Candidate(**base)


PROPRE = dict(
    creator_token_status="creator_hold",
    twitter_create_token_count=0,
    rug_ratio=0.0,
    dev_wallet_pct=1.0,
    bundler_rate=0.0,
    insider_rate=0.0,
)


class TestScore(unittest.TestCase):
    def test_un_createur_propre_sort_a_zero(self):
        verdict = DevHistoryAgent().score_candidate(_candidate(**PROPRE))
        self.assertEqual(verdict.score, 0.0)
        self.assertEqual(verdict.signals, ())
        self.assertEqual(verdict.coverage, 1.0)

    def test_tous_les_signaux_saturent_a_cent(self):
        pire = dict(
            creator_token_status="creator_sell_all",
            twitter_create_token_count=11,
            rug_ratio=0.9,
            dev_wallet_pct=40.0,
            bundler_rate=0.8,
            insider_rate=0.5,
        )
        verdict = DevHistoryAgent().score_candidate(_candidate(**pire))
        self.assertEqual(verdict.score, 100.0)
        self.assertEqual(len(verdict.signals), len(POIDS))

    def test_aucun_signal_seul_ne_declenche_le_rejet(self):
        """Un dev peut détenir 12 % pour de bonnes raisons, un compte peut
        avoir lancé trois tokens honnêtes. C'est l'accumulation qui discrimine."""
        agent = DevHistoryAgent()
        for cle, valeur in (
            ("creator_token_status", "creator_sell_all"),
            ("twitter_create_token_count", 11),
            ("rug_ratio", 0.9),
            ("dev_wallet_pct", 40.0),
            ("bundler_rate", 0.8),
            ("insider_rate", 0.5),
        ):
            champs = dict(PROPRE)
            champs[cle] = valeur
            self.assertIsNone(
                agent.rejection_reason(_candidate(**champs)),
                f"{cle} seul ne doit pas rejeter",
            )

    def test_deux_signaux_forts_declenchent_le_rejet(self):
        """Le créateur a vendu ET le compte a lancé onze tokens : 46,4 % sur
        une couverture complète, au-dessus du seuil de 45."""
        champs = dict(PROPRE)
        champs["creator_token_status"] = "creator_sell_all"
        champs["twitter_create_token_count"] = 11
        motif = DevHistoryAgent().rejection_reason(_candidate(**champs))
        self.assertIsNotNone(motif)
        self.assertIn("créateur suspect", motif)

    def test_les_trois_signaux_faibles_cumules_ne_rejettent_pas(self):
        """Détention du dev, bundler et insiders ont DÉJÀ leurs propres filtres
        au manifeste. Les compter deux fois serait un doublon silencieux."""
        champs = dict(PROPRE)
        champs["dev_wallet_pct"] = 40.0
        champs["bundler_rate"] = 0.8
        champs["insider_rate"] = 0.5
        verdict = DevHistoryAgent().score_candidate(_candidate(**champs))
        self.assertEqual(len(verdict.signals), 3)
        self.assertIsNone(DevHistoryAgent().rejection_reason(_candidate(**champs)))

    def test_le_score_est_rapporte_aux_signaux_presents(self):
        """Rapporté au total théorique, un token dont UN seul signal est connu
        et alarmant sortirait un score faible — l'absence de données
        ressemblerait à de la propreté."""
        verdict = DevHistoryAgent().score_candidate(
            _candidate(creator_token_status="creator_sell_all")
        )
        self.assertEqual(verdict.score, 100.0)
        self.assertLess(verdict.coverage, 0.5)


class TestDonneesAbsentes(unittest.TestCase):
    def test_aucun_signal_disponible_rend_inconnu_et_pas_zero(self):
        verdict = DevHistoryAgent().score_candidate(_candidate())
        self.assertFalse(verdict.known)
        self.assertIsNone(verdict.score)
        self.assertIn("non mesuré", verdict.reason)

    def test_un_score_inconnu_ne_rejette_jamais(self):
        self.assertIsNone(DevHistoryAgent().rejection_reason(_candidate()))

    def test_une_couverture_insuffisante_ne_rejette_pas(self):
        """Score de 100 sur un seul signal sur six : alarmant, pas concluant."""
        candidat = _candidate(creator_token_status="creator_sell_all")
        verdict = DevHistoryAgent().score_candidate(candidat)
        self.assertEqual(verdict.score, 100.0)
        self.assertIsNone(DevHistoryAgent().rejection_reason(candidat))

    def test_une_couverture_suffisante_laisse_rejeter(self):
        champs = {
            "creator_token_status": "creator_sell_all",
            "twitter_create_token_count": 11,
            "rug_ratio": 0.9,
            "dev_wallet_pct": 1.0,
        }
        verdict = DevHistoryAgent().score_candidate(_candidate(**champs))
        self.assertGreaterEqual(verdict.coverage, 0.5)
        self.assertIsNotNone(DevHistoryAgent().rejection_reason(_candidate(**champs)))

    def test_creator_hold_nest_pas_creator_sell(self):
        """La détection porte sur « sell » dans le statut : `creator_hold` ne
        doit pas déclencher parce qu'il contient d'autres lettres."""
        verdict = DevHistoryAgent().score_candidate(
            _candidate(creator_token_status="creator_hold")
        )
        self.assertNotIn("creator_sold", verdict.signals)


class TestJournal(unittest.TestCase):
    def test_observe_journalise_meme_un_verdict_inconnu(self):
        tmp = tempfile.mkdtemp()
        log = os.path.join(tmp, "dev_history_log.jsonl")
        DevHistoryAgent(log_path=log).observe(_candidate())
        rows = _journal.read(log)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["dev_score"])
        self.assertEqual(rows[0]["coverage"], 0.0)


if __name__ == "__main__":
    unittest.main()
