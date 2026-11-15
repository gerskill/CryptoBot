from src.core.onchain_antirug import OnchainAntiRug, TopSignalGate, SYSTEM_PROGRAM

MINT = "Mint111"


def make_post(top_owner_is_wallet=True, top_amount=600, shared=True, fail=False):
    def post(method, params):
        if fail:
            raise RuntimeError("down")
        if method == "getTokenSupply":
            return {"value": {"uiAmount": 1000}}
        if method == "getTokenLargestAccounts":
            return {"value": [{"address": "acc1", "uiAmount": top_amount}, {"address": "acc2", "uiAmount": 50}]}
        if method == "getMultipleAccounts":
            if params[1]["encoding"] == "jsonParsed":
                return {"value": [{"data": {"parsed": {"info": {"owner": "own1"}}}},
                                  {"data": {"parsed": {"info": {"owner": "own2"}}}}]}
            return {"value": [{"owner": SYSTEM_PROGRAM if top_owner_is_wallet else "AmmProg"},
                              {"owner": SYSTEM_PROGRAM}]}
        if method == "getSignaturesForAddress":
            addr = params[0]
            if addr == MINT:
                return [{"signature": f"s{i}"} for i in range(8)]
            return [{"signature": "first_" + addr}]
        if method == "getTransaction":
            sig = params[0]
            if sig.startswith("first_"):
                buyer = sig[6:]
                src = "BOSS" if shared else "f_" + buyer
                return {"transaction": {"message": {"accountKeys": [{"pubkey": src}],
                        "instructions": [{"parsed": {"info": {"source": src, "destination": buyer}}}]}}}
            return {"transaction": {"message": {"accountKeys": [{"pubkey": "buyer_" + sig}]}}}
    return post


def ar(**kw):
    return OnchainAntiRug(post=make_post(**kw), sync=True, sleep=lambda s: None)


def test_wallet_holding_most_supply_rejected():
    r, _ = ar(shared=False).reason(MINT, 50, 3)
    assert r and "wallet hors pool" in r


def test_pool_holding_most_supply_passes():
    r, _ = ar(top_owner_is_wallet=False, shared=False).reason(MINT, 50, 3)
    assert r is None


def test_shared_funder_rejected():
    r, _ = ar(top_amount=10, shared=True).reason(MINT, 50, 3)
    assert r and "même wallet" in r


def test_rpc_down_passes_with_note():
    r, note = ar(fail=True).reason(MINT, 50, 3)
    assert r is None and "accepté" in note


def test_cache_avoids_second_rpc_round():
    calls = []
    base = make_post(shared=False, top_amount=10)
    a = OnchainAntiRug(post=lambda m, p: (calls.append(m), base(m, p))[1], sync=True, sleep=lambda s: None)
    a.reason(MINT); n = len(calls); a.reason(MINT)
    assert len(calls) == n


def test_429_retried():
    import types
    a = OnchainAntiRug(sleep=lambda s: None)
    seq = [429, 200]
    class R:
        def __init__(self, c): self.status_code = c
        def raise_for_status(self): pass
        def json(self): return {"result": 7}
    import requests
    orig = requests.post
    requests.post = lambda *x, **k: R(seq.pop(0))
    try:
        assert a._rpc("getHealth", []) == 7
    finally:
        requests.post = orig


def test_async_pending_then_ready():
    a = OnchainAntiRug(post=make_post(shared=False, top_amount=10), sleep=lambda s: None)
    r, _ = a.reason(MINT)
    assert "en cours" in r
    a._pool.shutdown(wait=True)
    r, _ = a.reason(MINT)
    assert r is None


def test_top_signal_gate():
    g = TopSignalGate(min_samples=10)
    assert g.cutoff("x", 20) is None
    for i in range(100):
        g.observe("x", f"a{i}", float(i))
    assert g.cutoff("x", 20) == 80.0
    g.observe("x", "a99", 99.0)  # doublon ignoré
    assert len(g._seen["x"]) == 100


def test_off_by_default_on_all_arms_except_sniper_ar():
    import json
    d = json.load(open("config/strategies.json"))
    for arm in d["arms"]:
        o = arm.get("overrides", {})
        assert "entry_rules.top_signal_pct" not in o
        if arm["name"] != "sniper_ar":
            assert not o.get("filters.onchain_antirug")
        else:
            assert o["filters.onchain_antirug"] is True


def test_pipeline_gate_rejects_and_off_is_noop():
    from types import SimpleNamespace
    from src.pipeline import ScanPipeline as Pipeline
    p = Pipeline.__new__(Pipeline)
    p.onchain_antirug = OnchainAntiRug(post=make_post(shared=False), sync=True, sleep=lambda s: None)
    c = SimpleNamespace(token_address=MINT, symbol="X", alpha_score_absolute=90.0,
                        with_fields=lambda **k: ("rej", k))
    class P(dict):
        def get(self, k, d=None): return dict.get(self, k, d)
    rej = []
    assert p._apply_opt_in_gates([c], P(), {}, rej, "", False) == [c]
    out = p._apply_opt_in_gates([c], P({"scan.alpha_score_entry_threshold": 65}),
                                {"onchain_antirug": True}, rej, "", False)
    assert out == [] and rej
