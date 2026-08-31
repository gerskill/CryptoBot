"""Durée du drawdown — combien de temps un bras reste sous son plus haut.

Le dépôt savait déjà dire à quelle profondeur un bras était descendu. Il ne
savait pas dire combien de temps il y restait, alors que c'est la grandeur la
plus stable des deux sur quelques dizaines de trades — et la seule qui répond
à « faut-il attendre ou arrêter ce bras ».
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.stats import drawdown_episodes, longest_drawdown  # noqa: E402


def trade(pnl_usd, heure=None):
    row = {"pnl_usd": pnl_usd}
    if heure is not None:
        row["timestamp_exit"] = f"2026-08-01T{heure:02d}:00:00+00:00"
    return row


class TestDureeDeDrawdown(unittest.TestCase):
    def test_equite_toujours_croissante_aucun_episode(self):
        """Jamais descendu sous son pic n'est pas un épisode de durée nulle."""
        self.assertEqual(drawdown_episodes([trade(10), trade(20)], 1000.0), [])
        self.assertIsNone(longest_drawdown([trade(10), trade(20)], 1000.0))

    def test_episode_referme_compte_ses_trades_et_ses_heures(self):
        # 1000 -> 1100 (pic) -> 1000 -> 950 (creux) -> 1150 (récupéré)
        positions = [
            trade(100, heure=1),   # pic
            trade(-100, heure=2),  # début du drawdown
            trade(-50, heure=3),   # creux
            trade(200, heure=9),   # récupération
        ]
        episodes = drawdown_episodes(positions, 1000.0)
        self.assertEqual(len(episodes), 1)
        episode = episodes[0]
        self.assertTrue(episode.recovered)
        self.assertEqual(episode.trades, 3)
        self.assertEqual(episode.hours, 7.0)
        # Creux à 950 depuis un pic à 1100 : 13,6 %.
        self.assertAlmostEqual(episode.depth_pct, 13.64, places=1)

    def test_episode_en_cours_nest_pas_un_episode_court(self):
        """Ne pas savoir quand il finira est un état distinct, pas un zéro."""
        positions = [trade(100, heure=1), trade(-300, heure=4)]
        episode = longest_drawdown(positions, 1000.0)
        self.assertIsNotNone(episode)
        self.assertFalse(episode.recovered)
        self.assertIn("TOUJOURS EN COURS", episode.format())

    def test_le_plus_long_nest_pas_le_plus_profond(self):
        """C'est tout l'intérêt de la mesure : les deux ne coïncident pas."""
        positions = [
            trade(-200),                                    # court et profond
            trade(300),                                     # récupéré
            *[trade(-5) for _ in range(10)],                # long et peu profond
            trade(100),
        ]
        episodes = drawdown_episodes(positions, 1000.0)
        plus_profond = max(episodes, key=lambda e: e.depth_pct)
        plus_long = longest_drawdown(positions, 1000.0)
        self.assertGreater(plus_profond.depth_pct, plus_long.depth_pct)
        self.assertGreater(plus_long.trades, plus_profond.trades)

    def test_date_absente_ne_bloque_pas_le_calcul(self):
        """Une donnée absente ne rejette jamais : l'épisode reste compté."""
        episode = longest_drawdown([trade(100), trade(-300)], 1000.0)
        self.assertEqual(episode.trades, 1)
        self.assertIsNone(episode.hours)
        self.assertNotIn("h", episode.format().replace("TOUJOURS", ""))


if __name__ == "__main__":
    unittest.main()
