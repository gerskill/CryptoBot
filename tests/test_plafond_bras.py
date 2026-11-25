"""Plafond de 10 % du compte au total engagé PAR BRAS (préparé le 2026-11-22, hors réseau).

Règle : un NOUVEAU trade est refusé s'il ferait dépasser 10 % de la base prudente du
compte du bras = min(solde de départ du compte, valeur actuelle au prix d'entrée).
Les positions déjà ouvertes ne sont jamais fermées par cette règle.
"""

import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import correlation  # noqa: E402
from src.core.journal import TradeJournal  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.core.portfolio import PaperPortfolio  # noqa: E402
from src.core.positions import Position  # noqa: E402
from src.core.risk import arm_exposure_verdict, is_soft_reject, pre_trade_check  # noqa: E402


def candidat(adresse="mintNEW"):
    return Candidate(
        token_address=adresse, symbol="NEW", name="New", chain="solana",
        price_usd=1.0, liquidity_usd=30000, volume_1h=20000,
    )


def position(adresse, taille, entree=None, pending=0.0, reste=1.0):
    return Position(
        token_address=adresse, symbol=adresse[:6], chain="solana", pair_address="",
        entry_price=1.0, size_usd=taille, remaining_fraction=reste,
        entry_time=entree if entree is not None else time.time(),
        scale_in_pending_usd=pending,
    )


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.depuis = time.time() - 3600  # compte ouvert il y a 1 h
        self.pf = PaperPortfolio(
            500.0,
            TradeJournal(os.path.join(self.tmp, "trades.jsonl")),
            positions_path=os.path.join(self.tmp, "open.json"),
            cooldown_hours=0,
            max_drawdown_stop_pct=0,
            depuis=self.depuis,
        )

    def ajoute(self, pos, du_compte=True):
        """Comme `open` : une position du compte courant retire sa mise du solde libre."""
        self.pf.positions[pos.id] = pos
        if du_compte:
            self.pf.capital -= pos.size_usd * pos.remaining_fraction


class TestVerdict(Base):
    def test_desactive_a_zero(self):
        self.ajoute(position("a", 400.0))
        self.assertIsNone(arm_exposure_verdict(self.pf, 50.0, 0))
        self.assertIsNone(arm_exposure_verdict(self.pf, 50.0, None))

    def test_compte_neuf_plafond_50_dollars(self):
        self.assertAlmostEqual(self.pf.exposure_base(), 500.0)
        self.assertIsNone(arm_exposure_verdict(self.pf, 50.0, 10))  # pile 50 $ : accepté
        raison = arm_exposure_verdict(self.pf, 50.01, 10)
        self.assertIn("plafond du bras", raison)
        self.assertIn("10 % du compte (50.00 $", raison)

    def test_trois_positions_de_5_dollars_ne_declenchent_pas(self):
        # Usage normal du jour : 1 % de 500 $ = 5 $, 2 à 3 positions par bras.
        for i in range(3):
            self.ajoute(position(f"t{i}", 5.0))
        self.assertAlmostEqual(self.pf.engaged_total(), 15.0)
        self.assertIsNone(arm_exposure_verdict(self.pf, 5.0, 10))

    def test_refus_si_le_nouveau_trade_depasse(self):
        self.ajoute(position("a", 25.0))
        self.ajoute(position("b", 20.0))
        self.assertIsNone(arm_exposure_verdict(self.pf, 5.0, 10))      # 50 $ : ok
        self.assertIsNotNone(arm_exposure_verdict(self.pf, 6.0, 10))   # 51 $ : refus

    def test_base_prudente_baisse_apres_des_pertes(self):
        self.pf.capital = 300.0  # le bras a perdu 200 $ sur ses 500 $
        self.assertAlmostEqual(self.pf.exposure_base(), 300.0)
        self.assertIsNotNone(arm_exposure_verdict(self.pf, 31.0, 10))
        self.assertIsNone(arm_exposure_verdict(self.pf, 30.0, 10))

    def test_base_ne_monte_pas_au_dela_du_solde_de_depart(self):
        self.pf.capital = 900.0  # gains : la base reste 500 $
        self.assertAlmostEqual(self.pf.exposure_base(), 500.0)
        self.assertIsNotNone(arm_exposure_verdict(self.pf, 51.0, 10))

    def test_position_de_l_ancien_compte_compte_dans_l_engage(self):
        # Cas réel du 22/11 : mature_liquid garde mihao (67,37 $) ouvert à 08:56, avant
        # le compte n°4 de 12:32. Elle ne touche pas le solde du nouveau compte, mais elle
        # occupe du risque : la base prudente la compte.
        ancienne = position("mihao", 67.37, entree=self.depuis - 3 * 3600)
        self.ajoute(ancienne, du_compte=False)
        self.assertAlmostEqual(self.pf.exposure_base(), 500.0)
        self.assertAlmostEqual(self.pf.engaged_total(), 67.37)
        raison = arm_exposure_verdict(self.pf, 5.0, 10)
        self.assertIn("67.37 $ déjà engagés", raison)

    def test_entree_progressive_compte_la_part_promise(self):
        self.ajoute(position("s", 20.0, pending=30.0))
        self.assertAlmostEqual(self.pf.engaged_total(), 50.0)
        self.assertIsNotNone(arm_exposure_verdict(self.pf, 1.0, 10))

    def test_part_deja_vendue_ne_compte_plus(self):
        self.ajoute(position("p", 60.0, reste=0.5))  # 30 $ encore en jeu
        self.assertAlmostEqual(self.pf.engaged_total(), 30.0)
        self.assertIsNone(arm_exposure_verdict(self.pf, 20.0, 10))

    def test_portefeuille_sans_mesure_jamais_bloque(self):
        self.assertIsNone(arm_exposure_verdict(SimpleNamespace(), 1e9, 10))

    def test_ne_ferme_aucune_position(self):
        self.ajoute(position("a", 40.0))
        self.ajoute(position("b", 40.0))
        avant = dict(self.pf.positions)
        self.assertIsNotNone(arm_exposure_verdict(self.pf, 5.0, 10))
        self.assertEqual(self.pf.positions, avant)


class TestPorteUnique(Base):
    def verifie(self, taille, **kw):
        return pre_trade_check(
            mode="PAPER", candidate=candidat(), portfolio=self.pf, max_positions=3,
            fleet_exposure=correlation.measure([]), size_usd=taille,
            max_sector_positions=3, max_sector_exposure_pct=50.0,
            risk_rules={"cooldown_hours": 0, "max_drawdown_stop_pct": 0},
            **kw,
        )

    def test_sans_plafond_comportement_inchange(self):
        self.ajoute(position("a", 45.0))
        self.assertIsNone(self.verifie(10.0))

    def test_refus_motif_clair_et_refus_dur(self):
        self.ajoute(position("a", 45.0))
        raison = self.verifie(10.0, max_arm_exposure_pct=10.0)
        self.assertTrue(raison.startswith("plafond du bras"))
        self.assertFalse(is_soft_reject(raison))  # même taille pour tout le lot : on s'arrête

    def test_controle_anticipe_ne_bloque_que_si_deja_plein(self):
        self.ajoute(position("a", 45.0))
        # taille estimée large (5 % du solde) mais contrôle anticipé à 0 $ : passe
        self.assertIsNone(self.verifie(25.0, max_arm_exposure_pct=10.0, arm_exposure_size_usd=0.0))
        self.ajoute(position("b", 5.0))  # 50 $ : plein
        self.assertIsNone(self.verifie(25.0, max_arm_exposure_pct=10.0, arm_exposure_size_usd=0.0))
        self.ajoute(position("c", 0.5))  # 50,5 $ : au-delà
        self.assertIsNotNone(self.verifie(25.0, max_arm_exposure_pct=10.0, arm_exposure_size_usd=0.0))

    def test_les_autres_limites_passent_avant(self):
        for i in range(3):
            self.ajoute(position(f"t{i}", 30.0))
        raison = self.verifie(5.0, max_arm_exposure_pct=10.0)
        self.assertEqual(raison, "3/3 positions déjà ouvertes")


class TestReglageParDefaut(unittest.TestCase):
    def test_defaut_10_pour_cent_et_desactivable(self):
        from src.main import AlphaLoop

        class Params(dict):
            def get(self, cle, defaut=None):
                return super().get(cle, defaut)

        loop = AlphaLoop.__new__(AlphaLoop)
        loop.params = Params()
        self.assertEqual(loop.max_arm_exposure_pct, 10.0)
        loop.params = Params({"risk_rules.max_arm_exposure_pct": 0})
        self.assertEqual(loop.max_arm_exposure_pct, 0)


if __name__ == "__main__":
    unittest.main()
