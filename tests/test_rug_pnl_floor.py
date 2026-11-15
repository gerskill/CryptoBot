from src.core.journal import correct_legacy_cost
from src.core.positions import cost_vs_entry


def test_exit_cost_capped_at_100pct_of_sold():
    # going/up tp1_30 2026-11-04 : prix -68 %, devis 512 % -> perte <= mise
    entry, price = 0.0005025, 0.0001606
    cost = cost_vs_entry(512.205, price, entry)
    pnl = (price / entry - 1) * 100 - cost
    assert pnl >= -100.0 - 1e-9


def test_normal_cost_unchanged():
    assert cost_vs_entry(3.0, 1.0, 1.0) == 3.0


def test_read_floor_on_absurd_row():
    row = {"cost_basis": "stake", "entry_price": 0.0005025, "pnl_pct": -231.74, "pnl_usd": -46.0846}
    out = correct_legacy_cost(dict(row))
    assert out["pnl_pct"] == -100.0
    assert out["pnl_pct_journal"] == -231.74
    assert abs(out["pnl_usd"] - (-46.0846 * 100 / 231.74)) < 1e-3


def test_read_leaves_normal_row():
    row = {"cost_basis": "stake", "pnl_pct": -20.0, "pnl_usd": -5.0}
    assert correct_legacy_cost(dict(row))["pnl_pct"] == -20.0
