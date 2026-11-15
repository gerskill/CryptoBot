"""Anti-rug on-chain renforcé + filtre « meilleurs signaux » (2026-11-02, accord Killian).

Deux réglages OPT-IN par bras, désactivés par défaut :

* `filters.onchain_antirug` (bool) : refuse un token si
  - un wallet (hors pool / programme) détient > `filters.onchain_max_wallet_pct` % de l'offre
    (getTokenLargestAccounts + getMultipleAccounts : un propriétaire dont le compte
    appartient au System Program est un wallet ; sinon c'est une pool / un programme) ;
  - au moins `filters.onchain_max_shared_funder` des premiers acheteurs ont été financés
    par le MÊME wallet (getSignaturesForAddress + getTransaction).
  RPC Solana : Chainstack → Helius (secours) → public ; bascule auto sur 401/403.
  Vérification en tâche de fond (1 thread) pour ne pas
  ralentir le cycle ; résultat en cache 30 min ; retries sur 429. Tant que la
  vérification tourne, le token attend (max `pending_max_sec`), puis PASSE avec raison
  loguée. Données indisponibles = le token PASSE (raison loguée).

* `entry_rules.top_signal_pct` (ex. 20) : ne garde que les candidats dont le score
  alpha est dans les X % meilleurs des scores récemment vus par CE bras
  (fenêtre glissante). Moins de `min_samples` scores vus = pas de filtre.
"""
from __future__ import annotations

import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from src.core.rpc_endpoint import PUBLIC_RPC_URL, RpcClient, default_rpc_url, redact_exc

SYSTEM_PROGRAM = "11111111111111111111111111111111"


class OnchainAntiRug:
    def __init__(self, rpc_url: Optional[str] = None, post: Optional[Callable] = None,
                 ttl_sec: float = 1800.0, pending_max_sec: float = 200.0,
                 first_buyers: int = 8, sync: bool = False, sleep: Callable = time.sleep):
        self._rpc_client = RpcClient(rpc_url=rpc_url, post=post, timeout=8.0)
        self.rpc_url = self._rpc_client.rpc_url
        self._post, self.ttl, self.pending_max = post, ttl_sec, pending_max_sec
        self.first_buyers, self.sync, self._sleep = first_buyers, sync, sleep
        self._cache: dict[str, tuple[float, dict]] = {}
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()
        self._pool = None if sync else ThreadPoolExecutor(max_workers=1, thread_name_prefix="antirug")

    # -- RPC ---------------------------------------------------------------
    def _rpc(self, method: str, params: list) -> Any:
        for attempt in range(4):
            try:
                result = self._rpc_client.call(method, params)
                self.rpc_url = self._rpc_client.rpc_url
                self._sleep(0.25)
                return result
            except RuntimeError as exc:
                msg = redact_exc(exc)
                if "429" in msg and attempt < 3:
                    self._sleep(1.5 * (2 ** attempt))
                    continue
                raise RuntimeError(msg) from None
        raise RuntimeError("RPC 429 persistant")

    # -- analyses --------------------------------------------------------------
    def holder_info(self, mint: str) -> dict:
        supply = self._rpc("getTokenSupply", [mint])
        total = float((supply or {}).get("value", {}).get("uiAmount") or 0)
        if not total:
            return {"ok": False, "why": "offre ou holders indisponibles"}
        largest = None
        try:
            largest = (self._rpc("getTokenLargestAccounts", [mint]) or {}).get("value") or []
        except RuntimeError:
            largest = None
        if not largest:
            das = self._holder_info_das(mint, total)
            if das is not None:
                return das
            return {"ok": False, "why": "offre ou holders indisponibles"}
        top = largest[:5]
        accs = (self._rpc("getMultipleAccounts", [[a["address"] for a in top],
                                                 {"encoding": "jsonParsed"}]) or {}).get("value") or []
        owners = []
        for a in accs:
            try:
                owners.append(a["data"]["parsed"]["info"]["owner"])
            except Exception:
                owners.append(None)
        known = [o for o in owners if o]
        kinds = (self._rpc("getMultipleAccounts", [known, {"encoding": "base64"}]) or {}).get("value") or [] if known else []
        is_wallet = {o: (k is None or k.get("owner") == SYSTEM_PROGRAM) for o, k in zip(known, kinds)}
        best = 0.0
        for a, o in zip(top, owners):
            if o and is_wallet.get(o, False):
                best = max(best, float(a.get("uiAmount") or 0) / total * 100)
        return {"ok": True, "max_wallet_pct": round(best, 2)}

    def _holder_info_das(self, mint: str, total: float) -> Optional[dict]:
        """Secours Helius DAS (getTokenAccounts) si getTokenLargestAccounts est refusé."""
        try:
            from src import settings
            key = settings.HELIUS_API_KEY
            if not key:
                return None
            import requests
            url = f"https://mainnet.helius-rpc.com/?api-key={key}"
            by_owner: dict[str, float] = {}
            cursor = None
            for _ in range(5):  # max ~100 comptes, assez pour memecoins
                params: dict = {"mint": mint, "limit": 20}
                if cursor:
                    params["cursor"] = cursor
                r = requests.post(url, json={"jsonrpc": "2.0", "id": 1,
                                            "method": "getTokenAccounts", "params": params},
                                  timeout=12)
                if r.status_code >= 400:
                    return None
                body = r.json().get("result") or {}
                for row in body.get("token_accounts") or []:
                    owner = row.get("owner")
                    if not owner:
                        continue
                    by_owner[owner] = by_owner.get(owner, 0.0) + float(row.get("amount") or 0)
                cursor = body.get("cursor")
                if not cursor:
                    break
            if not by_owner or total <= 0:
                return None
            # amount DAS est en unités brutes ; getTokenSupply.uiAmount est UI.
            # On normalise via getTokenSupply decimals si possible.
            try:
                supply = self._rpc("getTokenSupply", [mint]) or {}
                dec = int((supply.get("value") or {}).get("decimals") or 0)
            except Exception:
                dec = 0
            scale = 10 ** dec if dec else 1
            best_raw = max(by_owner.values())
            best = best_raw / scale / total * 100
            return {"ok": True, "max_wallet_pct": round(best, 2)}
        except Exception:
            return None

    def _funder_of(self, wallet: str) -> Optional[str]:
        sigs = self._rpc("getSignaturesForAddress", [wallet, {"limit": 1000}]) or []
        if not sigs:
            return None
        tx = self._rpc("getTransaction", [sigs[-1]["signature"], {"encoding": "jsonParsed",
                                                                  "maxSupportedTransactionVersion": 0}])
        if not tx:
            return None
        for ins in tx.get("transaction", {}).get("message", {}).get("instructions", []):
            info = (ins.get("parsed") or {}).get("info") if isinstance(ins.get("parsed"), dict) else None
            if info and info.get("destination") == wallet and info.get("source"):
                return info["source"]
        keys = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
        payer = keys[0].get("pubkey") if keys and isinstance(keys[0], dict) else (keys[0] if keys else None)
        return payer if payer and payer != wallet else None

    def funder_info(self, mint: str) -> dict:
        sigs = self._rpc("getSignaturesForAddress", [mint, {"limit": 1000}]) or []
        if not sigs:
            return {"ok": False, "why": "historique du token indisponible"}
        if len(sigs) >= 1000:
            return {"ok": False, "why": "historique trop long, premiers acheteurs hors de portée"}
        earliest = list(reversed(sigs))[: self.first_buyers * 2]
        buyers: list[str] = []
        for s in earliest:
            if len(buyers) >= self.first_buyers:
                break
            tx = self._rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed",
                                                               "maxSupportedTransactionVersion": 0}])
            keys = (tx or {}).get("transaction", {}).get("message", {}).get("accountKeys", [])
            if keys:
                k = keys[0].get("pubkey") if isinstance(keys[0], dict) else keys[0]
                if k and k not in buyers:
                    buyers.append(k)
        funders = Counter(f for f in (self._funder_of(b) for b in buyers) if f)
        top, n = (funders.most_common(1)[0] if funders else (None, 0))
        return {"ok": True, "buyers": len(buyers), "shared_funder_count": n,
                "shared_funder": top[:6] + "…" if top else None,
                "complete": len(sigs) < 1000}

    def _analyse(self, mint: str) -> dict:
        out: dict = {}
        for name, fn in (("holders", self.holder_info), ("funders", self.funder_info)):
            try:
                out[name] = fn(mint)
            except Exception as exc:
                out[name] = {"ok": False, "why": f"erreur RPC : {str(exc)[:80]}"}
        with self._lock:
            self._cache[mint] = (time.time(), out)
            self._pending.pop(mint, None)
        return out

    # -- verdict -----------------------------------------------------------
    def reason(self, mint: str, max_wallet_pct: float = 50.0, max_shared_funder: int = 3
               ) -> tuple[Optional[str], Optional[str]]:
        """(raison de refus | None, note de log | None)."""
        now = time.time()
        with self._lock:
            hit = self._cache.get(mint)
            ttl = self.ttl if hit and all(v.get("ok") for v in hit[1].values()) else min(self.ttl, 300)
            if hit and now - hit[0] < ttl:
                data = hit[1]
            else:
                data = None
                started = self._pending.get(mint)
        if data is None:
            if started is None:
                with self._lock:
                    self._pending[mint] = now
                if self.sync:
                    data = self._analyse(mint)
                else:
                    self._pool.submit(self._analyse, mint)
                    return "anti-rug on-chain : vérification en cours", None
            elif now - started < self.pending_max:
                return "anti-rug on-chain : vérification en cours", None
            else:
                return None, "anti-rug on-chain : vérification trop lente, token accepté"
        notes = []
        h, f = data.get("holders", {}), data.get("funders", {})
        if h.get("ok"):
            if h["max_wallet_pct"] > max_wallet_pct:
                return f"un wallet hors pool détient {h['max_wallet_pct']:.0f}% > {max_wallet_pct}%", None
        else:
            notes.append(f"holders non vérifiés ({h.get('why')})")
        if f.get("ok"):
            if f["shared_funder_count"] >= max_shared_funder:
                return (f"{f['shared_funder_count']} premiers acheteurs financés par le même wallet "
                        f"{f['shared_funder']}"), None
        else:
            notes.append(f"financeurs non vérifiés ({f.get('why')})")
        return None, ("anti-rug on-chain : " + "; ".join(notes) + " — token accepté") if notes else None


class TopSignalGate:
    """Garde les X % meilleurs scores vus récemment par un bras."""

    def __init__(self, window: int = 300, min_samples: int = 50):
        self.window, self.min_samples = window, min_samples
        self._seen: dict[str, deque] = {}
        self._last: dict[str, dict[str, float]] = {}

    def observe(self, arm: str, address: str, score: float) -> None:
        last = self._last.setdefault(arm, {})
        if last.get(address) == score:
            return  # même candidat ré-évalué : ne pas le compter deux fois
        last[address] = score
        if len(last) > self.window * 4:
            last.clear()
        self._seen.setdefault(arm, deque(maxlen=self.window)).append(score)

    def cutoff(self, arm: str, top_pct: float) -> Optional[float]:
        seen = self._seen.get(arm)
        if not seen or len(seen) < self.min_samples:
            return None
        s = sorted(seen)
        idx = min(len(s) - 1, int(len(s) * (1 - top_pct / 100.0)))
        return s[idx]
