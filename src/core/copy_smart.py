"""Bras de TEST `copy_smart` (2026-10-29, demande Killian, PAPER uniquement).

IDÉE : copier des « smart wallets » — mais une liste construite sur NOTRE
historique, pas empruntée. Un wallet entre dans la liste s'il a été vu EN
AVANCE (>= 1 min) sur au moins `copy_smart_min_tokens_judged` tokens déjà
jugés (journal + shadow), et si la part de ces tokens qui ont fait >= +100 %
dépasse `copy_smart_min_lift` x le taux de base de tous les tokens jugés.
Calcul = `wallet_scoreboard.score_wallets` (zéro appel réseau).

SIGNAL D'ACHAT / VENTE : le flux GMGN `track smartmoney` déjà récupéré chaque
cycle pour le scoring (cache 120 s) — AUCUN appel supplémentaire.

Ce module ne fait aucun appel réseau et ne décide rien seul : `main.py`
l'appelle dans `_try_entries` (porte d'entrée) et au monitoring (sortie
« le wallet copié a vendu »).
"""

from __future__ import annotations

import difflib
import json
import os
import re
import time
from typing import Any, Iterable, Optional

from src.apis.gmgn import (
    DEFAULT_EXCLUDED_WALLET_TAGS,
    _is_buy,
    _timestamp,
    _token_address,
    _wallet_address,
    _wallet_tags,
)
from src.core.wallet_scoreboard import (
    MIN_LEAD_MINUTES,
    PUMP_THRESHOLD_PCT,
    _outcome_map,
    score_wallets,
)
from src.core.wallets import EXCLUDED_TAGS

DEFAULTS: dict[str, Any] = {
    "copy_smart_window_minutes": 10,      # achat du wallet copié vieux de 10 min max
    "copy_smart_min_tokens_judged": 20,
    "copy_smart_min_lift": 1.5,           # taux de pump >= 1.5 x le taux de base
    "copy_smart_max_change_1h_pct": 100.0,  # déjà +100 % dans l'heure : trop tard
    "copy_smart_exit_on_wallet_sell": True,
    "copy_smart_list_refresh_sec": 900,
    # 2026-10-29 (accord Killian) — 3 ajouts inspirés de la vidéo GROKIUS :
    # (2) taux de réussite du wallet copié : % de ses avances passées où le
    #     token a ensuite monté d'au moins `copy_smart_win_peak_pct`.
    "copy_smart_min_win_rate_pct": 55.0,
    "copy_smart_win_peak_pct": 0.01,       # « le token a monté » (> 0 %)
    "copy_smart_min_wallets_fallback": 3,  # < 3 au-dessus du seuil : les 3 meilleurs, signalé
    # (1) filtre « token copieur » (ticker/nom qui imite un token déjà vu).
    "copy_smart_copycat_filter": True,
    "copy_smart_copycat_similarity": 0.85,
    "copy_smart_copycat_min_len_fuzzy": 4,
}

# Tickers très connus (Solana + majors) : un nouveau token qui les reprend
# est presque toujours un copieur. Liste locale, zéro appel réseau.
FAMOUS_TICKERS = {
    "sol", "btc", "eth", "usdc", "usdt", "bonk", "wif", "popcat", "trump",
    "melania", "pepe", "doge", "shib", "jup", "ray", "pengu", "fartcoin",
    "mew", "bome", "pnut", "goat", "moodeng", "ai16z", "chillguy", "giga",
    "spx", "mog", "brett", "wen", "myro", "slerf", "jto", "pyth", "floki",
}


def rules_from(entry_rules: Optional[dict[str, Any]]) -> dict[str, Any]:
    entry_rules = entry_rules or {}
    return {k: entry_rules.get(k, v) for k, v in DEFAULTS.items()}


class SmartWalletList:
    """Liste des wallets à copier, recalculée au plus toutes les 15 min."""

    def __init__(self) -> None:
        self._wallets: dict[str, dict[str, Any]] = {}
        self._computed_at = 0.0
        self.base_rate_pct: Optional[float] = None
        self.fallback = False
        self.candidates: dict[str, dict[str, Any]] = {}
        self.copycats = CopycatIndex()

    def refresh(self, wallets, journal, shadow, rules: dict[str, Any], force: bool = False) -> dict:
        if not force and time.time() - self._computed_at < float(rules["copy_smart_list_refresh_sec"]):
            return self._wallets
        self._computed_at = time.time()
        if not (wallets and journal and shadow):
            return self._wallets
        outcomes = _outcome_map(journal, shadow)
        if not outcomes:
            return self._wallets
        base = 100.0 * sum(1 for v in outcomes.values() if v >= PUMP_THRESHOLD_PCT) / len(outcomes)
        self.base_rate_pct = round(base, 2)
        need = base * float(rules["copy_smart_min_lift"])
        candidates = {}
        for s in score_wallets(wallets, journal, shadow):
            if s.tokens_judged >= int(rules["copy_smart_min_tokens_judged"]) and (s.hit_rate or 0) >= need:
                candidates[s.wallet] = {"hit_rate": s.hit_rate, "tokens_judged": s.tokens_judged}
        win = win_rates(wallets, outcomes, candidates, float(rules["copy_smart_win_peak_pct"]))
        for w, info in candidates.items():
            info["win_rate"] = win.get(w)
        min_wr = float(rules["copy_smart_min_win_rate_pct"])
        selected = {w: i for w, i in candidates.items() if (i["win_rate"] or 0) >= min_wr}
        self.fallback = False
        fallback_n = int(rules["copy_smart_min_wallets_fallback"])
        if len(selected) < fallback_n and candidates:
            ranked = sorted(candidates.items(), key=lambda kv: -(kv[1]["win_rate"] or 0))
            selected = dict(ranked[:fallback_n])
            self.fallback = True
        self._wallets = selected
        self.candidates = candidates
        print(
            f"[CopySmart] liste : {len(selected)} wallets sur {len(candidates)} "
            f"(taux de base {base:.1f} %, seuil {need:.1f} %, réussite >= {min_wr:.0f} %"
            f"{' — PAS ASSEZ, repli sur les meilleurs' if self.fallback else ''})"
        )
        self.copycats.rebuild(journal, shadow)
        return selected

    @property
    def wallets(self) -> dict[str, dict[str, Any]]:
        return self._wallets


def win_rates(wallets, outcomes: dict[str, float], listed: Iterable[str], win_peak_pct: float) -> dict[str, float]:
    """% des avances passées du wallet (achat avant notre bot) où le token a monté.

    Même périmètre que `score_wallets` (avance >= 1 min, tags exclus, token jugé).
    """
    listed = set(listed)
    tally: dict[str, list[int]] = {}
    seen: set[tuple[str, str]] = set()
    for row in wallets.read_all():
        w = row.get("wallet")
        if w not in listed or set(row.get("tags") or ()) & EXCLUDED_TAGS:
            continue
        lead = row.get("lead_minutes")
        token = row.get("token_address")
        if lead is None or lead < MIN_LEAD_MINUTES or token not in outcomes or (w, token) in seen:
            continue
        seen.add((w, token))
        t = tally.setdefault(w, [0, 0])
        t[1] += 1
        if outcomes[token] >= win_peak_pct:
            t[0] += 1
    return {w: round(100.0 * a / n, 1) for w, (a, n) in tally.items() if n}


def _norm(text: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


class CopycatIndex:
    """Tickers/noms déjà vus (journal + shadow), pour repérer les copieurs."""

    def __init__(self) -> None:
        # nom normalisé -> (token_address, liquidité max vue)
        self.seen: dict[str, tuple[str, float]] = {}

    def rebuild(self, journal, shadow) -> None:
        seen: dict[str, tuple[str, float]] = {}

        def add(sym, addr, liq):
            key = _norm(sym)
            if not key or not addr:
                return
            liq = float(liq or 0.0)
            prev = seen.get(key)
            if prev is None or liq > prev[1]:
                seen[key] = (addr, liq)

        try:
            for r in shadow.read_all() if shadow else []:
                add(r.get("token"), r.get("token_address"), r.get("liquidity_at_rejection"))
            for r in journal.read_positions() if journal else []:
                add(r.get("token") or r.get("symbol"), r.get("token_address"), r.get("liquidity_at_entry"))
        except Exception as exc:  # jamais bloquant
            print(f"[CopySmart] index copieurs non reconstruit : {exc}")
            return
        self.seen = seen

    def check(self, symbol: str, name: str, token_address: str, liquidity: float,
              similarity: float = 0.85, min_len_fuzzy: int = 4) -> Optional[str]:
        """Raison du refus si le token imite un autre token, sinon None."""
        liquidity = float(liquidity or 0.0)
        keys = [k for k in {_norm(symbol), _norm(name)} if k]
        for key in keys:
            if key in FAMOUS_TICKERS:
                return f"token copieur : reprend le ticker connu {key.upper()}"
            other = self.seen.get(key)
            if other and other[0] != token_address:
                return f"token copieur : {key.upper()} déjà vu sur une autre adresse"
        for key in keys:
            if len(key) < min_len_fuzzy:
                continue
            for famous in FAMOUS_TICKERS:
                if len(famous) >= min_len_fuzzy and difflib.SequenceMatcher(None, key, famous).ratio() >= similarity:
                    return f"token copieur : {key.upper()} imite {famous.upper()}"
            for other_key, (addr, liq) in self.seen.items():
                if addr == token_address or len(other_key) < min_len_fuzzy or liq < liquidity:
                    continue
                if abs(len(other_key) - len(key)) > 2:
                    continue
                if difflib.SequenceMatcher(None, key, other_key).ratio() >= similarity:
                    return f"token copieur : {key.upper()} imite {other_key.upper()} (plus liquide)"
        return None


def _directional(trade: dict[str, Any]) -> bool:
    return not (_wallet_tags(trade) & DEFAULT_EXCLUDED_WALLET_TAGS)


def recent_copy_buys(trades: Iterable[dict], listed: dict, window_minutes: float) -> dict[str, dict]:
    """token -> {wallets, newest_ts} : achats récents des wallets de la liste."""
    cutoff = time.time() - window_minutes * 60
    out: dict[str, dict] = {}
    for trade in trades or []:
        wallet = _wallet_address(trade)
        if wallet not in listed or not _directional(trade) or _is_buy(trade) is not True:
            continue
        ts = _timestamp(trade)
        if ts is None or ts < cutoff:
            continue
        token = _token_address(trade)
        if not token:
            continue
        b = out.setdefault(token, {"wallets": set(), "newest_ts": ts})
        b["wallets"].add(wallet)
        b["newest_ts"] = max(b["newest_ts"], ts)
    return out


def copied_wallet_sold(trades: Iterable[dict], token: str, wallets: Iterable[str], since_ts: float) -> Optional[str]:
    """Adresse d'un wallet copié qui a VENDU ce token après notre entrée, sinon None."""
    wallets = set(wallets)
    for trade in trades or []:
        if _token_address(trade) != token or _wallet_address(trade) not in wallets:
            continue
        if _is_buy(trade) is False and (_timestamp(trade) or 0) >= since_ts:
            return _wallet_address(trade)
    return None


class CopySources:
    """Quels wallets ont déclenché chaque position (survit à un redémarrage)."""

    def __init__(self, path: str) -> None:
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                self.data: dict[str, dict] = json.load(f)
        except (OSError, ValueError):
            self.data = {}

    def _key(self, arm: str, token: str) -> str:
        return f"{arm}:{token}"

    def remember(self, arm: str, token: str, wallets: Iterable[str]) -> None:
        self.data[self._key(arm, token)] = {"wallets": sorted(wallets), "since_ts": time.time()}
        self._save()

    def get(self, arm: str, token: str) -> Optional[dict]:
        return self.data.get(self._key(arm, token))

    def forget(self, arm: str, token: str) -> None:
        if self.data.pop(self._key(arm, token), None) is not None:
            self._save()

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)


# ---------------------------------------------------------------------------
# Wallets MANUELS (2026-11-02, accord Killian) : toujours suivis, hors règle de
# réussite. Le flux GMGN smart money ne contient QUE les wallets choisis par
# GMGN : un wallet manuel absent de ce flux ne déclencherait jamais. On lit
# donc ses transactions sur le RPC Solana (Chainstack → Helius → public),
# au plus toutes les `poll_sec`, et on les rend au même format que GMGN.

from src.core.rpc_endpoint import PUBLIC_RPC_URL, RpcClient, default_rpc_url, redact_exc


QUOTE_MINTS = {
    "So11111111111111111111111111111111111111112",   # WSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn",   # PUMP
}


def load_manual_wallets(path: str) -> dict[str, dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for row in data.get("wallets", []) if isinstance(data, dict) else []:
        addr = row.get("address") if isinstance(row, dict) else row
        if isinstance(addr, str) and 32 <= len(addr) <= 44:
            out[addr] = {"manual": True}
    return out


def merge_manual(listed: dict, manual: dict) -> dict:
    merged = dict(listed or {})
    for w, info in (manual or {}).items():
        merged.setdefault(w, dict(info))
    return merged


def trades_from_tx(tx: Optional[dict], wallet: str, signature: str = "") -> list[dict]:
    """Achats/ventes du wallet dans une transaction RPC `jsonParsed`, format GMGN."""
    if not tx or not isinstance(tx.get("meta"), dict) or tx["meta"].get("err") is not None:
        return []
    meta = tx["meta"]
    ts = tx.get("blockTime")
    delta: dict[str, float] = {}
    for sign, key in ((-1.0, "preTokenBalances"), (1.0, "postTokenBalances")):
        for b in meta.get(key) or []:
            if b.get("owner") != wallet or b.get("mint") in QUOTE_MINTS:
                continue
            amt = (b.get("uiTokenAmount") or {}).get("uiAmount") or 0.0
            delta[b["mint"]] = delta.get(b["mint"], 0.0) + sign * float(amt)
    out = []
    for mint, d in delta.items():
        if d == 0:
            continue
        out.append({"maker": wallet, "base_address": mint, "side": "buy" if d > 0 else "sell",
                    "timestamp": ts, "tx": signature, "source": "rpc_manual"})
    return out


class ManualWalletWatcher:
    """Lit les dernières transactions des wallets manuels (RPC Chainstack→Helius→public)."""

    def __init__(self, rpc_url: str | None = None, poll_sec: float = 60.0,
                 sig_limit: int = 15, keep_sec: float = 7200.0, post=None) -> None:
        self._rpc_client = RpcClient(rpc_url=rpc_url, post=post, timeout=8.0)
        self.rpc_url = self._rpc_client.rpc_url
        self.poll_sec, self.sig_limit, self.keep_sec = poll_sec, sig_limit, keep_sec
        self._post = post
        self._last_poll = 0.0
        self._seen: set[str] = set()
        self._trades: list[dict] = []
        self.errors = 0

    def _rpc(self, method: str, params: list) -> Any:
        result = self._rpc_client.call(method, params)
        self.rpc_url = self._rpc_client.rpc_url
        return result

    def trades(self, wallets: Iterable[str], force: bool = False) -> list[dict]:
        wallets = list(wallets)
        now = time.time()
        if wallets and (force or now - self._last_poll >= self.poll_sec):
            self._last_poll = now
            for w in wallets:
                try:
                    self._poll(w)
                except Exception as exc:  # jamais bloquant
                    self.errors += 1
                    print(f"[CopySmart] wallet manuel {w[:6]}… lecture RPC échouée : {redact_exc(exc)}")
            cutoff = now - self.keep_sec
            self._trades = [t for t in self._trades if (t.get("timestamp") or 0) >= cutoff]
        return list(self._trades)

    def _poll(self, wallet: str) -> None:
        sigs = self._rpc("getSignaturesForAddress", [wallet, {"limit": self.sig_limit}]) or []
        first = not any(t["maker"] == wallet for t in self._trades) and not self._seen
        new = 0
        for s in sigs:
            sig = s.get("signature")
            if not sig or sig in self._seen:
                continue
            if s.get("err") is not None or (s.get("blockTime") or 0) < time.time() - self.keep_sec:
                self._seen.add(sig)
                continue
            if self._post is None:
                time.sleep(0.25)  # RPC public : éviter le 429
            tx = self._rpc("getTransaction", [sig, {"encoding": "jsonParsed",
                                                    "maxSupportedTransactionVersion": 0}])
            self._seen.add(sig)  # marqué lu seulement après succès (réessai si 429)
            got = trades_from_tx(tx, wallet, sig)
            self._trades.extend(got)
            new += len(got)
        if len(self._seen) > 5000:
            self._seen = set(list(self._seen)[-2000:])
        if new or first:
            print(f"[CopySmart] wallet manuel {wallet[:6]}… : {new} trade(s) lu(s)"
                  f"{' (démarrage)' if first else ''}")
