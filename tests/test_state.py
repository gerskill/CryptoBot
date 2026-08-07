"""Snapshot d'état lu par le dashboard : `StateWriter` et les payloads JSON.

Le bot et le dashboard sont DÉCOUPLÉS : le bot écrit un fichier JSON à chaque
cycle, l'API le lit. Ces tests verrouillent l'écriture atomique (le dashboard
ne doit jamais lire un fichier à moitié écrit), la tolérance à la corruption
et à l'absence de fichier, et le contrat des deux fonctions de sérialisation
utilisées par le dashboard (`candidate_to_dict`, `position_to_dict`).

`position_to_dict` a déjà un test dédié (`test_state_position_payload.py`)
pour `high_water_pct`/`low_water_pct` : on ne le reproduit pas ici, on couvre
le reste du contrat (P&L, absence de prix, valeurs par défaut).
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.models import Candidate  # noqa: E402
from src.core.positions import Position  # noqa: E402
from src.core.state import StateWriter, candidate_to_dict, position_to_dict  # noqa: E402


def _candidate(**overrides):
    base = dict(token_address="addr", symbol="TOK", name="Token", chain="solana")
    base.update(overrides)
    return Candidate(**base)


def _position(**overrides):
    base = dict(
        id="p1", token_address="addr", symbol="TOK", chain="solana",
        pair_address=None, entry_price=1.0, size_usd=20.0,
        stop_loss_pct=-25.0, take_profit_1=100.0, take_profit_2=300.0,
        take_profit_3=500.0, entry_time=1_000_000.0,
    )
    base.update(overrides)
    return Position(**base)


class TestStateWriterEcriture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "state.json")

    def test_ecrit_puis_relit_le_meme_payload(self):
        writer = StateWriter(self.path)
        writer.write({"cycle": 1, "positions": []})
        payload = writer.read()
        self.assertEqual(payload["cycle"], 1)
        self.assertEqual(payload["positions"], [])

    def test_ajoute_un_horodatage(self):
        writer = StateWriter(self.path)
        writer.write({"cycle": 1})
        self.assertIn("updated_at", writer.read())

    def test_ne_mute_pas_le_dict_dorigine(self):
        writer = StateWriter(self.path)
        original = {"cycle": 1}
        writer.write(original)
        self.assertNotIn("updated_at", original)

    def test_ecriture_atomique_pas_de_fichier_temporaire_residuel(self):
        writer = StateWriter(self.path)
        writer.write({"cycle": 1})
        restes = [f for f in os.listdir(self.tmp) if f.endswith(".tmp")]
        self.assertEqual(restes, [])

    def test_dossier_parent_absent_est_cree(self):
        path = os.path.join(self.tmp, "sous_dossier", "state.json")
        writer = StateWriter(path)
        writer.write({"cycle": 1})
        self.assertTrue(os.path.exists(path))

    def test_valeur_non_serialisable_convertie_via_str(self):
        # `default=str` : un objet quelconque (ex. un set, une exception) ne
        # doit jamais faire planter l'écriture d'un cycle.
        writer = StateWriter(self.path)
        writer.write({"objet": {1, 2, 3}})
        payload = writer.read()
        self.assertIn("objet", payload)

    def test_ecriture_suivante_ecrase_bien_la_precedente(self):
        writer = StateWriter(self.path)
        writer.write({"cycle": 1})
        writer.write({"cycle": 2})
        self.assertEqual(writer.read()["cycle"], 2)


class TestStateWriterLecture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "state.json")

    def test_fichier_absent_rend_none(self):
        self.assertIsNone(StateWriter(self.path).read())

    def test_fichier_corrompu_rend_none_sans_lever(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{ pas du json valide")
        self.assertIsNone(StateWriter(self.path).read())


class TestCandidateToDict(unittest.TestCase):
    def test_porte_les_champs_didentite_et_de_marche(self):
        candidate = _candidate(price_usd=0.001, liquidity_usd=30000, holders=120)
        payload = candidate_to_dict(candidate)
        self.assertEqual(payload["token_address"], "addr")
        self.assertEqual(payload["symbol"], "TOK")
        self.assertEqual(payload["price_usd"], 0.001)
        self.assertEqual(payload["liquidity_usd"], 30000)
        self.assertEqual(payload["holders"], 120)

    def test_rugcheck_risks_devient_une_liste(self):
        # `rugcheck_risks` est un tuple immuable côté modèle : le dashboard
        # (JSON) n'a pas de tuple, la conversion doit être explicite.
        candidate = _candidate(rugcheck_risks=("mint_not_revoked", "low_lp"))
        payload = candidate_to_dict(candidate)
        self.assertEqual(payload["rugcheck_risks"], ["mint_not_revoked", "low_lp"])
        self.assertIsInstance(payload["rugcheck_risks"], list)

    def test_valeurs_par_defaut_absentes_restent_none(self):
        payload = candidate_to_dict(_candidate())
        self.assertIsNone(payload["holders"])
        self.assertIsNone(payload["rejected_reason"])

    def test_rejected_reason_transmise(self):
        candidate = _candidate(rejected_reason="liquidité 7271$ < 25000$")
        self.assertEqual(
            candidate_to_dict(candidate)["rejected_reason"],
            "liquidité 7271$ < 25000$",
        )


class TestPositionToDict(unittest.TestCase):
    def test_sans_prix_le_pnl_est_nul(self):
        payload = position_to_dict(_position())
        self.assertEqual(payload["pnl_pct"], 0.0)
        self.assertEqual(payload["pnl_usd"], 0.0)
        self.assertIsNone(payload["current_price"])

    def test_pnl_positif_calcule_depuis_le_prix_courant(self):
        position = _position(entry_price=1.0, size_usd=100.0)
        payload = position_to_dict(position, price=1.5)
        self.assertAlmostEqual(payload["pnl_pct"], 50.0)
        self.assertAlmostEqual(payload["pnl_usd"], 50.0, places=2)
        self.assertAlmostEqual(payload["multiple"], 1.5)

    def test_pnl_tient_compte_de_la_fraction_restante(self):
        # Après une sortie partielle, le P&L en dollars ne porte plus que sur
        # ce qui reste engagé — sinon le dashboard afficherait un gain
        # supérieur à ce qui est réellement en jeu.
        position = _position(entry_price=1.0, size_usd=100.0, remaining_fraction=0.5)
        payload = position_to_dict(position, price=2.0)
        self.assertAlmostEqual(payload["pnl_pct"], 100.0)
        self.assertAlmostEqual(payload["pnl_usd"], 50.0, places=2)

    def test_champs_de_regles_de_sortie_transmis(self):
        position = _position(stop_loss_pct=-25.0, take_profit_1=100.0)
        payload = position_to_dict(position)
        self.assertEqual(payload["stop_loss_pct"], -25.0)
        self.assertEqual(payload["take_profit_1"], 100.0)

    def test_id_et_symbole_transmis(self):
        payload = position_to_dict(_position(id="pos-42", symbol="MOON"))
        self.assertEqual(payload["id"], "pos-42")
        self.assertEqual(payload["symbol"], "MOON")


if __name__ == "__main__":
    unittest.main(verbosity=2)
