"""RPC Solana partagé : Chainstack → Helius → public, avec logs redactés.

Ordre :
1. Chainstack si configuré (URL path-token, ou base + basic auth USER/PASS)
2. Helius si HELIUS_API_KEY présent
3. RPC public Solana

Sur 401 / 403 / timeout / connexion, on passe au suivant.
Ne jamais logger l'URL complète ni les jetons.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse

import requests

PUBLIC_RPC_URL = "https://api.mainnet-beta.solana.com"
CHAINSTACK_BASE = "https://solana-mainnet.core.chainstack.com"
HELIUS_BASE = "https://mainnet.helius-rpc.com"

_AUTH_FAIL = {401, 403}


def redact_url(url: str) -> str:
    """Garde le host, masque chemin / query (jetons)."""
    try:
        p = urlparse(url)
        host = p.hostname or "?"
        scheme = p.scheme or "https"
        return f"{scheme}://{host}/<REDACTED>"
    except Exception:
        return "<REDACTED>"


def redact_exc(exc: BaseException) -> str:
    """Message d'erreur sans URL secrète."""
    msg = str(exc)
    msg = re.sub(r"https?://[^\s'\"]+", lambda m: redact_url(m.group(0)), msg)
    msg = re.sub(r"(api-key=)[^&\s'\"]+", r"\1<REDACTED>", msg, flags=re.I)
    msg = re.sub(r"(Bearer\s+)\S+", r"\1<REDACTED>", msg, flags=re.I)
    return msg[:200]


def _helius_url() -> Optional[str]:
    try:
        from src import settings
        key = settings.HELIUS_API_KEY
        if key:
            return f"{HELIUS_BASE}/?api-key={key}"
    except Exception:
        pass
    return None


def _chainstack_endpoint() -> Optional[tuple[str, Optional[tuple[str, str]]]]:
    """Retourne (url, basic_auth_or_None) ou None si non configuré.

    Priorité :
    - USERNAME+PASSWORD → base URL + HTTP basic auth (sans path token)
    - sinon CHAINSTACK_RPC_URL (path-token) telle quelle
    - sinon base + AUTH_TOKEN en path
    """
    try:
        from src import settings
    except Exception:
        return None
    user = getattr(settings, "CHAINSTACK_USERNAME", None)
    password = getattr(settings, "CHAINSTACK_PASSWORD", None)
    if user and password:
        return (CHAINSTACK_BASE, (user, password))
    url = getattr(settings, "CHAINSTACK_RPC_URL", None)
    if url:
        return (url, None)
    tok = getattr(settings, "CHAINSTACK_AUTH_TOKEN", None)
    if tok:
        return (f"{CHAINSTACK_BASE}/{tok}", None)
    return None


def ordered_endpoints(
    explicit_url: Optional[str] = None,
    *,
    include_helius: bool = True,
    include_public: bool = True,
) -> list[tuple[str, Optional[tuple[str, str]], str]]:
    """Liste (url, basic_auth, label) dans l'ordre de tentative."""
    out: list[tuple[str, Optional[tuple[str, str]], str]] = []
    seen: set[str] = set()

    def add(url: str, auth: Optional[tuple[str, str]], label: str) -> None:
        key = f"{label}:{urlparse(url).hostname}:{bool(auth)}"
        if key in seen:
            return
        seen.add(key)
        out.append((url, auth, label))

    if explicit_url:
        add(explicit_url, None, "explicit")
    else:
        cs = _chainstack_endpoint()
        if cs:
            add(cs[0], cs[1], "chainstack")
    if include_helius:
        hu = _helius_url()
        if hu:
            add(hu, None, "helius")
    if include_public:
        add(PUBLIC_RPC_URL, None, "public")
    return out


def default_rpc_url() -> str:
    """URL primaire (sans auth tuple) — compat anciens appelants."""
    eps = ordered_endpoints()
    return eps[0][0] if eps else PUBLIC_RPC_URL


class RpcClient:
    """Client JSON-RPC avec bascule automatique Chainstack → Helius → public."""

    def __init__(
        self,
        rpc_url: Optional[str] = None,
        *,
        timeout: float = 8.0,
        post: Any = None,
    ) -> None:
        self._explicit = rpc_url
        self.timeout = timeout
        self._post = post  # injectable pour tests
        self._preferred_idx = 0
        self.last_label = "none"
        # Compat : URL affichable / primaire au démarrage
        self.rpc_url = (ordered_endpoints(rpc_url)[0][0]
                        if ordered_endpoints(rpc_url) else PUBLIC_RPC_URL)

    def endpoints(self) -> list[tuple[str, Optional[tuple[str, str]], str]]:
        return ordered_endpoints(self._explicit)

    def call(self, method: str, params: list) -> Any:
        if self._post is not None:
            return self._post(method, params)
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        eps = self.endpoints()
        if not eps:
            raise RuntimeError("aucun endpoint RPC configuré")
        # Tourner en partant du préféré (celui qui a marché la dernière fois)
        order = list(range(len(eps)))
        if 0 <= self._preferred_idx < len(eps):
            order = order[self._preferred_idx:] + order[:self._preferred_idx]
        errors: list[str] = []
        for idx in order:
            url, auth, label = eps[idx]
            try:
                r = requests.post(
                    url, json=payload, auth=auth, timeout=self.timeout
                )
                if r.status_code in _AUTH_FAIL:
                    errors.append(f"{label}: HTTP {r.status_code}")
                    continue
                if r.status_code == 429:
                    errors.append(f"{label}: HTTP 429")
                    continue
                if r.status_code >= 400:
                    errors.append(f"{label}: HTTP {r.status_code}")
                    continue
                body = r.json()
                if isinstance(body, dict) and "error" in body:
                    err = body["error"]
                    code = err.get("code") if isinstance(err, dict) else None
                    msg = str(err.get("message") if isinstance(err, dict) else err)[:80]
                    # Erreurs « méthode / archive non dispo » → essayer le suivant
                    low = msg.lower()
                    if code in (-32002, -32601, -32600) or any(
                        k in low for k in ("archive", "dedicated", "not available", "not supported")
                    ):
                        errors.append(f"{label}: rpc {code}:{msg}")
                        continue
                    raise RuntimeError(f"{label}: {msg}")
                self._preferred_idx = idx
                self.last_label = label
                self.rpc_url = url  # ne pas logger
                return body.get("result") if isinstance(body, dict) else body
            except (requests.Timeout, requests.ConnectionError) as exc:
                errors.append(f"{label}: {type(exc).__name__}")
                continue
            except RuntimeError:
                raise
            except Exception as exc:
                errors.append(f"{label}: {redact_exc(exc)}")
                continue
        raise RuntimeError(
            "RPC échoué sur tous les endpoints (" + "; ".join(errors[:4]) + ")"
        )
