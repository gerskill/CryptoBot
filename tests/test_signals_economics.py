"""Vote avec abstention, économie du trade, tableau de bord des agents.

Trois pièges verrouillés ici, chacun tiré d'une mesure réelle :

  - un quorum « 3 sur 5 » se casse dès qu'un agent perd son API ; le quorum
    doit porter sur les VOTANTS PRÉSENTS ;
  - un TP n'est pas un choix mais une conséquence du coût d'entrée : mesuré,
    l'aller-retour va de 1,85 % à 23,62 % selon le token ;
  - un agent qui refuse tout a une spécificité parfaite et une utilité nulle.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import economics  # noqa: E402
from src.core.scoreboard import (  # noqa: E402
    MIN_SIGNALS_PER_AGENT,
    MIN_TRADES_FOR_WEIGHTS,
    AgentScoreboard,
)
from src.core.signals import Signal, SignalLog, Vote, abstain, aggregate  # noqa: E402


def yes(agent, reason="ok"):
    return Signal(agent=agent, vote=Vote.YES, reason=reason)


def no(agent, reason="non", veto=False):
    return Signal(agent=agent, vote=Vote.NO, reason=reason, veto=veto)


class TestAbstention(unittest.TestCase):
    def test_le_quorum_porte_sur_les_votants_presents(self):
        # 2 oui / 1 non / 2 abstentions : 2 sur 3 présents = 67% >= 60%.
        verdict = aggregate(
            [yes("liq"), yes("tech"), no("smart"), abstain("social", "API morte"),
             abstain("safety", "pas de données")],
            min_ratio=0.6,
        )
        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.voters, 3)
        self.assertEqual(verdict.abstained, 2)

    def test_une_api_morte_ne_devient_pas_un_veto(self):
        # Le bug qu'on évite : avec « 3 sur 5 » en dur, deux agents muets
        # rendent toute entrée impossible pour toujours.
        signals = [yes("liq"), yes("tech"), abstain("a", "hs"), abstain("b", "hs"),
                   abstain("c", "hs")]
        self.assertTrue(aggregate(signals, min_ratio=0.6).passed)

    def test_abstention_totale_ne_laisse_pas_decider_un_seul(self):
        verdict = aggregate(
            [yes("liq"), abstain("a", "hs"), abstain("b", "hs")],
            min_ratio=0.6, min_voters=2,
        )
        self.assertFalse(verdict.passed)
        self.assertIn("quorum introuvable", verdict.reason)

    def test_le_refus_par_quorum_se_distingue_du_refus_motive(self):
        sans_quorum = aggregate([abstain("a", "hs")], min_ratio=0.6, min_voters=2)
        motive = aggregate([no("safety", "top holder 40%"), yes("liq")], min_ratio=0.6)
        self.assertIn("quorum introuvable", sans_quorum.reason)
        self.assertIn("top holder 40%", motive.reason)

    def test_le_veto_securite_court_circuite_le_quorum(self):
        verdict = aggregate(
            [yes("liq"), yes("tech"), yes("smart"), no("safety", "honeypot", veto=True)],
            min_ratio=0.6,
        )
        self.assertFalse(verdict.passed)
        self.assertIn("veto sécurité", verdict.reason)
        self.assertIn("honeypot", verdict.reason)

    def test_un_veto_ne_sapplique_pas_a_un_oui(self):
        # `veto` marque un agent habilité, pas n'importe quel avis.
        verdict = aggregate(
            [Signal("safety", Vote.YES, "propre", veto=True), yes("liq")], min_ratio=0.6
        )
        self.assertTrue(verdict.passed)

    def test_les_dissidents_sont_traçables(self):
        verdict = aggregate([yes("liq"), yes("tech"), no("smart", "aucun achat")],
                            min_ratio=0.6)
        self.assertTrue(verdict.passed)
        self.assertEqual([s.agent for s in verdict.dissent], ["smart"])

    def test_aucun_signal_ne_passe_pas(self):
        self.assertFalse(aggregate([], min_ratio=0.6).passed)


class TestSignalLog(unittest.TestCase):
    def test_journalise_par_token_et_rend_le_verdict(self):
        log = SignalLog()
        log.record("addr", yes("liq"))
        log.record("addr", no("safety", "dev vend"))
        log.record("addr", yes("tech"))
        self.assertTrue(log.verdict("addr", min_ratio=0.6).passed)
        self.assertEqual(len(log.payload("addr")), 3)

    def test_token_inconnu_ne_passe_pas(self):
        self.assertFalse(SignalLog().verdict("jamais vu").passed)


class TestEconomie(unittest.TestCase):
    def test_le_plancher_de_tp_monte_avec_les_frais(self):
        bas = economics.minimum_viable_tp(2.0, win_rate=0.3)
        haut = economics.minimum_viable_tp(23.6, win_rate=0.3)
        self.assertGreater(haut, bas * 5)

    def test_un_petit_tp_est_refuse_sur_un_carnet_mince(self):
        # PWEACEJIMO : 23,62 % d'aller-retour mesuré. Aucun « petit gain »
        # n'existe sur ce token, quel que soit le signal.
        verdict = economics.evaluate(
            round_trip_pct=23.62, take_profit_pct=25, win_rate=0.31
        )
        self.assertFalse(verdict.viable)
        self.assertIn("aller-retour", verdict.reason)

    def test_le_meme_tp_passe_sur_un_carnet_profond(self):
        # Buddy : 1,85 % mesuré.
        verdict = economics.evaluate(
            round_trip_pct=1.85, take_profit_pct=25, win_rate=0.31
        )
        self.assertTrue(verdict.viable, verdict.reason)
        self.assertGreater(verdict.expected_net_pct, 0)

    def test_cout_non_mesure_ne_bloque_pas(self):
        # Même invariant que le pipeline : une donnée absente ne rejette pas.
        verdict = economics.evaluate(None, take_profit_pct=25, win_rate=0.31)
        self.assertTrue(verdict.viable)
        self.assertIn("non mesuré", verdict.reason)

    def test_mais_non_mesure_se_distingue_de_gratuit(self):
        self.assertIsNone(economics.evaluate(None, 25, 0.31).round_trip_pct)

    def test_esperance_negative_quand_les_frais_mangent_le_gain(self):
        net = economics.expected_net_pct(
            take_profit_pct=10, win_rate=0.3, round_trip_pct=5.0
        )
        self.assertLess(net, 0)

    def test_un_tp_plus_haut_compense_des_frais_plus_lourds(self):
        self.assertGreater(
            economics.expected_net_pct(150, 0.15, 2.0),
            economics.expected_net_pct(25, 0.15, 2.0),
        )


class FakeJupiter:
    """Coût qui décroît quand la taille baisse — comportement réel d'un carnet."""

    enabled = True

    def __init__(self, costs):
        self.costs = costs
        self.calls = 0

    def round_trip_cost_pct(self, mint, size_usd, sol_price_usd=0.0):
        self.calls += 1
        return self.costs.pop(0) if self.costs else None


class TestSizingParLeCout(unittest.TestCase):
    def test_reduit_la_taille_plutot_que_de_renoncer(self):
        # Plus juste que « rejeter si slippage > 2 % » : sur un carnet mince
        # une position deux fois plus petite coûte souvent bien moins de la
        # moitié.
        taille, cost, raison = economics.size_for_cost(
            FakeJupiter([9.0, 3.0]), "mint", 20.0, max_round_trip_pct=4.0
        )
        self.assertEqual(taille, 10.0)
        self.assertEqual(cost, 3.0)
        self.assertIn("réduite", raison)

    def test_garde_la_taille_pleine_si_le_cout_passe(self):
        taille, cost, _ = economics.size_for_cost(
            FakeJupiter([1.5]), "mint", 20.0, max_round_trip_pct=4.0
        )
        self.assertEqual(taille, 20.0)
        self.assertEqual(cost, 1.5)

    def test_abandonne_si_meme_le_quart_coute_trop(self):
        taille, _, raison = economics.size_for_cost(
            FakeJupiter([30.0, 28.0, 25.0]), "mint", 20.0, max_round_trip_pct=4.0
        )
        self.assertEqual(taille, 0.0)
        self.assertIn("abandon", raison)

    def test_jupiter_absent_ne_bloque_pas(self):
        taille, cost, _ = economics.size_for_cost(None, "mint", 20.0, 4.0)
        self.assertEqual(taille, 20.0)
        self.assertIsNone(cost)


class TestTableauDeBordAgents(unittest.TestCase):
    def test_un_agent_qui_refuse_tout_a_une_utilite_nulle(self):
        # Spécificité parfaite, précision inexistante : le déséquilibre doit
        # être visible, sinon un agent inutile passe pour excellent.
        board = AgentScoreboard()
        for _ in range(25):
            board.record_rejection([{"agent": "peureux", "vote": "no"}], False)
        score = board.scores()[0]
        self.assertEqual(score.specificity, 100.0)
        self.assertIsNone(score.precision)
        self.assertIn("jamais tranché", score.verdict)

    def test_mesure_les_faux_negatifs_via_le_shadow(self):
        # Sans le shadow log on ne mesurerait que les trades pris, et refuser
        # tout donnerait un sans-faute.
        board = AgentScoreboard()
        for _ in range(10):
            board.record_rejection([{"agent": "strict", "vote": "no"}], True)
        for _ in range(10):
            board.record_trade([{"agent": "strict", "vote": "yes"}], True)
        score = board.scores()[0]
        self.assertEqual(score.false_negative, 10)
        self.assertEqual(score.true_positive, 10)

    def test_labstention_ne_compte_pas_comme_une_erreur(self):
        board = AgentScoreboard()
        for _ in range(10):
            board.record_trade([{"agent": "muet", "vote": "abstain"}], False)
        score = board.scores()[0]
        self.assertEqual(score.abstained, 10)
        self.assertEqual(score.sample, 0)

    def test_les_poids_ne_bougent_pas_sous_le_seuil(self):
        board = AgentScoreboard()
        for _ in range(30):
            board.record_trade([{"agent": "a", "vote": "yes"}], True)
        allowed, why = board.weights_allowed(total_trades=36)
        self.assertFalse(allowed)
        self.assertIn(str(MIN_TRADES_FOR_WEIGHTS), why)

    def test_les_poids_sont_autorises_avec_assez_de_donnees(self):
        board = AgentScoreboard()
        for _ in range(MIN_SIGNALS_PER_AGENT + 5):
            board.record_trade([{"agent": "a", "vote": "yes"}], True)
        allowed, _ = board.weights_allowed(total_trades=MIN_TRADES_FOR_WEIGHTS + 1)
        self.assertTrue(allowed)

    def test_un_agent_sous_echantillonne_est_signale_comme_tel(self):
        board = AgentScoreboard()
        board.record_trade([{"agent": "neuf", "vote": "yes"}], True)
        self.assertIn("trop court", board.scores()[0].verdict)

    def test_serialisable_pour_le_dashboard(self):
        board = AgentScoreboard()
        board.record_trade([{"agent": "a", "vote": "yes"}], True)
        payload = board.as_dict(total_trades=10)
        self.assertFalse(payload["weights_allowed"])
        self.assertEqual(payload["agents"][0]["agent"], "a")


if __name__ == "__main__":
    unittest.main(verbosity=2)
