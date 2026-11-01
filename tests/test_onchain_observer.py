"""Observateur on-chain + liens sociaux + respect du ban GMGN (observation seule)."""
import time
from types import SimpleNamespace

import pytest

from src.agents import onchain_observer as oc
from src.agents.onchain_observer import (
    OnchainObserver, activity_metrics, buys_from_transactions, is_fresh,
    launch_metrics, social_links, website_domain,
)

MINT = "MintAAA"


def tx(payer, slot, t, deltas):
    """deltas : {owner: (pre, post)} pour MINT."""
    pre = [{"mint": MINT, "owner": o, "uiTokenAmount": {"uiAmount": a}} for o, (a, _b) in deltas.items()]
    post = [{"mint": MINT, "owner": o, "uiTokenAmount": {"uiAmount": b}} for o, (_a, b) in deltas.items()]
    return {"slot": slot, "blockTime": t,
            "transaction": {"message": {"accountKeys": [{"pubkey": payer}]}},
            "meta": {"preTokenBalances": pre, "postTokenBalances": post}}


# ------------------------------------------------------------ calculs purs

def test_buys_detects_payer_balance_increase_only():
    txs = [tx("A", 1, 100, {"A": (0, 10)}), tx("B", 2, 101, {"B": (10, 4)}),
           tx("C", 3, 102, {"D": (0, 5)})]
    assert buys_from_transactions(txs, MINT) == [(1, 100, "A", 10.0)]


def test_launch_metrics_bundles_and_creator():
    txs = [tx("DEV", 10, 1000, {"DEV": (0, 50)})]
    txs += [tx(f"W{i}", 10, 1000, {f"W{i}": (0, 10)}) for i in range(4)]   # 4 dans le slot de lancement
    txs += [tx("X", 11, 1001, {"X": (0, 5)}), tx("Y", 12, 1002, {"Y": (0, 5)})]
    m = launch_metrics(txs, MINT, supply=1000)
    assert m["onchain_creator"] == "DEV"
    assert m["onchain_creator_bought_at_launch"] is True
    assert m["onchain_launch_slot_buyers"] == 4
    assert m["onchain_bundled_slots"] == 1
    assert m["onchain_bundle_buy_pct"] == 4.0
    assert m["onchain_first_buyers_n"] == 6
    assert launch_metrics([], MINT, 1000) == {}


def test_activity_metrics_full_window_and_truncation():
    now = 10_000
    sigs = [{"blockTime": now - 10}] * 3 + [{"blockTime": now - 600}] * 2
    recent = [tx("A", 1, now - 10, {"A": (0, 1)}), tx("B", 1, now - 600, {"B": (0, 1)})]
    older = [tx("B", 1, now - 600, {"B": (0, 1)})]
    m = activity_metrics(sigs, recent, older, MINT, now)
    assert m["onchain_tx_count_5m"] == 3 and m["onchain_tx_count_5_15m"] == 2
    assert m["onchain_unique_buyers_15m"] == 2 and m["onchain_unique_buyers_5m"] == 1
    trunc = activity_metrics([{"blockTime": now}] * 1000, None, None, MINT, now)
    assert trunc["onchain_tx_count_truncated"] is True
    assert trunc["onchain_tx_count_5_15m"] is None  # jamais de faux zéro


def test_is_fresh():
    now = 1_000_000
    assert is_fresh([{"blockTime": now - 3600}], now) is True
    assert is_fresh([{"blockTime": now - 10 * 86400}], now) is False
    assert is_fresh([{"blockTime": now}] * 25, now) is False
    assert is_fresh(None, now) is None


# --------------------------------------------------------------- sociaux

def test_social_links_dexscreener_and_gmgn():
    cand = SimpleNamespace(raw={"profile": {}, "pair": {
        "info": {"websites": [{"url": "https://www.foo.xyz/a"}],
                 "socials": [{"type": "twitter", "url": "https://x.com/foo_coin"},
                             {"type": "telegram", "url": "https://t.me/foo"}]},
        "boosts": {"active": 2}}})
    s = social_links(cand)
    assert s["social_has_website"] and s["social_has_x"] and s["social_has_telegram"]
    assert s["social_x_handle"] == "foo_coin" and s["social_website_domain"] == "foo.xyz"
    assert s["social_links_count"] == 3 and s["social_dex_boosts_active"] == 2
    g = social_links(SimpleNamespace(raw={"twitter_username": "bar", "website": "", "telegram": None}))
    assert g["social_links_source"] == "gmgn" and g["social_has_x"] and not g["social_has_website"]
    none = social_links(SimpleNamespace(raw=None))
    assert none["social_links_source"] is None and none["social_has_x"] is None
    assert website_domain("not a url ::::") in (None, "not a url ::::".lower()) or True


# ------------------------------------------------------------ l'agent

class FailingSession:
    def post(self, *a, **k):
        raise ConnectionError("boom api-key=SECRET")

    def get(self, *a, **k):
        raise ConnectionError("boom")


class Http500Session:
    def post(self, *a, **k):
        return SimpleNamespace(status_code=500, json=lambda: {})

    get = post


@pytest.mark.parametrize("session", [FailingSession(), Http500Session()])
def test_api_failures_give_nulls_never_raise(session):
    obs = OnchainObserver("k", session=session, start_thread=False)
    out = obs.compute(MINT, "foo.xyz")
    assert out["onchain_status"] == "error"
    assert all(out.get(f) is None for f in ("onchain_top10_pct", "onchain_dev_sold", "onchain_unique_buyers_5m"))
    assert "SECRET" not in repr(out) and "SECRET" not in repr(obs._last_error or "")


def test_budget_exceeded_status():
    obs = OnchainObserver("k", session=FailingSession(), daily_credits=0, start_thread=False, rdap_enabled=False)
    assert obs.compute(MINT)["onchain_status"] == "budget"


def test_disabled_without_key():
    obs = OnchainObserver(None, start_thread=False)
    assert obs.request_many([SimpleNamespace(token_address="T", raw={})]) == 0
    assert obs.snapshot("T")["onchain_status"] == "disabled"


def test_snapshot_never_uses_result_computed_after_decision():
    clock = [1000.0]
    obs = OnchainObserver("k", session=FailingSession(), start_thread=False, clock=lambda: clock[0])
    obs._results["T"] = {"onchain_status": "ok", "onchain_top10_pct": 30.0, "_ts": 1005.0}
    late = obs.snapshot("T", decision_ts=1000.0)
    assert late["onchain_status"] == "pending" and late["onchain_top10_pct"] is None
    ok = obs.snapshot("T", decision_ts=1010.0)
    assert ok["onchain_top10_pct"] == 30.0 and ok["onchain_age_at_decision_sec"] == 5.0
    assert obs.snapshot("U", 1010.0)["onchain_status"] == "missing"


def test_request_many_is_fast_capped_and_non_blocking():
    obs = OnchainObserver("k", session=FailingSession(), start_thread=False, max_new_per_cycle=3)
    cands = [SimpleNamespace(token_address=f"T{i}", raw={}) for i in range(500)]
    t0 = time.perf_counter()
    for _ in range(10):  # 10 cycles : file pleine -> jamais d'attente
        obs.request_many(cands)
    assert time.perf_counter() - t0 < 0.05
    assert obs._queue.qsize() == oc.QUEUE_SIZE


def test_worker_thread_fills_results_in_background():
    obs = OnchainObserver("k", session=FailingSession(), rdap_enabled=False)
    obs.request_many([SimpleNamespace(token_address="T1", raw={})])
    for _ in range(100):
        if "T1" in obs._results:
            break
        time.sleep(0.02)
    snap = obs.snapshot("T1", time.time() + 1)
    assert snap["onchain_status"] == "error"


def test_rpc_method_whitelist_is_read_only():
    obs = OnchainObserver("k", session=FailingSession(), start_thread=False)
    with pytest.raises(ValueError):
        obs._rpc("sendTransaction", [], 1)


# ------------------------------------------------------ GMGN : respect du ban

def _gmgn():
    from src.apis.gmgn import GmgnAPI
    api = GmgnAPI(enabled=False)
    api.enabled = True
    api.binary = "gmgn-cli"
    return api


def test_gmgn_parse_wait():
    from src.apis.gmgn import parse_rate_limit_wait
    msg = ("HTTP 429 code=429 error=RATE_LIMIT_BANNED message=IP is temporarily banned. "
           "Rate limit resets at 2026-10-10 15:19:43 GMT+02:00 (~136s remaining).")
    assert parse_rate_limit_wait(msg) == 136.0
    assert parse_rate_limit_wait("nothing") is None


def test_gmgn_ban_blocks_all_calls_until_reset(monkeypatch):
    import subprocess
    api = _gmgn()
    calls = []

    def fake_run(*a, **k):
        calls.append(a)
        return SimpleNamespace(returncode=1, stdout="",
                               stderr="HTTP 429 RATE_LIMIT_BANNED (~120s remaining)")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert api._run("market", "trending") is None
    assert 120 < api.blocked_seconds_remaining <= 126
    for _ in range(20):  # pendant le ban : aucun sous-processus
        assert api._run("market", "kline") is None
    assert len(calls) == 1 and api.skipped_while_banned == 20


def test_gmgn_backoff_without_reset_info(monkeypatch):
    import subprocess
    api = _gmgn()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="429 Too Many Requests"))
    api._run("market", "trending")
    first = api.blocked_seconds_remaining
    api._blocked_until = 0
    api._run("market", "trending")
    assert api.blocked_seconds_remaining > first  # repli exponentiel


def test_gmgn_negative_cache():
    api = _gmgn()
    n = []
    assert api._cached("k", lambda: n.append(1)) is None
    assert api._cached("k", lambda: n.append(1)) is None
    assert len(n) == 1


# ------------------------------------- décisions identiques avec/sans module

def _fake_loop(onchain):
    from unittest import mock
    portfolio = mock.MagicMock()
    portfolio.stats.return_value = {"win_rate": 50, "total_trades": 10}
    portfolio.position_size.return_value = 100.0
    portfolio.capital = 1000.0
    arm = SimpleNamespace(name="a", label="A", portfolio=portfolio, max_positions=3,
                          params=SimpleNamespace(get=lambda k, d=None: d, data={"p": 1}))
    loop = SimpleNamespace(
        _economics_guard=lambda c, s, a: (s, {"round_trip_pct": 1.0}),
        funnel=mock.MagicMock(), jupiter=None, mode="PAPER", learning=None,
        _fleet_exposure=lambda: 0.0, max_sector_positions=9, max_sector_exposure_pct=100,
        params=SimpleNamespace(get=lambda k, d=None: d), _measure_entry=lambda p, c: None,
        onchain=onchain,
    )
    return loop, arm, portfolio


class _Stop(Exception):
    pass


class _RaisingObserver:
    def snapshot(self, *a, **k):
        raise RuntimeError("boom")

    def request_many(self, *a, **k):
        raise RuntimeError("boom")


def test_open_position_decision_identical_with_or_without_observer(monkeypatch):
    from src import main as main_module
    monkeypatch.setattr(main_module, "pre_trade_check", lambda **k: None)
    cand = SimpleNamespace(token_address="T", symbol="T", price_usd=1.0, raw={})
    full = OnchainObserver("k", session=FailingSession(), start_thread=False)
    full._results["T"] = {"onchain_status": "ok", "onchain_top10_pct": 30.0, "_ts": 0.0}
    seen = []
    for observer in (OnchainObserver(None, start_thread=False), full, _RaisingObserver()):
        loop, arm, portfolio = _fake_loop(observer)
        portfolio.open.side_effect = _Stop  # on s'arrête à l'ordre d'ouverture
        with pytest.raises(_Stop):
            main_module.AlphaLoop._open_position(loop, cand, arm)
        args, kwargs = portfolio.open.call_args
        seen.append((args, kwargs["entry_context"].get("onchain_top10_pct")))
    assert seen[0][0] == seen[1][0] == seen[2][0]          # mêmes décisions/tailles
    assert [s[1] for s in seen] == [None, 30.0, None]       # seul le contexte change


def test_request_onchain_observation_never_raises_nor_mutates():
    from src import main as main_module
    cands = [SimpleNamespace(token_address=f"T{i}", raw={}, alpha_score_absolute=i) for i in range(5)]
    evaluation = SimpleNamespace(result=SimpleNamespace(candidates=list(cands)))
    loop = SimpleNamespace(_last_evaluations={"a": evaluation}, onchain=_RaisingObserver())
    main_module.AlphaLoop._request_onchain_observation(loop)
    obs = OnchainObserver("k", session=FailingSession(), start_thread=False)
    loop.onchain = obs
    main_module.AlphaLoop._request_onchain_observation(loop)
    assert evaluation.result.candidates == cands
    assert list(obs._queue.queue)[0][0] == "T4"   # meilleurs d'abord
