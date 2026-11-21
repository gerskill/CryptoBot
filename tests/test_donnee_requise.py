"""Option `require_data` d'un bras : exiger la donnée au lieu de la laisser passer (ADR 022).

CONTEXTE. Invariant du dépôt : « une donnée absente ne rejette jamais ». Mesuré le 2026-11-21,
des entrées partent avec bundlers/insiders/snipers vides (GMGN ou Helius en défaut) et passent
donc des filtres qui, eux, existent. L'option permet à UN bras de dire « sans cette donnée, pas
d'entrée ». Elle est DÉSACTIVÉE PARTOUT : ces tests verrouillent surtout qu'elle ne change rien
tant qu'aucun bras ne la demande, et qu'aucun bras ne la demande sans ADR.
"""

import glob
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import pipeline
from src.core.learning import RELAXATIONS
from src.core.models import Candidate
from src.core.sante_donnees import CHAMPS_ENTREE
from src.core.shadow import reason_family

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Bras autorisés à demander `require_data`. VIDE : l'activer pour un bras exige l'ADR 022 (mesure
# `scripts/analyse_donnees_manquantes.py`, décision de Killian) PUIS d'ajouter son nom ici.
BRAS_AVEC_DONNEES_REQUISES: set[str] = set()


def candidat(**champs):
    base = {"token_address": "mint1", "symbol": "TOK", "name": "TOK", "chain": "solana",
            "price_usd": 1.0, "liquidity_usd": 50000, "volume_1h": 20000}
    base.update(champs)
    return Candidate(**base)


def raison(c, filtres):
    return pipeline.ScanPipeline._manipulation_reason(c, filtres)


class TestParDefaut(unittest.TestCase):
    """Sans `require_data`, le comportement d'avant : une donnée absente passe."""

    def test_un_candidat_sans_aucun_signal_passe_sans_l_option(self):
        filtres = {"max_bundler_rate": 0.2, "max_insider_rate": 0.1, "max_sniper_count": 10}
        self.assertIsNone(raison(candidat(), filtres))

    def test_option_vide_ou_absente_ne_change_rien(self):
        for valeur in (None, [], ()):
            self.assertIsNone(raison(candidat(), {"require_data": valeur}))

    def test_aucun_bras_ne_l_active_sans_adr(self):
        """Le garde-fou de l'ADR 022 : toute config qui active `require_data` doit être listée ici."""
        activant = set()
        for chemin in glob.glob(os.path.join(RACINE, "config", "**", "*.json"), recursive=True):
            try:
                with open(chemin, encoding="utf-8") as fh:
                    texte = fh.read()
            except OSError:
                continue
            if '"require_data"' in texte and json.loads(texte or "{}"):
                activant.add(os.path.splitext(os.path.basename(chemin))[0])
        self.assertEqual(
            activant - BRAS_AVEC_DONNEES_REQUISES, set(),
            "un bras active `require_data` sans ADR : voir docs/adr/022-donnees-requises-opt-in.md",
        )


class TestAvecOption(unittest.TestCase):
    def test_donnee_manquante_rejette(self):
        self.assertEqual(
            raison(candidat(), {"require_data": ["bundlers"]}),
            "donnée manquante : bundlers",
        )

    def test_zero_est_une_valeur_presente(self):
        # 0 sniper, 0 achat smart money : valeurs légitimes, jamais « manquantes ».
        c = candidat(sniper_count=0, bundler_rate=0.0, smart_money_buys_30m=0)
        filtres = {"require_data": ["snipers", "bundlers", "smart_money"]}
        self.assertIsNone(raison(c, filtres))

    def test_la_premiere_donnee_manquante_est_nommee(self):
        c = candidat(bundler_rate=0.1)
        self.assertEqual(raison(c, {"require_data": ["bundlers", "insiders"]}), "donnée manquante : insiders")

    def test_les_seuils_existants_gardent_la_priorite_sur_la_valeur(self):
        # Donnée présente et trop haute : le filtre habituel parle, pas l'option.
        c = candidat(bundler_rate=0.5)
        filtres = {"require_data": ["bundlers"], "max_bundler_rate": 0.2}
        self.assertTrue(raison(c, filtres).startswith("bundlers"))

    def test_un_nom_inconnu_est_ignore_et_signale_une_seule_fois(self):
        pipeline._DONNEES_INCONNUES_SIGNALEES.clear()
        sortie = io.StringIO()
        with redirect_stdout(sortie):
            self.assertIsNone(raison(candidat(), {"require_data": ["bundlerz"]}))
            self.assertIsNone(raison(candidat(), {"require_data": ["bundlerz"]}))
        self.assertEqual(sortie.getvalue().count("bundlerz"), 1)

    def test_une_valeur_qui_n_est_pas_une_liste_ne_leve_pas(self):
        # Une config mal écrite ne doit jamais faire tomber la boucle (invariant 1).
        for valeur in ("bundlers", 5, {"bundlers": True}):
            raison(candidat(), {"require_data": valeur})


class TestFamilleEtApprentissage(unittest.TestCase):
    def test_le_motif_a_sa_propre_famille_pas_celle_de_holders(self):
        # « donnée manquante : holders » contient « holders » : sans branche dédiée, il serait
        # rangé dans la famille relâchable `holders` et l'apprentissage baisserait min_holders.
        self.assertEqual(reason_family("donnée manquante : holders"), "donnee_requise")
        self.assertEqual(reason_family("donnée manquante : bundlers"), "donnee_requise")

    def test_les_motifs_existants_gardent_leur_famille(self):
        self.assertEqual(reason_family("holders 50 < 75"), "holders")
        self.assertEqual(reason_family("liquidité 1000$ < 25000$"), "liquidity")

    def test_la_famille_n_est_jamais_relachee_par_l_apprentissage(self):
        self.assertNotIn("donnee_requise", RELAXATIONS)

    def test_chaque_donnee_exigible_est_mesuree_par_la_sante_des_donnees(self):
        mesures = {nom for nom, _ in CHAMPS_ENTREE}
        self.assertEqual(set(pipeline.DONNEES_EXIGIBLES) - mesures, set())


if __name__ == "__main__":
    unittest.main()
