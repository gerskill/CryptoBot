"""Notifications — ce qui doit percer le silence, et ce qui doit rester muet.

LE SYMPTÔME RAPPORTÉ. Une position à +100 % visible sur le dashboard, rien sur
Telegram. Quatre défauts distincts derrière, tous verrouillés ici.

1. AUCUNE NOTIFICATION DE GAIN LATENT. `send_entry` et `send_exit` couvrent les
   deux extrémités du trade. Entre les deux, rien : `Meowt` est montée à +83 %
   sans qu'aucun message ne parte, parce qu'elle était encore OUVERTE.

2. `notify=none` TAISAIT AUSSI LES GROS GAINS. La politique a été écrite quand
   seul le témoin tradait. Depuis, il n'a rien pris en 5 h pendant que les six
   autres bras produisaient 27 gagnants sur 93 : « seul le témoin parle »
   revenait à taire exactement ce qu'on voulait voir.

3. LE PREMIER DIGEST ARRIVAIT 4 H APRÈS LE DÉMARRAGE. `_last_summary` était
   initialisé à `time.time()`, donc chaque redémarrage repoussait l'échéance —
   avec plusieurs relances dans la journée, il n'est jamais parti.

4. LA TRONCATURE ÉTAIT SILENCIEUSE. `messages[-5:]` faisait passer un digest
   amputé pour un digest complet.

CE QUI DOIT RESTER MUET : les pertes. 96 % des trades touchent -10 %. Les
alerter serait un bruit permanent, et un canal qu'on finit par ignorer ne
notifie plus rien du tout.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.arm import NOTABLE_GAIN_PCT, ArmNotifier  # noqa: E402
from src.main import (  # noqa: E402
    DIGEST_MAX_LINES,
    FIRST_SUMMARY_DELAY,
    GAIN_MILESTONES,
    SUMMARY_EVERY_SECONDS,
    AlphaLoop,
)


class FakeTelegram:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, message: str) -> None:
        self.sent.append(message)


class FakePosition:
    def __init__(self, pnl: float, high_water: float = 0.0, pid: str = "p1"):
        self.id = pid
        self.symbol = "MEOWT"
        self.size_usd = 20.0
        self.remaining_fraction = 1.0
        self.high_water_pct = high_water
        self._pnl = pnl

    def pnl_pct(self, price: float) -> float:
        return self._pnl


class TestPolitiqueDeSilence(unittest.TestCase):
    def setUp(self):
        self.inner = FakeTelegram()

    def _notifier(self, policy: str = "none") -> ArmNotifier:
        return ArmNotifier(self.inner, "runner", policy)

    def test_une_sortie_routiniere_reste_au_digest(self):
        notifier = self._notifier()
        notifier.send_exit(FakePosition(0), -25.0, "stop loss", 12.0)
        self.assertEqual(self.inner.sent, [])
        self.assertEqual(len(notifier.digest), 1)

    def test_une_grosse_perte_reste_au_digest(self):
        """96 % des trades touchent -10 %. Les alerter noierait le canal."""
        notifier = self._notifier()
        notifier.send_exit(FakePosition(0), -80.0, "stop loss", 12.0)
        self.assertEqual(self.inner.sent, [])

    def test_un_gros_gain_perce_le_silence(self):
        notifier = self._notifier()
        notifier.send_exit(FakePosition(0), NOTABLE_GAIN_PCT, "take profit", 30.0)
        self.assertEqual(len(self.inner.sent), 1)
        self.assertIn("runner", self.inner.sent[0])
        self.assertEqual(notifier.digest, [])

    def test_juste_sous_le_seuil_reste_muet(self):
        notifier = self._notifier()
        notifier.send_exit(FakePosition(0), NOTABLE_GAIN_PCT - 0.1, "TP", 30.0)
        self.assertEqual(self.inner.sent, [])

    def test_un_palier_part_toujours_meme_en_none(self):
        notifier = self._notifier()
        notifier.send_milestone(FakePosition(0), 83.4, 50.0)
        self.assertEqual(len(self.inner.sent), 1)
        self.assertIn("+50%", self.inner.sent[0])

    def test_le_seuil_est_dans_le_quart_superieur_des_trajectoires(self):
        """+50 % est franchi par 23 % des trades mesurés, +100 % par 15 %. Un
        seuil plus bas sonnerait à chaque trade : le pic médian est +4,9 %."""
        self.assertGreaterEqual(NOTABLE_GAIN_PCT, 50.0)


class TestPaliers(unittest.TestCase):
    def setUp(self):
        self.inner = FakeTelegram()
        self.arm = type("A", (), {})()
        self.arm.portfolio = type("P", (), {})()
        self.arm.portfolio.notifier = ArmNotifier(self.inner, "runner", "none")
        self.loop = type("L", (), {})()
        self.loop._milestones_sent = {}
        self.loop._announce_milestones = AlphaLoop._announce_milestones.__get__(
            self.loop
        )

    def test_le_cas_meowt_declenche_une_alerte(self):
        """+83 % de plus-haut sur une position ENCORE OUVERTE. C'est le cas
        exact rapporté : dashboard au courant, Telegram muet."""
        self.loop._announce_milestones(self.arm, FakePosition(83.4, 83.4), 1.0)
        self.assertEqual(len(self.inner.sent), 1)
        self.assertIn("+50%", self.inner.sent[0])

    def test_un_palier_nest_annonce_quune_fois(self):
        position = FakePosition(60.0, 60.0)
        for _ in range(20):
            self.loop._announce_milestones(self.arm, position, 1.0)
        self.assertEqual(len(self.inner.sent), 1)

    def test_les_paliers_superieurs_sannoncent_a_leur_tour(self):
        self.loop._announce_milestones(self.arm, FakePosition(60.0, 60.0), 1.0)
        self.loop._announce_milestones(self.arm, FakePosition(120.0, 120.0), 1.0)
        self.assertEqual(len(self.inner.sent), 2)
        self.assertIn("+100%", self.inner.sent[1])

    def test_un_bond_direct_annonce_tous_les_paliers_franchis(self):
        self.loop._announce_milestones(self.arm, FakePosition(160.0, 160.0), 1.0)
        self.assertEqual(len(self.inner.sent), len(GAIN_MILESTONES))

    def test_le_plus_haut_fait_foi_pas_le_prix_courant(self):
        """Un palier franchi puis reperdu a bien été franchi — et c'est
        justement le moment où l'information avait de la valeur."""
        redescendue = FakePosition(5.0, high_water=90.0)
        self.loop._announce_milestones(self.arm, redescendue, 1.0)
        self.assertEqual(len(self.inner.sent), 1)

    def test_une_position_ordinaire_ne_sonne_pas(self):
        """Le pic médian est +4,9 % : sans ça, chaque trade alerterait."""
        self.loop._announce_milestones(self.arm, FakePosition(12.0, 12.0), 1.0)
        self.assertEqual(self.inner.sent, [])

    def test_deux_positions_ont_des_paliers_independants(self):
        self.loop._announce_milestones(self.arm, FakePosition(60.0, 60.0, "p1"), 1.0)
        self.loop._announce_milestones(self.arm, FakePosition(60.0, 60.0, "p2"), 1.0)
        self.assertEqual(len(self.inner.sent), 2)

    def test_sans_notifier_rien_ne_leve(self):
        self.arm.portfolio.notifier = None
        self.loop._announce_milestones(self.arm, FakePosition(99.0, 99.0), 1.0)


class TestCadenceDuDigest(unittest.TestCase):
    def test_le_premier_digest_ne_part_pas_quatre_heures_apres_le_boot(self):
        """`_last_summary = time.time()` repoussait l'échéance à chaque
        relance. Avec plusieurs redémarrages dans la journée, le digest des
        stratégies muettes n'est jamais parti une seule fois."""
        self.assertLess(FIRST_SUMMARY_DELAY, SUMMARY_EVERY_SECONDS)
        self.assertLessEqual(FIRST_SUMMARY_DELAY, 30 * 60)

    def test_la_troncature_garde_de_quoi_couvrir_une_fenetre(self):
        """À 8-32 trades par jour et par bras, 5 lignes pour 4 h était trop
        bas — et la troncature était silencieuse."""
        self.assertGreaterEqual(DIGEST_MAX_LINES, 10)


if __name__ == "__main__":
    unittest.main()
