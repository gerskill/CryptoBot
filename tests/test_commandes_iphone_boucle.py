"""La boucle applique les commandes iPhone comme celles de Telegram (ADR 021).

Le bug évité : une commande déposée par le Mac mais jamais lue par la boucle
(le téléphone dirait « appliquée » et rien ne se passerait), ou lue deux fois.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import main as boucle  # noqa: E402
from src.core import commandes  # noqa: E402


class Faux:
    def __init__(self, telegram_cmds):
        self.paused = True
        self.stops = 0
        self.envoyes = []
        self.telegram = mock.MagicMock()
        self.telegram.poll_commands.return_value = telegram_cmds
        self.telegram.send.side_effect = self.envoyes.append
        self.portfolio = mock.MagicMock()

    def _panic_exit(self):
        self.stops += 1


class TestBoucleLitLaBoite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "data"))
        patch = mock.patch.object(boucle.settings, "BASE_DIR", self.tmp)
        patch.start()
        self.addCleanup(patch.stop)

    def test_stop_depuis_l_iphone_declenche_le_panic_exit_une_fois(self):
        commandes.deposer(os.path.join(self.tmp, "data"), "stop", "0000000000001-1")
        faux = Faux([])
        boucle.AlphaLoop._handle_commands(faux)
        self.assertEqual(faux.stops, 1)
        boucle.AlphaLoop._handle_commands(faux)
        self.assertEqual(faux.stops, 1, "la commande ne doit pas être rejouée")
        self.assertTrue(any("iPhone" in m for m in faux.envoyes))

    def test_resume_depuis_l_iphone_et_telegram_ensemble(self):
        commandes.deposer(os.path.join(self.tmp, "data"), "resume", "0000000000001-1")
        faux = Faux(["/STATUS"])
        boucle.AlphaLoop._handle_commands(faux)
        self.assertFalse(faux.paused)
        faux.telegram.send_summary.assert_called_once()

    def test_rien_dans_la_boite_rien_envoye(self):
        faux = Faux([])
        boucle.AlphaLoop._handle_commands(faux)
        self.assertEqual(faux.envoyes, [])


if __name__ == "__main__":
    unittest.main()
