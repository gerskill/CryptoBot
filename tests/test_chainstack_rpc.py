"""Chainstack / Helius RPC : ordre de bascule + redaction (dummy, jamais de vrai jeton)."""
from src import settings
from src.core import copy_smart as cs
from src.core import onchain_antirug as ar
from src.core import rpc_endpoint as rpc

PUBLIC = "https://api.mainnet-beta.solana.com"
DUMMY_CS = "https://example.test/chainstack-dummy/abc"
DUMMY_HELIUS_KEY = "dummy-helius-key-not-real"


def test_default_rpc_falls_back_to_public_when_unset(monkeypatch):
    monkeypatch.setattr(settings, "CHAINSTACK_RPC_URL", None)
    monkeypatch.setattr(settings, "CHAINSTACK_AUTH_TOKEN", None)
    monkeypatch.setattr(settings, "CHAINSTACK_USERNAME", None)
    monkeypatch.setattr(settings, "CHAINSTACK_PASSWORD", None)
    monkeypatch.setattr(settings, "HELIUS_API_KEY", None)
    assert cs.default_rpc_url() == PUBLIC
    assert ar.default_rpc_url() == PUBLIC
    w = cs.ManualWalletWatcher(post=lambda m, p: None)
    assert "mainnet-beta.solana.com" in w.rpc_url
    a = ar.OnchainAntiRug(post=lambda m, p: None, sync=True, sleep=lambda s: None)
    assert "mainnet-beta.solana.com" in a.rpc_url
    assert settings.key_status()["chainstack"] is False


def test_default_rpc_uses_chainstack_env_when_set(monkeypatch):
    monkeypatch.setattr(settings, "CHAINSTACK_RPC_URL", DUMMY_CS)
    monkeypatch.setattr(settings, "CHAINSTACK_AUTH_TOKEN", "dummy-token-not-real")
    monkeypatch.setattr(settings, "CHAINSTACK_USERNAME", None)
    monkeypatch.setattr(settings, "CHAINSTACK_PASSWORD", None)
    monkeypatch.setattr(settings, "HELIUS_API_KEY", None)
    assert cs.default_rpc_url() == DUMMY_CS
    assert ar.default_rpc_url() == DUMMY_CS
    w = cs.ManualWalletWatcher(post=lambda m, p: {"ok": True})
    assert w.rpc_url == DUMMY_CS
    a = ar.OnchainAntiRug(post=lambda m, p: {"ok": True}, sync=True, sleep=lambda s: None)
    assert a.rpc_url == DUMMY_CS
    assert settings.key_status()["chainstack"] is True


def test_basic_auth_preferred_over_path_token(monkeypatch):
    monkeypatch.setattr(settings, "CHAINSTACK_RPC_URL", DUMMY_CS)
    monkeypatch.setattr(settings, "CHAINSTACK_AUTH_TOKEN", "dummy-token")
    monkeypatch.setattr(settings, "CHAINSTACK_USERNAME", "user-dummy")
    monkeypatch.setattr(settings, "CHAINSTACK_PASSWORD", "pass-dummy")
    monkeypatch.setattr(settings, "HELIUS_API_KEY", None)
    eps = rpc.ordered_endpoints()
    assert eps[0][2] == "chainstack"
    assert eps[0][0] == rpc.CHAINSTACK_BASE
    assert eps[0][1] == ("user-dummy", "pass-dummy")
    assert settings.key_status()["chainstack"] is True


def test_ordered_endpoints_helius_after_chainstack(monkeypatch):
    monkeypatch.setattr(settings, "CHAINSTACK_RPC_URL", DUMMY_CS)
    monkeypatch.setattr(settings, "CHAINSTACK_USERNAME", None)
    monkeypatch.setattr(settings, "CHAINSTACK_PASSWORD", None)
    monkeypatch.setattr(settings, "HELIUS_API_KEY", DUMMY_HELIUS_KEY)
    labels = [e[2] for e in rpc.ordered_endpoints()]
    assert labels == ["chainstack", "helius", "public"]
    assert "api-key=" in rpc.ordered_endpoints()[1][0]
    assert DUMMY_HELIUS_KEY in rpc.ordered_endpoints()[1][0]


def test_explicit_rpc_url_overrides_env(monkeypatch):
    monkeypatch.setattr(settings, "CHAINSTACK_RPC_URL", DUMMY_CS)
    other = "https://example.test/other"
    w = cs.ManualWalletWatcher(rpc_url=other, post=lambda m, p: None)
    assert w.rpc_url == other
    a = ar.OnchainAntiRug(rpc_url=other, post=lambda m, p: None, sync=True, sleep=lambda s: None)
    assert a.rpc_url == other


def test_rpc_client_falls_back_on_403(monkeypatch):
    monkeypatch.setattr(settings, "CHAINSTACK_RPC_URL", "https://cs.example/tok")
    monkeypatch.setattr(settings, "CHAINSTACK_USERNAME", None)
    monkeypatch.setattr(settings, "CHAINSTACK_PASSWORD", None)
    monkeypatch.setattr(settings, "HELIUS_API_KEY", DUMMY_HELIUS_KEY)

    class FakeResp:
        def __init__(self, status, body):
            self.status_code = status
            self._body = body
        def json(self):
            return self._body

    calls = []

    def fake_post(url, json=None, auth=None, timeout=None):
        calls.append(url)
        if "cs.example" in url:
            return FakeResp(403, {"error": {"code": -32002, "message": "Archive not available"}})
        if "helius" in url:
            return FakeResp(200, {"result": {"ok": True}})
        return FakeResp(200, {"result": {"public": True}})

    monkeypatch.setattr(rpc.requests, "post", fake_post)
    client = rpc.RpcClient()
    out = client.call("getSignaturesForAddress", ["x", {"limit": 1}])
    assert out == {"ok": True}
    assert client.last_label == "helius"
    assert any("cs.example" in u for u in calls)
    assert any("helius" in u for u in calls)


def test_redact_url_and_exc():
    u = "https://solana-mainnet.core.chainstack.com/secrettoken123"
    r = rpc.redact_url(u)
    assert "secrettoken123" not in r
    assert "chainstack.com" in r
    assert "<REDACTED>" in r
    exc = Exception("403 for url: " + u + " api-key=abc123")
    msg = rpc.redact_exc(exc)
    assert "secrettoken123" not in msg
    assert "abc123" not in msg
