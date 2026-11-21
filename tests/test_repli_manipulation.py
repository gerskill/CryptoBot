"""Signaux de manipulation GMGN perdus ou absents (bundlers, insiders, snipers, entrapment).

Deux défauts mesurés le 2026-11-21 (entrées OGTA : tous ces champs à `None`) :
  1. `_merge` gardait la fiche DexScreener d'un doublon et ne lui greffait de GMGN que holders,
     concentration et smart money : les signaux de manipulation, déjà payés par l'appel de
     découverte, étaient JETÉS ;
  2. un token vu par DexScreener mais absent du classement GMGN (filtré côté serveur, justement
     peut-être sur `max_bundler_rate`) arrivait sans aucun signal et passait les filtres.
Repli : `gmgn token info` (1 appel par token, en cache 120 s), plafonné par cycle.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pipeline  # noqa: E402
from src.core.entry_context import build_entry_context  # noqa: E402
from src.core.models import Candidate  # noqa: E402

INFO = {
    "holder_count": 812,
    "stat": {"top_bundler_trader_percentage": 0.31, "top_rat_trader_percentage": 0.12,
             "top_entrapment_trader_percentage": 0.04, "bot_degen_count": 7},
    "wallet_tags_stat": {"sniper_wallets": 14},
}


def candidat(adresse="mint1", **champs):
    base = {"token_address": adresse, "symbol": "TOK", "name": "TOK", "chain": "solana",
            "price_usd": 1.0, "liquidity_usd": 50000, "volume_1h": 20000}
    base.update(champs)
    return Candidate(**base)


class FauxGmgn:
    enabled = True

    def __init__(self, info=INFO, bloque=0.0):
        self.info = info
        self.appels = []
        self.bloque = bloque

    def token_info(self, adresse):
        self.appels.append(adresse)
        return self.info

    def blocked_seconds_remaining(self):
        return self.bloque


def pipeline_avec(gmgn):
    p = object.__new__(pipeline.ScanPipeline)
    p.gmgn = gmgn
    return p


class TestFusion(unittest.TestCase):
    def test_un_doublon_garde_les_signaux_de_manipulation_de_gmgn(self):
        dex = candidat(pair_address="pair1")
        gmgn = candidat(bundler_rate=0.2, insider_rate=0.05, sniper_count=3, entrapment_ratio=0.01,
                        rug_ratio=0.1, is_wash_trading=False, manipulation_source="gmgn_trending")
        fusion = pipeline.ScanPipeline._merge(None, [dex], [gmgn])[0]
        self.assertEqual(fusion.pair_address, "pair1")  # DexScreener reste la base
        self.assertEqual((fusion.bundler_rate, fusion.insider_rate, fusion.sniper_count),
                         (0.2, 0.05, 3))
        self.assertEqual(fusion.entrapment_ratio, 0.01)
        self.assertIs(fusion.is_wash_trading, False)
        self.assertEqual(fusion.manipulation_source, "gmgn_trending")

    def test_une_valeur_deja_connue_n_est_pas_ecrasee(self):
        dex = candidat(sniper_count=9)
        gmgn = candidat(sniper_count=3)
        self.assertEqual(pipeline.ScanPipeline._merge(None, [dex], [gmgn])[0].sniper_count, 9)

    def test_un_token_seulement_chez_gmgn_est_garde_tel_quel(self):
        gmgn = candidat("mint2", bundler_rate=0.2)
        fusion = pipeline.ScanPipeline._merge(None, [candidat()], [gmgn])
        self.assertEqual({c.token_address for c in fusion}, {"mint1", "mint2"})


class TestConversionTrending(unittest.TestCase):
    def test_la_source_est_notee_quand_gmgn_donne_un_signal(self):
        ligne = {"address": "mint1", "symbol": "TOK", "liquidity": 50000, "volume": 20000,
                 "price": 1, "open_timestamp": 0, "bundler_rate": 0.2}
        c = pipeline.gmgn_to_candidate(ligne)
        if c is not None:  # la conversion peut écarter une ligne incomplète ; ici elle doit passer
            self.assertEqual(c.manipulation_source, "gmgn_trending")


class TestLectureTokenInfo(unittest.TestCase):
    def test_champs_lus(self):
        champs = pipeline.manipulation_depuis_token_info(INFO)
        self.assertEqual(champs["bundler_rate"], 0.31)
        self.assertEqual(champs["insider_rate"], 0.12)
        self.assertEqual(champs["entrapment_ratio"], 0.04)
        self.assertEqual(champs["sniper_count"], 14)
        self.assertEqual(champs["bot_degen_count"], 7)
        self.assertEqual(champs["holders"], 812)

    def test_reponse_vide_ou_illisible(self):
        for info in (None, {}, [], "x", {"stat": "pas un dict"}):
            self.assertEqual(pipeline.manipulation_depuis_token_info(info), {})

    def test_zero_est_garde(self):
        champs = pipeline.manipulation_depuis_token_info({"wallet_tags_stat": {"sniper_wallets": 0}})
        self.assertEqual(champs["sniper_count"], 0)


class TestRepli(unittest.TestCase):
    def test_un_candidat_sans_signal_est_complete(self):
        gmgn = FauxGmgn()
        c = pipeline_avec(gmgn)._repli_manipulation([candidat()])[0]
        self.assertEqual(c.bundler_rate, 0.31)
        self.assertEqual(c.sniper_count, 14)
        self.assertEqual(c.manipulation_source, "gmgn_token_info")
        self.assertEqual(gmgn.appels, ["mint1"])

    def test_un_candidat_qui_a_deja_un_signal_ne_coute_rien(self):
        gmgn = FauxGmgn()
        c = candidat(bundler_rate=0.1)
        self.assertEqual(pipeline_avec(gmgn)._repli_manipulation([c])[0].bundler_rate, 0.1)
        self.assertEqual(gmgn.appels, [])

    def test_les_valeurs_presentes_ne_sont_pas_ecrasees(self):
        c = candidat(holders=300)
        self.assertEqual(pipeline_avec(FauxGmgn())._repli_manipulation([c])[0].holders, 300)

    def test_un_candidat_deja_rejete_ne_coute_rien(self):
        gmgn = FauxGmgn()
        pipeline_avec(gmgn)._repli_manipulation([candidat(rejected_reason="risque critique RugCheck")])
        self.assertEqual(gmgn.appels, [])

    def test_plafond_par_cycle(self):
        gmgn = FauxGmgn()
        lot = [candidat(f"mint{i}") for i in range(pipeline.REPLI_MANIPULATION_MAX_PAR_CYCLE + 5)]
        sortie = pipeline_avec(gmgn)._repli_manipulation(lot)
        self.assertEqual(len(gmgn.appels), pipeline.REPLI_MANIPULATION_MAX_PAR_CYCLE)
        self.assertEqual(len(sortie), len(lot))  # aucun candidat perdu, les autres restent sans donnée

    def test_gmgn_coupe_ou_en_pause_ne_coute_rien(self):
        for gmgn in (FauxGmgn(bloque=30.0), None):
            if gmgn is not None:
                self.assertEqual(pipeline_avec(gmgn)._repli_manipulation([candidat()])[0].bundler_rate, None)
                self.assertEqual(gmgn.appels, [])
            else:
                self.assertEqual(len(pipeline_avec(None)._repli_manipulation([candidat()])), 1)

    def test_reponse_vide_laisse_le_candidat_intact(self):
        c = pipeline_avec(FauxGmgn(info=None))._repli_manipulation([candidat()])[0]
        self.assertIsNone(c.bundler_rate)
        self.assertIsNone(c.manipulation_source)

    def test_une_exception_du_cli_ne_fait_pas_tomber_le_cycle(self):
        class Panne(FauxGmgn):
            def token_info(self, adresse):
                raise RuntimeError("cli")
        self.assertEqual(len(pipeline_avec(Panne())._repli_manipulation([candidat()])), 1)


class TestJournal(unittest.TestCase):
    def test_la_source_des_signaux_est_ecrite_a_l_entree(self):
        contexte = build_entry_context(candidat(bundler_rate=0.2, manipulation_source="gmgn_token_info"))
        self.assertEqual(contexte["entry_manipulation_source"], "gmgn_token_info")


if __name__ == "__main__":
    unittest.main()
