"""Reporter Telegram — percer le silence sans créer de saturation.

LE DÉFAUT RÉPARÉ, mesuré le 2026-08-02 sur 108 trades clôturés : **16 sorties
à +50 % ou plus n'ont jamais été notifiées**, dont JORDAN +184,2 %, WOJAKOS
+158,6 %, CATECOIN +123,9 % et GOONER +107,2 %. Six bras sur sept étaient en
`notify=none`, et leur digest ne partait jamais.

LE DÉFAUT SYMÉTRIQUE À NE PAS CRÉER : tout envoyer. À 8-32 trades par jour et
par bras, entrées comprises, c'est plusieurs centaines de messages quotidiens.
Un canal qu'on coupe ne notifie plus rien — le silence par saturation vaut le
silence par politique.

Ces tests verrouillent donc les deux bords à la fois.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents import _journal  # noqa: E402
from src.agents.telegram_reporter import (  # noqa: E402
    BATCH_MAX_LINES,
    NOTABLE_GAIN_PCT,
    TelegramReporterAgent,
    is_notable,
)


class FakeTelegram:
    def __init__(self, boom=False):
        self.sent: list[str] = []
        self.boom = boom

    def send(self, message: str) -> None:
        if self.boom:
            raise RuntimeError("réseau")
        self.sent.append(message)


class FakePosition:
    def __init__(self, symbol="GOONER"):
        self.symbol = symbol
        self.size_usd = 20.0
        self.stop_loss_pct = -40
        self.remaining_fraction = 1.0


class FakeJournal:
    def __init__(self, rows):
        self._rows = rows

    def read_positions(self):
        return self._rows


def _arm(name, pnls):
    obj = type("A", (), {})()
    obj.name = name
    obj.journal = FakeJournal([
        {"position_id": f"{name}{i}", "pnl_usd": p} for i, p in enumerate(pnls)
    ])
    return obj


class TestRemarquable(unittest.TestCase):
    def test_un_gros_gain_est_remarquable(self):
        self.assertTrue(is_notable(NOTABLE_GAIN_PCT))
        self.assertTrue(is_notable(107.2))

    def test_une_perte_meme_lourde_ne_lest_pas(self):
        """96 % des trades touchent -10 %. Les alerter serait du bruit."""
        self.assertFalse(is_notable(-80.0, "STOP_LOSS"))

    def test_un_rug_lest_quel_que_soit_le_pnl(self):
        """Ce n'est pas un résultat de trading mais un incident : il renseigne
        sur la SÉLECTION, pas sur la stratégie de sortie."""
        self.assertTrue(is_notable(-90.0, "RUG_PULL (liquidité -70%)"))

    def test_juste_sous_le_seuil_ne_lest_pas(self):
        self.assertFalse(is_notable(NOTABLE_GAIN_PCT - 0.1, "TAKE_PROFIT_1"))


class TestSorties(unittest.TestCase):
    def setUp(self):
        self.tg = FakeTelegram()
        self.agent = TelegramReporterAgent(inner=self.tg)

    def test_le_cas_gooner_part_immediatement(self):
        """+107,2 % en 10 min sur `sniper`, un bras en `notify=none`. Sous
        l'ancienne politique, rien ne partait."""
        envoye = self.agent.report_exit(
            "sniper", FakePosition(), 107.16, "TRAILING_STOP", 10.3
        )
        self.assertTrue(envoye)
        self.assertEqual(len(self.tg.sent), 1)
        self.assertIn("GOONER", self.tg.sent[0])
        self.assertIn("sniper", self.tg.sent[0])

    def test_le_bras_nentre_pas_dans_la_decision_denvoyer(self):
        """La valeur d'un +184 % ne dépend pas de quelle stratégie l'a produit.
        C'était le défaut de fond de la politique par bras."""
        for bras in ("baseline", "sniper", "runner", "narrative", "quality"):
            agent = TelegramReporterAgent(inner=FakeTelegram())
            self.assertTrue(agent.report_exit(bras, FakePosition(), 184.2, "TP", 5.0))

    def test_une_sortie_routiniere_attend_le_lot(self):
        self.agent.report_exit("sniper", FakePosition(), -25.0, "STOP_LOSS", 12.0)
        self.assertEqual(self.tg.sent, [])
        self.assertEqual(len(self.agent._batch), 1)

    def test_un_rug_ne_passe_pas_par_le_lot(self):
        self.assertTrue(
            self.agent.report_exit("sniper", FakePosition(), -70.0, "RUG_PULL", 2.0)
        )

    def test_une_entree_est_toujours_groupee(self):
        """Une entrée n'apprend rien seule : ce qui compte est ce qu'elle
        devient, et ce sera republié à la sortie."""
        self.agent.report_entry("sniper", FakePosition())
        self.assertEqual(self.tg.sent, [])
        self.assertEqual(len(self.agent._batch), 1)

    def test_un_palier_part_toujours(self):
        """GOONER a fait son plus-haut à +135,7 % et n'est sorti qu'à +107,2 %.
        Ni l'entrée ni la sortie ne couvrent cet instant."""
        self.assertTrue(
            self.agent.report_milestone("sniper", FakePosition(), 135.7, 100.0)
        )
        self.assertIn("+100%", self.tg.sent[0])


class TestSaturation(unittest.TestCase):
    def setUp(self):
        self.tg = FakeTelegram()
        self.agent = TelegramReporterAgent(inner=self.tg, batch_window=100.0)

    def test_le_lot_ne_part_pas_avant_sa_fenetre(self):
        self.agent.report_entry("sniper", FakePosition())
        self.assertFalse(self.agent.flush_batch(now=self.agent._last_batch_at + 10))
        self.assertEqual(self.tg.sent, [])

    def test_le_lot_part_en_un_seul_message(self):
        for _ in range(8):
            self.agent.report_entry("sniper", FakePosition())
        self.assertTrue(self.agent.flush_batch(now=self.agent._last_batch_at + 200))
        self.assertEqual(len(self.tg.sent), 1)
        self.assertEqual(self.tg.sent[0].count("GOONER"), 8)

    def test_un_lot_vide_nenvoie_rien(self):
        self.assertFalse(self.agent.flush_batch(force=True))
        self.assertEqual(self.tg.sent, [])

    def test_la_troncature_est_annoncee(self):
        """Un lot amputé présenté comme complet est pire qu'un lot tronqué
        annoncé."""
        for _ in range(BATCH_MAX_LINES + 7):
            self.agent.report_entry("sniper", FakePosition())
        self.agent.flush_batch(force=True)
        self.assertIn("7 de plus", self.tg.sent[0])

    def test_une_alerte_repetee_ne_part_quune_fois(self):
        """Sans refroidissement, « Birdeye indisponible » partirait 960 fois
        par jour et l'alerte deviendrait le bruit qu'elle doit percer."""
        for _ in range(50):
            self.agent.report_alert("birdeye", "quota mort")
        self.assertEqual(len(self.tg.sent), 1)

    def test_deux_alertes_differentes_partent_toutes_les_deux(self):
        self.agent.report_alert("birdeye", "quota mort")
        self.agent.report_alert("jupiter", "429")
        self.assertEqual(len(self.tg.sent), 2)

    def test_lalerte_repart_apres_le_refroidissement(self):
        agent = TelegramReporterAgent(inner=self.tg, alert_cooldown=0.0)
        agent.report_alert("birdeye", "quota mort")
        agent.report_alert("birdeye", "quota mort")
        self.assertEqual(len(self.tg.sent), 2)


class TestRapport(unittest.TestCase):
    def setUp(self):
        self.tg = FakeTelegram()
        self.agent = TelegramReporterAgent(inner=self.tg)

    def test_le_rapport_porte_lintervalle_pas_seulement_le_point(self):
        """Sur les échantillons de ce projet, un P&L moyen nu est de la fausse
        précision. Le rapport doit dire ce qu'on ignore."""
        arms = [_arm("sniper", [10.0, -4.0, -4.0, 20.0] * 6)]
        self.agent.report_periodic(arms, force=True)
        self.assertIn("IC95", self.tg.sent[0])

    def test_un_intervalle_qui_contient_zero_ne_conclut_pas_sur_le_signe(self):
        """PAS `Interval.conclusive` : son seuil de 12 « points » vise des
        PROPORTIONS, alors que `pnl_per_trade_interval` rend des DOLLARS. Un
        intervalle de 4 $ de large y passerait pour concluant par collision
        d'unités. Le seul verdict qui a un sens sur un P&L est le signe."""
        arms = [_arm("runner", [40.0, -5.0, -5.0, -5.0, 25.0])]
        self.agent.report_periodic(arms, force=True)
        self.assertIn("ne conclut pas sur le signe", self.tg.sent[0])

    def test_une_perte_demontree_est_nommee(self):
        arms = [_arm("baseline", [-4.0] * 40)]
        self.agent.report_periodic(arms, force=True)
        self.assertIn("perte démontrée", self.tg.sent[0])

    def test_un_gain_demontre_est_nomme(self):
        arms = [_arm("licorne", [8.0] * 40)]
        self.agent.report_periodic(arms, force=True)
        self.assertIn("gain démontré", self.tg.sent[0])

    def test_chaque_bras_a_sa_ligne(self):
        arms = [_arm("sniper", [1.0, -1.0]), _arm("runner", [5.0])]
        self.agent.report_periodic(arms, force=True)
        self.assertIn("sniper", self.tg.sent[0])
        self.assertIn("runner", self.tg.sent[0])

    def test_un_bras_sans_trade_na_pas_de_ligne(self):
        arms = [_arm("sniper", [1.0]), _arm("narrative", [])]
        self.agent.report_periodic(arms, force=True)
        self.assertNotIn("narrative", self.tg.sent[0])

    def test_aucun_trade_du_tout_le_dit_sans_diviser_par_zero(self):
        self.agent.report_periodic([_arm("narrative", [])], force=True)
        self.assertIn("Aucun trade", self.tg.sent[0])

    def test_le_rapport_ne_part_pas_avant_sa_cadence(self):
        self.assertFalse(self.agent.report_periodic([_arm("sniper", [1.0])]))
        self.assertEqual(self.tg.sent, [])


class TestRobustesse(unittest.TestCase):
    def test_sans_telegram_lagent_reste_fonctionnel(self):
        """Un déploiement sans jeton emprunte le même chemin que la production."""
        agent = TelegramReporterAgent(inner=None)
        self.assertFalse(agent.report_exit("sniper", FakePosition(), 107.0, "TP", 10))
        agent.report_entry("sniper", FakePosition())
        self.assertEqual(len(agent._batch), 1)

    def test_une_panne_reseau_nemporte_pas_la_boucle(self):
        """La notification est postérieure au trade, qui est déjà au journal."""
        agent = TelegramReporterAgent(inner=FakeTelegram(boom=True))
        self.assertFalse(agent.report_exit("sniper", FakePosition(), 107.0, "TP", 10))

    def test_le_tick_absorbe_tout(self):
        casse = type("A", (), {})()
        casse.name = "casse"
        casse.journal = property(lambda self: 1 / 0)
        TelegramReporterAgent(inner=FakeTelegram()).tick([casse])

    def test_les_envois_sont_journalises_succes_comme_echec(self):
        tmp = tempfile.mkdtemp()
        log = os.path.join(tmp, "telegram_reporter_log.jsonl")
        TelegramReporterAgent(inner=FakeTelegram(boom=True), log_path=log).report_exit(
            "sniper", FakePosition(), 107.0, "TP", 10
        )
        rows = _journal.read(log)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["sent"])
        self.assertEqual(rows[0]["kind"], "exit_notable")


if __name__ == "__main__":
    unittest.main()
