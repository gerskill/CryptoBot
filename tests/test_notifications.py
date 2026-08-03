"""Câblage des notifications — de la position jusqu'au reporter.

CE QUI EST TESTÉ ICI, et ce qui ne l'est pas. Les règles d'envoi (qu'est-ce qui
part tout de suite, qu'est-ce qui attend le lot) appartiennent à
`TelegramReporterAgent` et sont couvertes par `test_agents_telegram_reporter`.
Ce fichier vérifie le CHEMIN : qu'un événement produit par la boucle ou par un
portefeuille arrive bien jusqu'au reporter, avec le nom du bras.

LE DÉFAUT RÉPARÉ. Mesuré le 2026-08-02 sur 108 trades clôturés : **16 sorties
à +50 % ou plus n'ont jamais été notifiées**, dont JORDAN +184,2 %, WOJAKOS
+158,6 %, CATECOIN +123,9 % et GOONER +107,2 %. Trois causes empilées :

  1. `ArmNotifier` décidait par BRAS — six sur sept étaient à `notify=none` ;
  2. leur digest était cadencé à 4 h et son compteur repartait à zéro à chaque
     redémarrage, donc il n'est jamais parti ;
  3. rien ne couvrait le gain LATENT : GOONER a culminé à +135,7 % et n'est
     sorti qu'à +107,2 %, sans qu'aucun message ne parte entre les deux.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents.telegram_reporter import TelegramReporterAgent  # noqa: E402
from src.core.arm import ArmNotifier  # noqa: E402
from src.main import GAIN_MILESTONES, AlphaLoop  # noqa: E402


class FakeTelegram:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, message: str) -> None:
        self.sent.append(message)


class FakePosition:
    def __init__(self, pnl: float, high_water: float = 0.0, pid: str = "p1",
                 symbol: str = "MEOWT"):
        self.id = pid
        self.symbol = symbol
        self.size_usd = 20.0
        self.stop_loss_pct = -40
        self.remaining_fraction = 1.0
        self.high_water_pct = high_water
        self._pnl = pnl

    def pnl_pct(self, price: float) -> float:
        return self._pnl


class TestAdaptateurDeBras(unittest.TestCase):
    """`ArmNotifier` ne décide plus rien : il nomme le bras et transmet."""

    def setUp(self):
        self.tg = FakeTelegram()
        self.reporter = TelegramReporterAgent(inner=self.tg)
        self.notifier = ArmNotifier(self.reporter, "sniper", "none")

    def test_le_cas_gooner_arrive_jusqua_telegram(self):
        """+107,2 % sur `sniper`, un bras qui était à `notify=none`."""
        self.notifier.send_exit(FakePosition(0, symbol="GOONER"), 107.16,
                                "TRAILING_STOP", 10.3)
        self.assertEqual(len(self.tg.sent), 1)
        self.assertIn("GOONER", self.tg.sent[0])
        self.assertIn("sniper", self.tg.sent[0])

    def test_la_politique_du_manifeste_ne_bloque_plus_rien(self):
        """`notify` reste accepté pour compatibilité, mais ne décide plus.
        La valeur d'un +184 % ne dépend pas de quelle stratégie l'a produit."""
        for politique in ("none", "exits", "all"):
            tg = FakeTelegram()
            notifier = ArmNotifier(TelegramReporterAgent(inner=tg), "runner", politique)
            notifier.send_exit(FakePosition(0, symbol="JORDAN"), 184.2, "TP", 5.0)
            self.assertEqual(len(tg.sent), 1, politique)

    def test_une_entree_transite_par_le_lot(self):
        self.notifier.send_entry(FakePosition(0))
        self.assertEqual(self.tg.sent, [])
        self.assertEqual(len(self.reporter._batch), 1)

    def test_une_sortie_routiniere_transite_par_le_lot(self):
        self.notifier.send_exit(FakePosition(0), -25.0, "STOP_LOSS", 12.0)
        self.assertEqual(self.tg.sent, [])
        self.assertEqual(len(self.reporter._batch), 1)

    def test_sans_reporter_rien_ne_leve(self):
        muet = ArmNotifier(None, "sniper", "none")
        muet.send_entry(FakePosition(0))
        muet.send_exit(FakePosition(0), 107.0, "TP", 10.0)
        muet.send_milestone(FakePosition(0), 107.0, 100.0)


class TestPaliers(unittest.TestCase):
    """Le gain LATENT, que ni l'entrée ni la sortie ne couvrent."""

    def setUp(self):
        self.tg = FakeTelegram()
        reporter = TelegramReporterAgent(inner=self.tg)
        self.arm = type("A", (), {})()
        self.arm.portfolio = type("P", (), {})()
        self.arm.portfolio.notifier = ArmNotifier(reporter, "runner", "none")
        self.loop = type("L", (), {})()
        self.loop._milestones_sent = {}
        self.loop._announce_milestones = AlphaLoop._announce_milestones.__get__(
            self.loop
        )

    def test_le_cas_meowt_declenche_une_alerte(self):
        """+83 % de plus-haut sur une position ENCORE OUVERTE : dashboard au
        courant, Telegram muet."""
        self.loop._announce_milestones(self.arm, FakePosition(83.4, 83.4), 1.0)
        self.assertEqual(len(self.tg.sent), 1)
        self.assertIn("+50%", self.tg.sent[0])

    def test_un_palier_nest_annonce_quune_fois(self):
        position = FakePosition(60.0, 60.0)
        for _ in range(20):
            self.loop._announce_milestones(self.arm, position, 1.0)
        self.assertEqual(len(self.tg.sent), 1)

    def test_les_paliers_superieurs_sannoncent_a_leur_tour(self):
        self.loop._announce_milestones(self.arm, FakePosition(60.0, 60.0), 1.0)
        self.loop._announce_milestones(self.arm, FakePosition(120.0, 120.0), 1.0)
        self.assertEqual(len(self.tg.sent), 2)
        self.assertIn("+100%", self.tg.sent[1])

    def test_le_cas_gooner_aurait_sonne_deux_fois_avant_la_sortie(self):
        """Pic à +135,7 %, sortie à +107,2 %. Les paliers +50 et +100 devaient
        partir PENDANT la détention, pas après."""
        self.loop._announce_milestones(
            self.arm, FakePosition(135.7, 135.7, symbol="GOONER"), 1.0
        )
        self.assertEqual(len(self.tg.sent), 2)

    def test_un_bond_direct_annonce_tous_les_paliers_franchis(self):
        self.loop._announce_milestones(self.arm, FakePosition(160.0, 160.0), 1.0)
        self.assertEqual(len(self.tg.sent), len(GAIN_MILESTONES))

    def test_le_plus_haut_fait_foi_pas_le_prix_courant(self):
        """Un palier franchi puis reperdu a bien été franchi — et c'est
        justement le moment où l'information avait de la valeur."""
        redescendue = FakePosition(5.0, high_water=90.0)
        self.loop._announce_milestones(self.arm, redescendue, 1.0)
        self.assertEqual(len(self.tg.sent), 1)

    def test_une_position_ordinaire_ne_sonne_pas(self):
        """Le pic médian est +4,9 % : sans seuil, chaque trade alerterait."""
        self.loop._announce_milestones(self.arm, FakePosition(12.0, 12.0), 1.0)
        self.assertEqual(self.tg.sent, [])

    def test_deux_positions_ont_des_paliers_independants(self):
        self.loop._announce_milestones(self.arm, FakePosition(60.0, 60.0, "p1"), 1.0)
        self.loop._announce_milestones(self.arm, FakePosition(60.0, 60.0, "p2"), 1.0)
        self.assertEqual(len(self.tg.sent), 2)

    def test_sans_notifier_rien_ne_leve(self):
        self.arm.portfolio.notifier = None
        self.loop._announce_milestones(self.arm, FakePosition(99.0, 99.0), 1.0)


class TestRoutageDesAjustements(unittest.TestCase):
    """`_after_trade_closed` n'envoyait les décisions QUE pour le témoin.

    Même classe de défaut que les 16 sorties jamais notifiées, une couche plus
    profonde : `arm.is_baseline` limitait la visibilité, et le chemin
    contournait en plus le reporter via `self.telegram.send()` direct. Cinq
    bras sur six ajustaient leurs paramètres en silence.
    """

    def setUp(self):
        self.tg = FakeTelegram()
        self.reporter = TelegramReporterAgent(inner=self.tg)

    def test_un_bras_non_temoin_est_notifie(self):
        notifier = ArmNotifier(self.reporter, "quality", "none")
        notifier.send_learning("filters.min_liquidity_usd -> 15000")
        self.reporter.flush_batch(force=True)
        self.assertEqual(len(self.tg.sent), 1)
        self.assertIn("quality", self.tg.sent[0])

    def test_un_ajustement_et_un_cooldown_ne_se_collisionnent_pas(self):
        """`send` (cooldown) et `send_learning` (ajustement) partagent le même
        bras : sous le même `kind`, l'un écraserait l'autre dans la fenêtre de
        déduplication d'une heure."""
        notifier = ArmNotifier(self.reporter, "sniper", "none")
        notifier.send("COOLDOWN 2h")
        notifier.send_learning("exit_rules.stop_loss_slippage_buffer_pct -> 9.8")
        self.reporter.flush_batch(force=True)
        # Le cooldown part immédiatement (alerte) ; l'ajustement rejoint le lot.
        self.assertEqual(len(self.tg.sent), 2)

    def test_learning_est_toujours_groupe_jamais_immediat(self):
        notifier = ArmNotifier(self.reporter, "runner", "none")
        notifier.send_learning("exit_rules.max_hold_time_minutes -> 270")
        self.assertEqual(self.tg.sent, [])
        self.assertEqual(len(self.reporter._batch), 1)


if __name__ == "__main__":
    unittest.main()
