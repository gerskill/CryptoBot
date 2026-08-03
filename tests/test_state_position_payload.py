"""`position_to_dict` — le contrat entre `Position` et la carte du dashboard.

`low_water_pct` existe sur `Position` depuis le début (instrumentation
pic/creux) mais n'était jamais sorti vers l'API : le dashboard pouvait
afficher le plus-haut depuis l'entrée mais pas le plus-bas, alors que les deux
sont symétriques et déjà mesurés.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.positions import Position  # noqa: E402
from src.core.state import position_to_dict  # noqa: E402


def _position(**overrides):
    base = dict(
        id="p1", token_address="addr", symbol="TOK", chain="solana",
        pair_address=None, entry_price=1.0, size_usd=20.0,
        stop_loss_pct=-25.0, take_profit_1=100.0, take_profit_2=300.0,
        take_profit_3=500.0, entry_time=1_000_000.0,
    )
    base.update(overrides)
    return Position(**base)


class TestPositionToDict(unittest.TestCase):
    def test_porte_le_plus_haut_et_le_plus_bas(self):
        position = _position(high_water_pct=42.0, low_water_pct=-18.0)
        payload = position_to_dict(position, price=1.1)
        self.assertEqual(payload["high_water_pct"], 42.0)
        self.assertEqual(payload["low_water_pct"], -18.0)

    def test_le_plus_bas_par_defaut_est_zero_pas_absent(self):
        """Une position qui vient d'ouvrir n'a encore vu aucun creux réel :
        0.0 le dit, l'absence du champ obligerait le frontend à deviner."""
        payload = position_to_dict(_position(), price=1.0)
        self.assertIn("low_water_pct", payload)
        self.assertEqual(payload["low_water_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
