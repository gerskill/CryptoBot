import json, time
from src.core import copy_smart as cs

W = "8zkgFGVZrDLieViwqiXFCydSX6WL5hsxmUu55yBdsNsZ"
MINT = "MemeMint1111111111111111111111111111111111"


def _tx(pre, post, err=None):
    return {"blockTime": int(time.time()), "meta": {"err": err,
        "preTokenBalances": [{"owner": W, "mint": m, "uiTokenAmount": {"uiAmount": a}} for m, a in pre],
        "postTokenBalances": [{"owner": W, "mint": m, "uiTokenAmount": {"uiAmount": a}} for m, a in post]}}


def test_load_and_merge(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"wallets": [{"address": W}, {"address": "bad"}]}))
    manual = cs.load_manual_wallets(str(p))
    assert list(manual) == [W]
    assert cs.load_manual_wallets(str(tmp_path / "none.json")) == {}
    merged = cs.merge_manual({"A" * 40: {"win_rate": 58}}, manual)
    assert W in merged and "A" * 40 in merged


def test_real_config_contains_wallet():
    assert W in cs.load_manual_wallets("config/copy_smart_manual_wallets.json")


def test_trades_from_tx_buy_sell_and_quote_ignored():
    buy = cs.trades_from_tx(_tx([(cs.QUOTE_MINTS.__iter__().__next__(), 5)], [(MINT, 100)]), W)
    assert [(t["base_address"], t["side"]) for t in buy] == [(MINT, "buy")]
    sell = cs.trades_from_tx(_tx([(MINT, 100)], [(MINT, 0)]), W)
    assert sell[0]["side"] == "sell"
    assert cs.trades_from_tx(_tx([], [(MINT, 1)], err={"x": 1}), W) == []
    assert cs.trades_from_tx(None, W) == []


def test_watcher_feeds_copy_gate_and_sell_detection():
    calls = []
    now = int(time.time())

    def post(method, params):
        calls.append(method)
        if method == "getSignaturesForAddress":
            return [{"signature": "s2", "err": None, "blockTime": now},
                    {"signature": "s1", "err": None, "blockTime": now - 60},
                    {"signature": "bad", "err": {"e": 1}, "blockTime": now}]
        if params[0] == "s1":
            return _tx([], [(MINT, 100)])
        return _tx([(MINT, 100)], [(MINT, 0)])

    w = cs.ManualWalletWatcher(post=post, poll_sec=60)
    trades = w.trades([W])
    assert calls.count("getTransaction") == 2
    buys = cs.recent_copy_buys(trades, {W: {"manual": True}}, 10)
    assert W in buys[MINT]["wallets"]
    assert cs.copied_wallet_sold(trades, MINT, [W], 0) == W
    w.trades([W])  # throttled: no new RPC call
    assert calls.count("getSignaturesForAddress") == 1


def test_watcher_never_raises():
    def post(method, params):
        raise RuntimeError("down")
    w = cs.ManualWalletWatcher(post=post)
    assert w.trades([W]) == [] and w.errors == 1
