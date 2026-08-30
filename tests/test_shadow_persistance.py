"""Reprise des suivis shadow après un redémarrage, et taxonomie des gardes.

Deux défauts distincts verrouillés ici :

1. `ShadowTracker._tracked` ne vivait qu'en mémoire. Un redémarrage perdait
   tout rejet pas encore arrivé à ses 4 h — donc `SHADOW_MIN_SAMPLE` restait
   hors d'atteinte sur une boucle relancée souvent, donc `_relax_from_shadow`
   ne rendait jamais rien.
2. « LP verrouillée 12% < 50% » et « concentration meta » tombaient dans la
   famille « autre », qu'aucun paramètre ne dessert : les deux gardes livrées
   le 2026-08-17 étaient invisibles au comptage par famille, donc impossibles
   à qualifier sur données réelles.
"""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.models import Candidate  # noqa: E402
from src.core.shadow import (  # noqa: E402
    MAX_TRACKING_HOURS,
    ShadowTracker,
    reason_family,
    tracking_path,
)


def rejet(symbol="REJ", reason="liquidité 12000 < 15000", price=1.0):
    return Candidate(
        token_address=f"addr_{symbol}", symbol=symbol, name=symbol, chain="solana",
        price_usd=price, liquidity_usd=12000, rejected_reason=reason,
    )


class TestFamillesDesGardesDu17Aout(unittest.TestCase):
    def test_lp_verrouillee_a_sa_propre_famille(self):
        """Tombait dans « autre » : la garde était invisible au comptage."""
        self.assertEqual(reason_family("LP verrouillée 12% < 50%"), "lp_lock")

    def test_concentration_meta_nest_pas_la_concentration_de_holders(self):
        """Deux risques différents, deux corrections différentes : les
        confondre ferait relâcher `max_top_wallet_concentration` pour un
        problème d'exposition de flotte."""
        self.assertEqual(
            reason_family("concentration meta « ia » — 3 position(s) déjà ouverte(s)"),
            "sector",
        )
        self.assertEqual(reason_family("top wallet 30% > 20%"), "concentration")

    def test_lp_lock_nest_pas_relachable_automatiquement(self):
        """Visible ne veut pas dire relâchable : c'est un vecteur de rug pull,
        au même titre que `rugcheck` et `authority`."""
        from src.core.learning import RELAXATIONS

        self.assertNotIn("lp_lock", RELAXATIONS)
        self.assertNotIn("sector", RELAXATIONS)

    def test_les_familles_deja_couvertes_ne_bougent_pas(self):
        """Garde-fou anti-régression : ajouter une branche en tête de fonction
        peut capturer des motifs qui appartenaient à une autre famille."""
        for motif, attendu in (
            ("liquidité 3000$ < 5000$", "liquidity"),
            ("âge 6.0h > 6h", "age_max"),
            ("âge 0.1h < 1.5h", "age_min"),
            ("volume 1h 500$ < 2000$", "volume"),
            ("rugcheck 40 < 60", "rugcheck"),
            ("alpha 62 < 70", "alpha"),
        ):
            self.assertEqual(reason_family(motif), attendu, motif)


class TestReprisDesSuivis(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "shadow_log.jsonl")

    def test_un_redemarrage_ne_perd_plus_les_suivis_en_cours(self):
        premier = ShadowTracker(self.path)
        premier.record_rejections([rejet("A"), rejet("B")])
        self.assertEqual(len(premier.tracked_addresses), 2)

        second = ShadowTracker(self.path)
        self.assertEqual(sorted(second.tracked_addresses), ["addr_A", "addr_B"])
        self.assertEqual(second.restored, 2)

    def test_le_pic_survit_au_redemarrage(self):
        """C'est la grandeur mesurée : la reprendre à zéro fabriquerait un
        verdict « n'a pas monté » sur un token qui avait monté."""
        premier = ShadowTracker(self.path)
        premier.record_rejections([rejet("A", price=1.0)])
        premier.update_price("addr_A", 3.0)
        premier.update_price("addr_A", 2.0)

        second = ShadowTracker(self.path)
        verdicts = self._forcer_expiration(second)
        self.assertEqual(len(verdicts), 1)
        self.assertAlmostEqual(verdicts[0].peak_price, 3.0)
        self.assertTrue(verdicts[0].would_have_won)

    def test_un_suivi_perime_pendant_larret_est_ecarte_pas_juge(self):
        """Le cas qui compte au retour de vacances. Personne n'a observé le
        token pendant la coupure : son `peak_price` date d'avant l'arrêt.
        Le juger écrirait un « n'a pas atteint +100% » qui ne mesure que la
        durée de l'arrêt, et ce verdict pèserait sur le desserrage des filtres.
        """
        premier = ShadowTracker(self.path)
        premier.record_rejections([rejet("VIEUX"), rejet("FRAIS")])
        with open(premier.tracking_path, encoding="utf-8") as fh:
            rows = json.load(fh)
        rows["addr_VIEUX"]["rejected_at"] = time.time() - (MAX_TRACKING_HOURS + 1) * 3600
        with open(premier.tracking_path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)

        second = ShadowTracker(self.path)
        self.assertEqual(second.tracked_addresses, ["addr_FRAIS"])
        self.assertEqual(second.dropped_stale, 1)
        self.assertEqual(second.restored, 1)
        # Écarté ne veut pas dire jugé : rien n'a été écrit au journal.
        self.assertEqual(second.read_all(), [])

    def test_un_fichier_de_reprise_illisible_ne_bloque_pas_le_demarrage(self):
        """Perdre les suivis en cours est acceptable ; ne pas démarrer, non."""
        with open(tracking_path(self.path), "w", encoding="utf-8") as fh:
            fh.write("{ceci n'est pas du json")

        tracker = ShadowTracker(self.path)
        self.assertEqual(tracker.tracked_addresses, [])
        self.assertEqual(tracker.restored, 0)
        tracker.record_rejections([rejet("A")])
        self.assertEqual(len(tracker.tracked_addresses), 1)

    def test_le_fichier_de_reprise_se_vide_quand_le_suivi_est_juge(self):
        """Sinon le fichier grossirait indéfiniment et rejugerait au démarrage
        des rejets déjà écrits au journal."""
        tracker = ShadowTracker(self.path)
        tracker.record_rejections([rejet("A")])
        self._forcer_expiration(tracker)

        self.assertEqual(tracker.tracked_addresses, [])
        with open(tracker.tracking_path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {})
        self.assertEqual(len(ShadowTracker(self.path).read_all()), 1)

    def test_le_fichier_de_reprise_est_a_cote_du_journal_du_bras(self):
        """Un par bras, comme le journal — sinon deux bras se marcheraient
        dessus et le témoin reprendrait les suivis de `sniper`."""
        self.assertEqual(
            tracking_path("data/arms/sniper/shadow_log.jsonl"),
            "data/arms/sniper/shadow_log_tracking.json",
        )
        self.assertEqual(
            tracking_path("data/shadow_log.jsonl"), "data/shadow_log_tracking.json"
        )

    def test_les_rejets_securite_ne_sont_toujours_pas_suivis(self):
        """Anti-régression : la persistance ne doit pas rouvrir la porte aux
        familles qu'on ne relâchera jamais."""
        tracker = ShadowTracker(self.path)
        tracker.record_rejections([rejet("H", reason="rugcheck 20 < 60")])
        self.assertEqual(tracker.tracked_addresses, [])
        self.assertFalse(os.path.exists(tracker.tracking_path))

    @staticmethod
    def _forcer_expiration(tracker):
        for entry in tracker._tracked.values():
            entry["rejected_at"] = time.time() - (MAX_TRACKING_HOURS * 3600 + 1)
        return tracker.expire()


if __name__ == "__main__":
    unittest.main()
