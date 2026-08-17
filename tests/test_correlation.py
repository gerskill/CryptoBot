"""Tests de la concentration sectorielle — src/core/correlation.py."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.correlation import (  # noqa: E402
    MIN_FLEET_POSITIONS_FOR_SHARE,
    classify,
    measure,
    position_sector,
    verdict,
)
from src.core.models import Candidate  # noqa: E402
from src.core.portfolio import PaperPortfolio  # noqa: E402
from src.core.positions import Position  # noqa: E402


def position(symbol="TOKEN", size=30.0, remaining=1.0, sector=None):
    return Position(
        token_address=f"addr-{symbol}-{size}-{remaining}",
        symbol=symbol,
        chain="solana",
        entry_price=1.0,
        size_usd=size,
        remaining_fraction=remaining,
        sector=sector,
    )


class TestClassify(unittest.TestCase):
    def test_mot_entier(self):
        self.assertEqual(classify("BARK"), "chien")
        self.assertEqual(classify("TOAD"), "grenouille")
        self.assertEqual(classify("BOT"), "ia")

    def test_sous_chaine_pour_symboles_colles(self):
        # Cas réels du journal : le découpage en mots ne les sépare pas.
        self.assertEqual(classify("toadtard"), "grenouille")
        self.assertEqual(classify("MIDASTOAD"), "grenouille")
        self.assertEqual(classify("HAIRINU"), "chien")
        self.assertEqual(classify("splashdog"), "chien")

    def test_le_nom_complete_le_symbole(self):
        """« PMR » seul ne dit rien ; « Pomeranian » dit la meta."""
        self.assertIsNone(classify("PMR"))
        self.assertEqual(classify("PMR", "Pomeranian Coin"), "chien")

    def test_accents_et_ponctuation_normalises(self):
        self.assertEqual(classify("BOIÚNA", "Frög Deluxe!"), "grenouille")

    def test_cles_de_deux_lettres_jamais_en_sous_chaine(self):
        """« ai » dans CHAIN, RAIN, MAID ou SAIL ne fait pas de la meta IA."""
        for symbole in ("CHAIN", "RAIN", "MAID", "SAIL"):
            self.assertIsNone(classify(symbole), f"{symbole} ne doit pas être classé")

    def test_cles_de_trois_lettres_en_suffixe_seulement(self):
        """« cat » attrape SOLCAT (fin) mais pas CATALYST (début) ni LOCATE."""
        self.assertEqual(classify("SOLCAT"), "chat")
        for symbole in ("CATALYST", "LOCATE", "BOTTOM", "DOGMA"):
            self.assertIsNone(classify(symbole), f"{symbole} ne doit pas être classé")

    def test_suffixe_reconnu_a_travers_un_espace(self):
        """« SOL CAT », « SOLCAT » et « Solana Cat » tombent au même endroit."""
        self.assertEqual(classify("SOL CAT"), "chat")
        self.assertEqual(classify("SLC", "Solana Cat"), "chat")

    def test_inconnu_rend_none_pas_un_secteur_divers(self):
        self.assertIsNone(classify("ZQXWV"))
        self.assertIsNone(classify(""))
        self.assertIsNone(classify("", ""))

    def test_ambiguite_tranchee_de_facon_stable(self):
        """« claudedog » touche « ia » et « chien » : la priorité donne « ia »."""
        self.assertEqual(classify("claudedog"), "ia")
        self.assertEqual(classify("CLAUDEDOG"), classify("claudedog"))

    def test_politique_prime_sur_animal(self):
        """« trump » (n'importe où) + « cat » (suffixe) : politique gagne."""
        self.assertEqual(classify("TRUMPCAT"), "politique")


class TestMeasure(unittest.TestCase):
    def test_agrege_notionnel_et_compte(self):
        exposure = measure([
            position("TOAD", size=30, sector="grenouille"),
            position("FROGOS", size=10, sector="grenouille"),
            position("BARK", size=20, sector="chien"),
        ])
        self.assertEqual(exposure.total_positions, 3)
        self.assertEqual(exposure.total_notional_usd, 60.0)
        grenouille = exposure.get("grenouille")
        self.assertEqual(grenouille.positions, 2)
        self.assertEqual(grenouille.notional_usd, 40.0)
        self.assertAlmostEqual(grenouille.share_pct, 100 * 40 / 60)

    def test_notionnel_tient_compte_de_la_fraction_restante(self):
        """Un TP1 pris réduit l'exposition — même convention que `equity()`."""
        exposure = measure([position("TOAD", size=40, remaining=0.5, sector="grenouille")])
        self.assertEqual(exposure.total_notional_usd, 20.0)

    def test_non_classe_compte_dans_le_total_mais_jamais_en_secteur(self):
        exposure = measure([
            position("ZQXWV", size=50, sector=None),
            position("TOAD", size=50, sector="grenouille"),
        ])
        self.assertEqual(exposure.total_notional_usd, 100.0)
        self.assertEqual(exposure.unclassified_positions, 1)
        self.assertEqual([e.sector for e in exposure.by_sector], ["grenouille"])

    def test_position_soldee_ignoree(self):
        exposure = measure([position("TOAD", size=30, remaining=0.0, sector="grenouille")])
        self.assertEqual(exposure.total_positions, 0)
        self.assertEqual(exposure.by_sector, ())

    def test_secteur_absent_replie_sur_le_symbole(self):
        """Positions restaurées d'un fichier écrit avant l'existence du champ."""
        self.assertEqual(position_sector(position("BARK", sector=None)), "chien")
        self.assertIsNone(position_sector(position("PMR", sector=None)))

    def test_secteur_stocke_prime_sur_le_symbole(self):
        """Le secteur figé à l'entrée ne doit jamais être réécrit par le symbole."""
        self.assertEqual(position_sector(position("BARK", sector="grenouille")), "grenouille")


class TestVerdict(unittest.TestCase):
    def exposure_grenouille(self, n, size=30.0, autres=0):
        positions = [position(f"TOAD{i}", size=size, sector="grenouille") for i in range(n)]
        positions += [position(f"X{i}", size=size, sector=None) for i in range(autres)]
        return measure(positions)

    def test_secteur_inconnu_jamais_contraint(self):
        exposure = self.exposure_grenouille(5)
        self.assertIsNone(verdict(exposure, None, 30.0, 1, 1.0))

    def test_plafond_de_nombre(self):
        exposure = self.exposure_grenouille(3)
        blocage = verdict(exposure, "grenouille", 30.0, 3, 0)
        self.assertIsNotNone(blocage)
        self.assertIn("grenouille", blocage)
        self.assertIn("plafond 3", blocage)

    def test_sous_le_plafond_de_nombre_passe(self):
        exposure = self.exposure_grenouille(2)
        self.assertIsNone(verdict(exposure, "grenouille", 30.0, 3, 0))

    def test_autre_secteur_non_bloque(self):
        exposure = self.exposure_grenouille(3)
        self.assertIsNone(verdict(exposure, "chien", 30.0, 3, 0))

    def test_plafond_de_part(self):
        """2 grenouilles + 1 inconnue, +1 grenouille = 75 % projeté, > 50 %."""
        exposure = self.exposure_grenouille(2, autres=1)
        blocage = verdict(exposure, "grenouille", 30.0, 0, 50.0)
        self.assertIsNotNone(blocage)
        self.assertIn("%", blocage)

    def test_part_ignoree_sous_le_plancher_de_validite(self):
        """Avec 2 positions ouvertes, un secteur pèse trivialement 100 %."""
        exposure = self.exposure_grenouille(1)
        self.assertLess(exposure.total_positions + 1, MIN_FLEET_POSITIONS_FOR_SHARE)
        self.assertIsNone(verdict(exposure, "grenouille", 30.0, 0, 50.0))

    def test_seuils_a_zero_desactivent(self):
        exposure = self.exposure_grenouille(7)
        self.assertIsNone(verdict(exposure, "grenouille", 30.0, 0, 0))

    def test_flotte_vide_laisse_passer(self):
        self.assertIsNone(verdict(measure([]), "grenouille", 30.0, 3, 50.0))

    def test_cas_reel_du_journal_91_pct_en_ia(self):
        """L'état mesuré le pire : 3 « claudius » + 1 autre = 91 % en meta ia."""
        exposure = measure([
            position("claudius", size=32.0, sector="ia"),
            position("claudius", size=32.0, sector="ia"),
            position("claudius", size=32.0, sector="ia"),
            position("SADSWAG", size=9.0, sector=None),
        ])
        self.assertGreater(exposure.get("ia").share_pct, 90)
        # Avec les seuils livrés, la 3e position n'aurait jamais été ouverte.
        deux_claudius = measure([
            position("claudius", size=32.0, sector="ia"),
            position("claudius", size=32.0, sector="ia"),
            position("SADSWAG", size=9.0, sector=None),
        ])
        self.assertIsNotNone(verdict(deux_claudius, "ia", 32.0, 3, 50.0))


class TestSecteurFigeALEntree(unittest.TestCase):
    """L'intégration qui compte : `PaperPortfolio.open` remplit `sector`."""

    class _Journal:
        def read_positions(self):
            return []

    def test_open_classe_avec_le_nom_du_candidat(self):
        portfolio = PaperPortfolio(capital=1000.0, journal=self._Journal())
        candidate = Candidate(
            token_address="addr", symbol="PMR", name="Pomeranian Coin",
            chain="solana", price_usd=1.0,
        )
        opened = portfolio.open(candidate, {"exit_rules": {}}, 30.0)
        # « PMR » seul ne dirait rien : c'est le nom qui porte la meta, et il
        # n'existe plus après l'entrée.
        self.assertIsNone(classify(opened.symbol))
        self.assertEqual(opened.sector, "chien")

    def test_open_laisse_none_quand_rien_ne_ressort(self):
        portfolio = PaperPortfolio(capital=1000.0, journal=self._Journal())
        candidate = Candidate(
            token_address="addr", symbol="ZQXWV", name="Zqxwv",
            chain="solana", price_usd=1.0,
        )
        self.assertIsNone(portfolio.open(candidate, {"exit_rules": {}}, 30.0).sector)


class TestCablageDansLaBoucle(unittest.TestCase):
    """La garde est fleet-wide : elle doit voir les SEPT bras, pas un seul.

    Construit un `AlphaLoop` sans passer par `__init__` (qui ouvrirait des
    clients réseau et un verrou d'instance) : seules les pièces que la garde
    lit sont posées.
    """

    def loop(self, arms, params_document):
        from src.core.params import ParamsStore
        from src.main import AlphaLoop

        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(params_document, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)

        loop = object.__new__(AlphaLoop)
        loop.arms = arms
        loop.params = ParamsStore(handle.name)
        return loop

    class _Portfolio:
        def __init__(self, positions):
            self.positions = {p.id: p for p in positions}

    class _Arm:
        def __init__(self, name, positions):
            self.name = name
            self.portfolio = TestCablageDansLaBoucle._Portfolio(positions)

    def test_exposition_agregee_sur_tous_les_bras(self):
        loop = self.loop(
            [
                self._Arm("sniper", [position("TOAD", size=30, sector="grenouille")]),
                self._Arm("scalp", [position("FROGOS", size=30, sector="grenouille")]),
                self._Arm("runner", [position("BARK", size=30, sector="chien")]),
            ],
            {},
        )
        exposure = loop._fleet_exposure()
        # Un seul bras en verrait 1 ; la garde doit en compter 2.
        self.assertEqual(exposure.get("grenouille").positions, 2)
        self.assertEqual(exposure.total_positions, 3)

    def test_seuils_lus_sur_le_document_global(self):
        loop = self.loop([], {"risk_rules": {
            "max_sector_positions": 2, "max_sector_exposure_pct": 33.0,
        }})
        self.assertEqual(loop.max_sector_positions, 2)
        self.assertEqual(loop.max_sector_exposure_pct, 33.0)

    def test_defauts_quand_la_cle_est_absente(self):
        loop = self.loop([], {})
        self.assertEqual(loop.max_sector_positions, 3)
        self.assertEqual(loop.max_sector_exposure_pct, 50.0)

    def test_porte_declaree_dans_l_entonnoir(self):
        """Sans ça, `scripts/analyse_rejets.py` ignorerait silencieusement la porte."""
        from src.core.funnel import GATES

        self.assertIn("correlation", GATES)
        self.assertLess(GATES.index("portefeuille"), GATES.index("correlation"))
        self.assertLess(GATES.index("correlation"), GATES.index("technique"))


if __name__ == "__main__":
    unittest.main()
