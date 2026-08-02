"""Contrefactuel de timing — reconstitution honnête, jamais extrapolée.

CE QUI EST VERROUILLÉ ICI. Cet agent doit servir de PREUVE pour décider si
l'entrée est systématiquement trop tardive — 96 % des trades touchent -10 %,
creux médian -20,9 %. Une preuve construite sur des prix extrapolés ne vaut
rien, et pire, elle serait indiscernable d'une vraie.

Trois protections :
  1. hors de l'intervalle couvert par les snapshots, au-delà de `max_gap`, on
     rend `None` plutôt que le point le plus proche ;
  2. l'interpolation est signalée point par point (`interpolated`) ;
  3. un avantage inférieur à l'aller-retour médian mesuré (3,06 %) n'est pas
     « significatif » — il n'existe pas en pratique.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents import _journal  # noqa: E402
from src.agents.counterfactual_timing import (  # noqa: E402
    DEFAULT_OFFSETS,
    MEANINGFUL_EDGE_PCT,
    SCAN_CYCLE_SECONDS,
    CounterfactualTimingAgent,
    price_at,
    summarise,
)
from src.analysis.technical import Snapshot  # noqa: E402

T0 = 1_000_000.0


def _snaps(points):
    """`points` = liste de (offset depuis T0, prix)."""
    return [
        Snapshot(ts=T0 + offset, price=prix, volume_5m=100.0, liquidity=20000.0)
        for offset, prix in points
    ]


class TestReconstitution(unittest.TestCase):
    def test_un_instant_exact_nest_pas_interpole(self):
        prix, ecart, interpole = price_at(_snaps([(-30, 1.0), (0, 2.0)]), T0)
        self.assertEqual(prix, 2.0)
        self.assertFalse(interpole)
        self.assertEqual(ecart, 0.0)

    def test_entre_deux_snapshots_interpole_lineairement(self):
        prix, _, interpole = price_at(_snaps([(-20, 1.0), (+20, 3.0)]), T0)
        self.assertAlmostEqual(prix, 2.0)
        self.assertTrue(interpole)

    def test_hors_intervalle_mais_proche_prend_le_plus_proche(self):
        prix, ecart, interpole = price_at(_snaps([(0, 5.0)]), T0 - 20)
        self.assertEqual(prix, 5.0)
        self.assertAlmostEqual(ecart, 20.0)
        self.assertFalse(interpole)

    def test_hors_intervalle_et_loin_rend_none(self):
        """N'EXTRAPOLE PAS. Un prix de memecoin projeté sur une minute est un
        chiffre inventé, et cet agent doit rester utilisable comme preuve."""
        prix, ecart, _ = price_at(_snaps([(0, 5.0)]), T0 - 300)
        self.assertIsNone(prix)
        self.assertAlmostEqual(ecart, 300.0)

    def test_sans_snapshot_rend_none_sans_lever(self):
        self.assertEqual(price_at([], T0), (None, None, False))

    def test_les_snapshots_desordonnes_sont_tries(self):
        prix, _, _ = price_at(_snaps([(+20, 3.0), (-20, 1.0)]), T0)
        self.assertAlmostEqual(prix, 2.0)


class TestCadence(unittest.TestCase):
    """LE DÉFAUT TROUVÉ DE BOUT EN BOUT, avant toute mise en service.

    La spec demandait des décalages de -30 s, -10 s et +10 s. Ils sont PLUS
    FINS QUE L'ÉCHANTILLONNAGE : avant l'ouverture d'une position, le seul
    historique vient du scan, qui tourne à 90 s. Au moment d'entrer, le dernier
    snapshot a déjà entre 0 et 90 s — donc t-30 s tombait systématiquement
    au-delà de la tolérance et l'agent rendait `null` sur les trois décalages.

    Un journal rempli de `null` se serait lu « pas d'avantage de timing ».
    """

    def test_les_decalages_sont_des_multiples_du_cycle(self):
        for offset in DEFAULT_OFFSETS:
            self.assertAlmostEqual(abs(offset) % SCAN_CYCLE_SECONDS, 0.0)

    def test_aucun_decalage_nest_plus_fin_que_lechantillonnage(self):
        for offset in DEFAULT_OFFSETS:
            self.assertGreaterEqual(abs(offset), SCAN_CYCLE_SECONDS)

    def test_aucun_decalage_positif_a_louverture(self):
        """« Et si on entrait plus tard ? » est dans le futur au moment
        d'entrer. Ça se mesure depuis le monitoring, pas d'ici."""
        for offset in DEFAULT_OFFSETS:
            self.assertLess(offset, 0)

    def test_le_cas_reel_rend_des_valeurs_et_non_des_nulls(self):
        """Reproduction exacte du bug : snapshots au cycle, entrée juste après
        le dernier. Avec les décalages d'origine, tout sortait `null`."""
        snaps = [
            Snapshot(ts=T0 - i * SCAN_CYCLE_SECONDS, price=2.0 - 0.02 * i,
                     volume_5m=100.0, liquidity=20000.0)
            for i in range(1, 12)
        ]
        verdict = CounterfactualTimingAgent().replay("addr", "TOK", 2.0, T0, snaps)
        self.assertTrue(
            any(v is not None for v in verdict.deltas_pct.values()),
            f"tous nuls : {verdict.deltas_pct}",
        )
        self.assertIsNotNone(verdict.best_edge_pct)


class TestRejeu(unittest.TestCase):
    def test_un_prix_qui_montait_donne_un_avantage_positif(self):
        """Entrée à 1,20 alors que le token valait 1,00 trois cycles plus
        tôt : entrer plus tôt aurait fait économiser ~17 %."""
        agent = CounterfactualTimingAgent()
        verdict = agent.replay(
            "addr", "TOK", entry_price=1.20, entry_ts=T0,
            snapshots=_snaps([(-270, 1.00), (-180, 1.05), (-90, 1.10), (0, 1.20)]),
        )
        self.assertGreater(verdict.deltas_pct["-270s"], 0)
        self.assertGreater(verdict.deltas_pct["-270s"], verdict.deltas_pct["-90s"])
        self.assertEqual(verdict.best_offset_sec, -270.0)

    def test_le_signe_dit_bien_moins_cher(self):
        agent = CounterfactualTimingAgent()
        verdict = agent.replay(
            "addr", "TOK", entry_price=1.00, entry_ts=T0,
            snapshots=_snaps([(-270, 2.00), (-180, 1.60), (-90, 1.30), (0, 1.00)]),
        )
        # Le token BAISSAIT : entrer plus tôt aurait coûté plus cher.
        self.assertLess(verdict.deltas_pct["-270s"], 0)
        self.assertLess(verdict.deltas_pct["-90s"], 0)

    def test_un_avantage_sous_le_cout_nest_pas_significatif(self):
        """Un gain de timing inférieur à l'aller-retour médian n'existe pas
        en pratique."""
        agent = CounterfactualTimingAgent()
        verdict = agent.replay(
            "addr", "TOK", entry_price=1.01, entry_ts=T0,
            snapshots=_snaps([(-270, 1.00), (-180, 1.00), (-90, 1.00), (0, 1.01)]),
        )
        self.assertLess(verdict.best_edge_pct, MEANINGFUL_EDGE_PCT)
        self.assertFalse(verdict.meaningful)

    def test_un_avantage_au_dessus_du_cout_est_significatif(self):
        agent = CounterfactualTimingAgent()
        verdict = agent.replay(
            "addr", "TOK", entry_price=1.20, entry_ts=T0,
            snapshots=_snaps([(-270, 1.00), (-180, 1.00), (-90, 1.00), (0, 1.20)]),
        )
        self.assertTrue(verdict.meaningful)

    def test_historique_absent_rend_un_verdict_vide_et_le_dit(self):
        agent = CounterfactualTimingAgent()
        verdict = agent.replay("addr", "TOK", 1.0, T0, [])
        self.assertIsNone(verdict.best_edge_pct)
        self.assertFalse(verdict.meaningful)
        self.assertIn("trop clairsemé", verdict.reason)

    def test_un_prix_dentree_nul_ne_divise_pas_par_zero(self):
        verdict = CounterfactualTimingAgent().replay(
            "addr", "TOK", entry_price=0.0, entry_ts=T0,
            snapshots=_snaps([(-30, 1.0), (0, 1.0)]),
        )
        self.assertTrue(all(v is None for v in verdict.deltas_pct.values()))


class TestSynthese(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "counterfactual_log.jsonl")

    def test_les_echecs_de_reconstitution_sont_journalises(self):
        """Sans eux, un journal vide se lirait « pas d'avantage de timing » au
        lieu de « pas de données »."""
        agent = CounterfactualTimingAgent(log_path=self.log)
        agent.observe("addr", "TOK", 1.0, T0, [])
        rows = _journal.read(self.log)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["best_edge_pct"])

    def test_la_synthese_rend_une_mediane_pas_une_moyenne(self):
        """Un token à +400 % en dix secondes écraserait toute moyenne — et
        c'est exactement le régime de queue de ce marché."""
        rows = [
            {"deltas_pct": {"-30s": 1.0}},
            {"deltas_pct": {"-30s": 2.0}},
            {"deltas_pct": {"-30s": 3.0}},
            {"deltas_pct": {"-30s": 400.0}},
            {"deltas_pct": {"-30s": 2.5}},
        ]
        resume = summarise(rows)
        self.assertEqual(resume["offsets"]["-30s"]["n"], 5)
        self.assertEqual(resume["offsets"]["-30s"]["median_edge_pct"], 2.5)

    def test_la_synthese_compte_la_part_au_dessus_du_cout(self):
        rows = [{"deltas_pct": {"-30s": v}} for v in (1.0, 5.0, 6.0, 0.5)]
        resume = summarise(rows)
        self.assertEqual(resume["offsets"]["-30s"]["share_above_cost"], 50.0)

    def test_la_synthese_ignore_les_valeurs_absentes(self):
        rows = [{"deltas_pct": {"-30s": None}}, {"deltas_pct": {"-30s": 4.0}}]
        self.assertEqual(summarise(rows)["offsets"]["-30s"]["n"], 1)

    def test_la_synthese_porte_ses_reserves(self):
        """La note n'est pas décorative : sans elle, le chiffre serait lu comme
        un gain réalisable."""
        resume = summarise([{"deltas_pct": {"-30s": 4.0}}])
        self.assertIn("SUPÉRIEURE", resume["note"])
        self.assertEqual(resume["cost_threshold_pct"], MEANINGFUL_EDGE_PCT)


if __name__ == "__main__":
    unittest.main()
