"""Détecteur insider front-run (observation seule, 2026-10-18)."""
from src.agents.onchain_observer import ALLOWED_METHODS, OnchainObserver
from src.core import insider as ins
from src.core.insider import (
    INSIDER_FIELDS, detect_spike, first_activity, funder_of, insider_metrics, swaps,
)

MINT = "MintAAA"


def tx(payer, slot, t, deltas, sig=None):
    pre = [{"mint": MINT, "owner": o, "uiTokenAmount": {"uiAmount": a}} for o, (a, _b) in deltas.items()]
    post = [{"mint": MINT, "owner": o, "uiTokenAmount": {"uiAmount": b}} for o, (_a, b) in deltas.items()]
    return {"slot": slot, "blockTime": t,
            "transaction": {"signatures": [sig or f"{payer}-{slot}-{t}"],
                            "message": {"accountKeys": [{"pubkey": payer}]}},
            "meta": {"preTokenBalances": pre, "postTokenBalances": post}}


def fund_tx(src, dst):
    return {"transaction": {"message": {"accountKeys": [{"pubkey": src}], "instructions": [
        {"program": "system", "parsed": {"type": "transfer",
                                          "info": {"source": src, "destination": dst}}}]}},
            "meta": {}}


def base(**kw):
    args = dict(mint=MINT, supply=1000.0, holdings={}, candidates=[], creator="DEV",
                fresh={}, signatures={}, funders={}, early_txs=[], recent_txs=[], now=10_000)
    args.update(kw)
    return insider_metrics(**args)


def test_swaps_buy_and_sell_dedup():
    a = tx("A", 1, 100, {"A": (0, 10)}, sig="s1")
    s = swaps([a, a, tx("A", 2, 160, {"A": (10, 0)})], MINT)
    assert [(w, d) for _s, _t, w, d in s] == [("A", 10.0), ("A", -10.0)]


def test_funder_of_transfer_and_fallback():
    assert funder_of(fund_tx("HUB", "W1"), "W1") == "HUB"
    fallback = {"transaction": {"message": {"accountKeys": ["PAYER", "W1"], "instructions": []}}}
    assert funder_of(fallback, "W1") == "PAYER"
    assert funder_of(None, "W1") is None


def test_first_activity_oldest():
    assert first_activity([{"signature": "b", "blockTime": 20}, {"signature": "a", "blockTime": 10}]) == ("a", 10)
    assert first_activity(None) == (None, None)


def test_detect_spike():
    times = [0, 70, 130] + [200 + i for i in range(8)] + [250]
    assert detect_spike(times, 0, 300) == (180, 8)
    assert detect_spike([0, 70, 130, 190], 0, 300) is None         # pas de pic
    assert detect_spike([1, 2, 3, 4, 5, 6], 0, 60) is None          # fenêtre trop courte


def test_same_funder_cluster_supply_and_flag():
    w = ["W1", "W2", "W3", "W4"]
    m = base(candidates=w, holdings={"W1": 30, "W2": 20, "W3": 10, "W4": 99},
             funders={"W1": "F", "W2": "F", "W3": "F", "W4": "OTHER"})
    assert m["insider_cluster_size"] == 3 and m["insider_linked_wallets"] == 3
    assert m["insider_links_funder"] == 2 and m["insider_pct_supply"] == 6.0
    assert m["insider_flag"] is True


def test_creator_funding_and_creation_window_and_slot():
    sigs = {"A": [{"signature": "a", "blockTime": 1000}], "B": [{"signature": "b", "blockTime": 1200}],
            "C": [{"signature": "c", "blockTime": 9000}]}
    m = base(candidates=["A", "B", "C", "D", "E"], fresh={"A": True, "B": True, "C": True},
             signatures=sigs, funders={"C": "DEV"},
             early_txs=[tx("D", 5, 50, {"D": (0, 1)}), tx("E", 5, 50, {"E": (0, 1)})])
    assert m["insider_links_created"] == 1   # A-B (200 s), pas C
    assert m["insider_links_creator"] == 1
    assert m["insider_links_slot"] == 1      # D-E même slot
    assert m["insider_clusters_n"] == 3 and m["insider_cluster_size"] == 2
    assert m["insider_flag"] is False        # grappe < 3


def test_bought_before_spike_and_early_sell():
    now = 10_000
    start = now - 900
    recent = [tx("W1", 1, start + 200, {"W1": (0, 5)}), tx("W2", 1, start + 230, {"W2": (0, 5)}),
              tx("W3", 1, start + 240, {"W3": (0, 5)})]
    recent += [tx(f"R{i}", 2, start + 480 + i, {f"R{i}": (0, 1)}) for i in range(8)]
    recent += [tx("W1", 3, start + 600, {"W1": (5, 0)})]
    m = base(candidates=["W1", "W2", "W3"], funders={"W1": "F", "W2": "F", "W3": "F"},
             recent_txs=recent, now=now)
    assert m["insider_spike_detected"] is True and m["insider_spike_time"] == start + 480
    assert m["insider_bought_before_spike"] is True and m["insider_pre_spike_buyers"] == 3
    assert m["insider_spike_lead_sec"] == 280
    assert m["insider_buyers_before_entry_15m"] == 3
    assert m["insider_early_sellers"] == 1 and m["insider_sellers_15m"] == 1
    assert m["insider_selling"] is True and m["insider_flag"] is True


def test_no_link_no_flag_and_unknowns_stay_none():
    m = base(candidates=["A", "B"], recent_txs=None, early_txs=None, supply=None)
    assert m["insider_cluster_size"] == 0 and m["insider_flag"] is False
    assert m["insider_pct_supply"] is None and m["insider_spike_detected"] is None
    assert m["insider_buyers_before_entry_15m"] is None
    assert base()["insider_status"] == "no_wallets"
    assert set(INSIDER_FIELDS) <= set(base(candidates=["A"]))


def test_hub_funders_ignored(monkeypatch):
    monkeypatch.setattr(ins, "KNOWN_HUB_FUNDERS", frozenset({"BINANCE"}))
    m = base(candidates=["A", "B"], funders={"A": "BINANCE", "B": "BINANCE"})
    assert m["insider_cluster_size"] == 0


def test_observer_snapshot_exposes_insider_nulls_and_readonly():
    assert "getTransaction" in ALLOWED_METHODS
    assert not any(m.startswith("send") for m in ALLOWED_METHODS)
    obs = OnchainObserver(None, start_thread=False)
    snap = obs.snapshot("X", 0)
    assert snap["onchain_status"] == "disabled"
    assert all(k in snap for k in INSIDER_FIELDS) and snap["insider_flag"] is None


class _Resp:
    def __init__(self, result):
        self.status_code, self._r = 200, result

    def json(self):
        return {"result": self._r}


class _Session:
    """Faux Helius : 2 wallets frais du top 10, financés par la même source."""

    def __init__(self):
        self.methods = []

    def post(self, url, json, timeout):
        m, p = json["method"], json["params"]
        self.methods.append(m)
        if m == "getAccountInfo":
            return _Resp({"value": {"data": {"parsed": {"info": {
                "mintAuthority": None, "freezeAuthority": None, "supply": "1000", "decimals": 0}}}}})
        if m == "getTokenLargestAccounts":
            return _Resp({"value": [{"address": "TA1", "uiAmount": 40}, {"address": "TA2", "uiAmount": 30}]})
        if m == "getMultipleAccounts":
            if p[1].get("encoding") == "jsonParsed":
                return _Resp({"value": [{"data": {"parsed": {"info": {"owner": o}}}} for o in ("W1", "W2")]})
            return _Resp({"value": [{"owner": "11111111111111111111111111111111"}] * len(p[0])})
        if m == "getSignaturesForAddress":
            return _Resp([{"signature": f"first-{p[0]}", "blockTime": 9_900}])
        if m == "getTransaction":
            return _Resp(fund_tx("FUNDER", p[0].removeprefix("first-")))
        return _Resp({"data": []})


def test_observer_compute_end_to_end_and_status_untouched():
    sess = _Session()
    obs = OnchainObserver("k", session=sess, start_thread=False, rdap_enabled=False,
                          clock=lambda: 10_000.0)
    out = obs.compute(MINT)
    assert sess.methods.count("getTransaction") == 2
    assert out["insider_credits_used"] == 2
    assert out["insider_cluster_size"] == 2 and out["insider_pct_supply"] == 7.0
    assert out["insider_links_funder"] == 1
    assert out["onchain_credits_used"] >= out["insider_credits_used"]
    # le statut on-chain ne compte pas les champs insider
    assert out["onchain_status"] in ("ok", "partial")


def test_insider_budget_never_changes_onchain_status():
    sess = _Session()
    obs = OnchainObserver("k", session=sess, start_thread=False, rdap_enabled=False,
                          clock=lambda: 10_000.0, daily_credits=10_000)
    ref = obs.compute(MINT)["onchain_status"]
    used = ref and obs.budget.used
    # budget juste suffisant pour le cœur, pas pour les getTransaction
    core = used - 2
    obs2 = OnchainObserver("k", session=_Session(), start_thread=False, rdap_enabled=False,
                           clock=lambda: 10_000.0, daily_credits=core)
    out = obs2.compute(MINT)
    assert out["onchain_status"] == ref
    assert out["insider_status"] == "budget_partial" and out["insider_credits_used"] == 0
