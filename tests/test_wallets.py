"""Registre de wallets — étape 1 : observer sans décider.

Ce qui est verrouillé ici, et pourquoi :

  - la DÉDUPLICATION sur (wallet, token). Un wallet qui accumule sur le même
    token pendant dix cycles compterait dix fois, et « nombre de tokens
    touchés » — la mesure qui distingue un profil d'une coïncidence — ne
    voudrait plus rien dire ;
  - le FILTRE sur les tokens connus. Sans lui le registre avale tout le flux
    de la chaîne, dont l'écrasante majorité ne croisera jamais une décision,
    et l'avance devient incalculable faute de référence ;
  - l'AVANCE, pas le gain. Un wallet qui achète en même temps que tout le
    monde n'apporte rien, même s'il gagne.
"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.wallets import (  # noqa: E402
    MIN_TOKENS_FOR_PROFILE,
    WalletObservation,
    WalletRegistry,
)


def trade(wallet, token, ts, side="buy", usd=50.0, tags=(), symbol="TEST"):
    return {
        "maker": wallet,
        "base_address": token,
        "timestamp": ts,
        "side": side,
        "amount_usd": usd,
        "base_token": {"symbol": symbol},
        "maker_info": {"tags": list(tags)},
    }


class TestObservation(unittest.TestCase):
    def test_lavance_se_calcule_sur_notre_decouverte(self):
        obs = WalletObservation(
            wallet="w", token_address="t", symbol="X", side="buy",
            wallet_ts=1000.0, amount_usd=50.0, bot_first_seen_ts=1600.0,
        )
        self.assertEqual(obs.lead_minutes, 10.0)

    def test_avance_negative_quand_le_wallet_est_en_retard(self):
        obs = WalletObservation(
            wallet="w", token_address="t", symbol="X", side="buy",
            wallet_ts=1600.0, amount_usd=50.0, bot_first_seen_ts=1000.0,
        )
        self.assertEqual(obs.lead_minutes, -10.0)

    def test_avance_inconnue_sans_reference(self):
        obs = WalletObservation("w", "t", "X", "buy", 1000.0, 50.0)
        self.assertIsNone(obs.lead_minutes)

    def test_les_bots_ne_sont_pas_directionnels(self):
        self.assertFalse(
            WalletObservation("w", "t", "X", "buy", 1.0, 1.0,
                              tags=("arbitrager",)).is_directional
        )
        self.assertTrue(
            WalletObservation("w", "t", "X", "buy", 1.0, 1.0,
                              tags=("smart_degen",)).is_directional
        )


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.registry = WalletRegistry(os.path.join(self.tmp, "wallets.jsonl"))
        self.now = time.time()

    def test_enregistre_les_couples_inedits(self):
        added = self.registry.observe(
            [trade("w1", "tokA", self.now - 600), trade("w2", "tokA", self.now - 300)],
            first_seen_by_token={"tokA": self.now},
            known_tokens={"tokA"},
        )
        self.assertEqual(added, 2)

    def test_deduplique_sur_wallet_et_token(self):
        # Le même wallet qui recharge dix fois ne doit compter qu'une fois,
        # sinon les accumulateurs écrasent la statistique.
        rows = [trade("w1", "tokA", self.now - i * 60) for i in range(10)]
        self.assertEqual(
            self.registry.observe(rows, known_tokens={"tokA"}), 1
        )

    def test_deduplique_entre_deux_cycles(self):
        self.registry.observe([trade("w1", "tokA", self.now)], known_tokens={"tokA"})
        self.assertEqual(
            self.registry.observe([trade("w1", "tokA", self.now)], known_tokens={"tokA"}),
            0,
        )

    def test_la_dedup_survit_au_redemarrage(self):
        self.registry.observe([trade("w1", "tokA", self.now)], known_tokens={"tokA"})
        recharge = WalletRegistry(self.registry.path)
        self.assertEqual(
            recharge.observe([trade("w1", "tokA", self.now)], known_tokens={"tokA"}), 0
        )

    def test_ignore_les_tokens_que_le_bot_na_pas_vus(self):
        added = self.registry.observe(
            [trade("w1", "inconnu", self.now)], known_tokens={"tokA"}
        )
        self.assertEqual(added, 0)

    def test_un_meme_wallet_sur_deux_tokens_compte_deux_fois(self):
        self.registry.observe(
            [trade("w1", "tokA", self.now), trade("w1", "tokB", self.now)],
            known_tokens={"tokA", "tokB"},
        )
        self.assertEqual(self.registry.profiles()[0].tokens, 2)

    def test_lavance_est_journalisee(self):
        self.registry.observe(
            [trade("w1", "tokA", self.now - 720)],
            first_seen_by_token={"tokA": self.now},
            known_tokens={"tokA"},
        )
        self.assertEqual(self.registry.read_all()[0]["lead_minutes"], 12.0)

    def test_ligne_corrompue_ignoree(self):
        self.registry.observe([trade("w1", "tokA", self.now)], known_tokens={"tokA"})
        with open(self.registry.path, "a", encoding="utf-8") as fh:
            fh.write("{cassé\n")
        self.assertEqual(len(self.registry.read_all()), 1)

    def test_trade_sans_wallet_ou_sans_token_ignore(self):
        self.assertEqual(
            self.registry.observe(
                [{"timestamp": self.now}, {"maker": "w1"}], known_tokens={"tokA"}
            ),
            0,
        )

    def test_timestamp_en_millisecondes_accepte(self):
        self.registry.observe(
            [trade("w1", "tokA", (self.now - 600) * 1000)],
            first_seen_by_token={"tokA": self.now},
            known_tokens={"tokA"},
        )
        self.assertAlmostEqual(self.registry.read_all()[0]["lead_minutes"], 10.0, places=1)


class TestProfils(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.registry = WalletRegistry(os.path.join(self.tmp, "wallets.jsonl"))
        self.now = time.time()

    def _observe(self, wallet, tokens, lead_minutes, tags=()):
        for token in tokens:
            self.registry.observe(
                [trade(wallet, token, self.now - lead_minutes * 60, tags=tags)],
                first_seen_by_token={token: self.now},
                known_tokens={token},
            )

    def test_un_seul_token_ne_fait_pas_un_profil(self):
        self._observe("w1", ["tokA"], 10)
        self.assertFalse(self.registry.profiles()[0].profiled)

    def test_assez_de_tokens_fait_un_profil(self):
        self._observe("w1", [f"tok{i}" for i in range(MIN_TOKENS_FOR_PROFILE)], 10)
        self.assertTrue(self.registry.profiles()[0].profiled)

    def test_classe_par_avance_decroissante(self):
        self._observe("lent", ["a1", "a2", "a3"], 2)
        self._observe("rapide", ["b1", "b2", "b3"], 20)
        self.assertEqual(self.registry.profiles()[0].wallet, "rapide")

    def test_les_profils_passent_avant_les_isoles(self):
        self._observe("isole", ["z1"], 99)
        self._observe("profile", ["c1", "c2", "c3"], 5)
        self.assertEqual(self.registry.profiles()[0].wallet, "profile")

    def test_les_bots_sont_ecartes_par_defaut(self):
        self._observe("bot", ["a1", "a2", "a3"], 30, tags=("arbitrager",))
        self._observe("humain", ["b1", "b2", "b3"], 5, tags=("smart_degen",))
        self.assertEqual(
            [p.wallet for p in self.registry.profiles()], ["humain"]
        )
        self.assertEqual(len(self.registry.profiles(directional_only=False)), 2)

    def test_wallets_pour_un_token_tries_par_anteriorite(self):
        self.registry.observe(
            [trade("tard", "tokA", self.now - 60), trade("tot", "tokA", self.now - 900)],
            first_seen_by_token={"tokA": self.now},
            known_tokens={"tokA"},
        )
        self.assertEqual(
            [r["wallet"] for r in self.registry.wallets_for("tokA")], ["tot", "tard"]
        )

    def test_stats_globales(self):
        self._observe("w1", ["a1", "a2", "a3"], 10)
        stats = self.registry.stats
        self.assertEqual(stats["observations"], 3)
        self.assertEqual(stats["tokens"], 3)
        self.assertEqual(stats["profiled"], 1)
        self.assertAlmostEqual(stats["median_lead_minutes"], 10.0, places=0)

    def test_la_premiere_vue_survit_au_redemarrage(self):
        # Le bug évité : en mémoire seule, un redémarrage ferait passer tous
        # les tokens pour « découverts à l'instant » et gonflerait chaque
        # avance de plusieurs heures.
        ancien = self.now - 7200
        self.registry.mark_first_seen(["tokA"], now=ancien)
        recharge = WalletRegistry(self.registry.path)
        self.assertAlmostEqual(recharge.first_seen["tokA"], ancien, places=1)

    def test_la_premiere_vue_ne_remonte_jamais(self):
        ancien = self.now - 3600
        self.registry.mark_first_seen(["tokA"], now=ancien)
        self.registry.mark_first_seen(["tokA"], now=self.now)
        self.assertAlmostEqual(self.registry.first_seen["tokA"], ancien, places=1)

    def test_avance_correcte_apres_redemarrage(self):
        self.registry.mark_first_seen(["tokA"], now=self.now - 3600)
        recharge = WalletRegistry(self.registry.path)
        recharge.observe(
            [trade("w1", "tokA", self.now - 3660)],
            first_seen_by_token=recharge.first_seen,
            known_tokens={"tokA"},
        )
        self.assertAlmostEqual(recharge.read_all()[0]["lead_minutes"], 1.0, places=1)

    def test_fichier_de_premieres_vues_corrompu_ne_crashe_pas(self):
        self.registry.mark_first_seen(["tokA"])
        with open(self.registry.first_seen_path, "w", encoding="utf-8") as fh:
            fh.write("{cassé")
        self.assertEqual(WalletRegistry(self.registry.path).first_seen, {})

    def test_registre_vide_ne_crashe_pas(self):
        self.assertEqual(self.registry.stats["observations"], 0)
        self.assertEqual(self.registry.profiles(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
