"""OBSERVATEUR ON-CHAIN + SOCIAL GRATUIT (2026-10-10) — observation SEULEMENT.

POURQUOI. La recalibration du score (docs/score_calibration_2026-10-10.md) a
montré que nos journaux ne contiennent presque rien sur la STRUCTURE d'un
token : qui l'a acheté au lancement, si le dev a vendu, si les gros porteurs
sont des wallets jetables. GMGN le donnait en partie, mais il est banni par
intermittence, et le social X est en 402.

CE QUE FAIT CE MODULE. Un fil d'arrière-plan (daemon) calcule, pour les tokens
candidats, des mesures on-chain via Helius (lecture seule) et l'âge du domaine
du site déclaré (RDAP public). La boucle principale ne fait QUE :
  - `request(candidate)` : mise en file non bloquante (put_nowait) ;
  - `snapshot(token, decision_ts)` : lecture d'un cache en mémoire.
Aucune décision d'entrée ou de sortie ne lit ces champs : ils sont recopiés
dans `entry_context`, donc dans `trades_log.jsonl`, et c'est tout.

ANTI LOOK-AHEAD. Un résultat n'est rendu que s'il a été calculé AVANT
l'horodatage de la décision (`computed_at <= decision_ts`). Sinon : champs
null et `onchain_status = "pending"`.

BUDGET. Crédits Helius comptés par appel selon la grille officielle
(https://www.helius.dev/docs/billing/credits) : RPC standard 1,
getTransactionsForAddress 10 par tranche de 100 transactions complètes (10
plat en mode signatures). Plafond journalier configurable ; au-delà, plus
aucun appel jusqu'au lendemain (statut "budget"). Le limiteur de débit est
PARTAGÉ avec le client Helius de la boucle : la somme ne dépasse jamais son
plafond. Timeouts courts ; toute panne -> null, jamais d'exception.
"""

from __future__ import annotations

import queue
import re
import threading
import time
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests

from src.core.insider import (
    INSIDER_FIELDS, MAX_FUNDER_LOOKUPS, empty_insider, first_activity, funder_of, insider_metrics,
)

ONCHAIN_VERSION = 1

# Lecture seule, liste blanche vérifiée AVANT chaque requête.
ALLOWED_METHODS = frozenset({
    "getTransactionsForAddress",
    "getSignaturesForAddress",
    "getAccountInfo",
    "getTokenLargestAccounts",
    "getMultipleAccounts",
    # 2026-10-18 : source de financement des wallets frais (détecteur insider).
    "getTransaction",
})

RPC_TIMEOUT = 6.0
RDAP_TIMEOUT = 4.0
CACHE_TTL_SECONDS = 15 * 60
RDAP_CACHE_TTL_SECONDS = 24 * 3600
DEFAULT_DAILY_CREDITS = 20_000
DEFAULT_MAX_NEW_PER_CYCLE = 3
# 2026-10-29 : part du budget journalier RÉSERVÉE aux demandes prioritaires
# (bras anti_rug). L'observation de flotte s'arrête à daily - réserve.
RESERVED_PRIORITY_CREDITS = 6_000
QUEUE_SIZE = 12
EARLY_TX_LIMIT = 100
FIRST_BUYERS_N = 20
FRESH_SIG_LIMIT = 25
FRESH_MAX_AGE_HOURS = 72
FRESH_CHECK_MAX_WALLETS = 12
BUNDLE_MIN_BUYERS_PER_SLOT = 3
# Propriétaires « non-wallets » exclus du top 10 ajusté (2026-10-11). Le top 10
# brut inclut le pool AMM / la courbe pump.fun (comptes détenus par un
# PROGRAMME), ce qui n'est pas de la concentration dumpable. Un wallet humain
# ou bot est un compte du System Program (ou inexistant s'il n'a plus de SOL).
SYSTEM_PROGRAM = "11111111111111111111111111111111"
BURN_OWNERS = frozenset({"1nc1nerator11111111111111111111111111111111"})

ONCHAIN_FIELDS = (
    "onchain_status", "onchain_version", "onchain_computed_at", "onchain_age_at_decision_sec",
    "onchain_compute_sec", "onchain_credits_used",
    "onchain_mint_authority_revoked", "onchain_freeze_authority_revoked", "onchain_supply",
    "onchain_top1_pct", "onchain_top10_pct",
    "onchain_top10_wallets_pct", "onchain_program_holders_top20", "onchain_program_holders_pct",
    "onchain_creator", "onchain_creator_pct_top20", "onchain_creator_bought_at_launch",
    "onchain_dev_out_transfers", "onchain_dev_sold",
    "onchain_launch_slot", "onchain_launch_time", "onchain_token_age_min",
    "onchain_early_tx_sampled", "onchain_first_buyers_n", "onchain_first_buyers_in_top20",
    "onchain_first_buyers_top20_pct",
    "onchain_launch_slot_buyers", "onchain_max_buyers_same_slot", "onchain_bundled_slots",
    "onchain_bundle_buy_pct",
    "onchain_fresh_checked", "onchain_fresh_top10", "onchain_fresh_first_buyers",
    "onchain_tx_count_5m", "onchain_tx_count_5_15m", "onchain_tx_count_truncated",
    "onchain_unique_buyers_5m", "onchain_unique_buyers_15m",
    "onchain_buyers_per_min_recent", "onchain_buyers_per_min_5_15m", "onchain_buyers_trend",
)
SOCIAL_BG_FIELDS = ("social_website_domain_age_days", "social_website_rdap_status")


def _mask(text: str) -> str:
    return re.sub(r"api-key=[^&\s\"']+", "api-key=<masquée>", str(text))[:160]


def _r(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def website_domain(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        host = urlparse(url if "://" in url else f"https://{url}").hostname or ""
    except ValueError:
        return None
    host = host.lower().removeprefix("www.")
    return host or None


# --------------------------------------------------------------- liens sociaux

def social_links(candidate: Any) -> dict[str, Any]:
    """Liens DÉCLARÉS (DexScreener info/profil, champs GMGN). CPU pur, 0 appel."""
    raw = getattr(candidate, "raw", None) or {}
    websites: list[str] = []
    socials: dict[str, str] = {}
    source = None

    pair = raw.get("pair") if isinstance(raw, dict) else None
    profile = raw.get("profile") if isinstance(raw, dict) else None
    if isinstance(pair, dict):
        info = pair.get("info") or {}
        for w in info.get("websites") or []:
            if isinstance(w, dict) and w.get("url"):
                websites.append(w["url"])
        for s in info.get("socials") or []:
            if isinstance(s, dict) and s.get("url"):
                socials.setdefault(str(s.get("type") or "").lower(), s["url"])
        source = "dexscreener"
    if isinstance(profile, dict):
        for link in profile.get("links") or []:
            if not isinstance(link, dict) or not link.get("url"):
                continue
            kind = str(link.get("type") or "").lower()
            if kind in ("", "website") or (link.get("label") or "").lower() == "website":
                websites.append(link["url"])
            else:
                socials.setdefault(kind, link["url"])
        source = source or "dexscreener"
    if isinstance(raw, dict) and ("twitter_username" in raw or "website" in raw or "telegram" in raw):
        if raw.get("website"):
            websites.append(str(raw["website"]))
        if raw.get("twitter_username"):
            socials.setdefault("twitter", f"https://x.com/{raw['twitter_username']}")
        if raw.get("telegram"):
            socials.setdefault("telegram", str(raw["telegram"]))
        source = source or "gmgn"

    x_url = socials.get("twitter") or socials.get("x")
    handle = None
    if x_url:
        match = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]{1,30})", x_url)
        handle = match.group(1) if match else None
    websites = list(dict.fromkeys(websites))
    boosts = (pair or {}).get("boosts") if isinstance(pair, dict) else None
    return {
        "social_links_source": source,
        "social_links_count": (len(websites) + len(socials)) if source else None,
        "social_has_website": bool(websites) if source else None,
        "social_has_x": bool(x_url) if source else None,
        "social_has_telegram": ("telegram" in socials) if source else None,
        "social_has_discord": ("discord" in socials) if source else None,
        "social_x_handle": handle,
        "social_x_is_community": bool(x_url and "/i/communities/" in x_url) if x_url else None,
        "social_website_domain": website_domain(websites[0]) if websites else None,
        "social_dex_boosts_active": (boosts or {}).get("active") if isinstance(boosts, dict) else None,
        "social_dex_profile": bool(profile) if isinstance(raw, dict) and "profile" in raw else None,
    }


SOCIAL_SCORE_SOURCE = "links_v1"


def social_links_score(links: dict[str, Any]) -> Optional[int]:
    """Score social 0-100 OBSERVATION SEULE, depuis les liens déclarés.

    Remplace l'ancien social_score (= mentions X/1h, API payante, quasi
    jamais servie -> null sur 100 % des lignes). Aucun poids dans les
    décisions d'entrée. None si aucune source de liens.
    """
    if not links or not links.get("social_links_source"):
        return None
    score = 0
    if links.get("social_has_x"):
        score += 30 if not links.get("social_x_is_community") else 20
    if links.get("social_has_website"):
        score += 30
    if links.get("social_has_telegram"):
        score += 15
    if links.get("social_has_discord"):
        score += 10
    if links.get("social_dex_profile"):
        score += 10
    boosts = links.get("social_dex_boosts_active")
    if isinstance(boosts, (int, float)) and boosts > 0:
        score += 5
    return min(100, score)


def candidate_social_score(candidate: Any) -> Optional[int]:
    try:
        return social_links_score(social_links(candidate))
    except Exception:
        return None


# ---------------------------------------------------------------- calculs purs

def _payer(tx: dict) -> Optional[str]:
    try:
        key = tx["transaction"]["message"]["accountKeys"][0]
    except (KeyError, IndexError, TypeError):
        return None
    return key.get("pubkey") if isinstance(key, dict) else key


def _owner_deltas(tx: dict, mint: str) -> dict[str, float]:
    meta = tx.get("meta") or {}
    pre: dict[str, float] = {}
    post: dict[str, float] = {}
    for bucket, rows in ((pre, meta.get("preTokenBalances")), (post, meta.get("postTokenBalances"))):
        for row in rows or []:
            if row.get("mint") != mint:
                continue
            owner = row.get("owner")
            amount = float(((row.get("uiTokenAmount") or {}).get("uiAmount")) or 0)
            bucket[owner] = bucket.get(owner, 0.0) + amount
    return {o: post.get(o, 0.0) - pre.get(o, 0.0) for o in set(pre) | set(post)}


def buys_from_transactions(txs: list[dict], mint: str) -> list[tuple[int, int, str, float]]:
    """(slot, blockTime, acheteur, quantité). Acheteur = payeur dont le solde monte."""
    out = []
    for tx in txs:
        payer = _payer(tx)
        if not payer:
            continue
        delta = _owner_deltas(tx, mint).get(payer, 0.0)
        if delta > 0:
            out.append((int(tx.get("slot") or 0), int(tx.get("blockTime") or 0), payer, delta))
    return out


def launch_metrics(early_txs: list[dict], mint: str, supply: Optional[float]) -> dict[str, Any]:
    if not early_txs:
        return {}
    first = early_txs[0]
    creator = _payer(first)
    launch_slot = int(first.get("slot") or 0)
    buys = buys_from_transactions(early_txs, mint)
    first_buyers: list[str] = []
    for _slot, _t, buyer, _q in buys:
        if buyer != creator and buyer not in first_buyers:
            first_buyers.append(buyer)
        if len(first_buyers) >= FIRST_BUYERS_N:
            break
    per_slot: dict[int, set[str]] = {}
    qty_slot: Counter = Counter()
    for slot, _t, buyer, qty in buys:
        if buyer == creator:
            continue
        per_slot.setdefault(slot, set()).add(buyer)
        qty_slot[slot] += qty
    bundled = [s for s, b in per_slot.items() if len(b) >= BUNDLE_MIN_BUYERS_PER_SLOT]
    bundle_qty = sum(qty_slot[s] for s in bundled)
    return {
        "onchain_creator": creator,
        "onchain_launch_slot": launch_slot,
        "onchain_launch_time": int(first.get("blockTime") or 0) or None,
        "onchain_creator_bought_at_launch": any(b == creator for _s, _t, b, _q in buys),
        "onchain_early_tx_sampled": len(early_txs),
        "onchain_first_buyers_n": len(first_buyers),
        "onchain_launch_slot_buyers": len(per_slot.get(launch_slot, set())),
        "onchain_max_buyers_same_slot": max((len(b) for b in per_slot.values()), default=0),
        "onchain_bundled_slots": len(bundled),
        "onchain_bundle_buy_pct": _r(100 * bundle_qty / supply) if supply else None,
        "_first_buyers": first_buyers,
    }


def activity_metrics(
    sigs_15m: Optional[list[dict]], recent_full: Optional[list[dict]],
    older_full: Optional[list[dict]], mint: str, now: float,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if sigs_15m is not None:
        n5 = sum(1 for s in sigs_15m if (s.get("blockTime") or 0) >= now - 300)
        out["onchain_tx_count_5m"] = n5
        truncated = len(sigs_15m) >= 1000
        # Tronqué à 1000 : la fenêtre 5-15 min n'est pas couverte, ne pas
        # écrire un faux zéro.
        out["onchain_tx_count_5_15m"] = None if truncated else len(sigs_15m) - n5
        out["onchain_tx_count_truncated"] = truncated

    def rate(txs: Optional[list[dict]], window_start: float, window_end: float):
        if txs is None:
            return None, None, None
        buys = buys_from_transactions(txs, mint)
        if not txs:
            return 0.0, 0, True
        times = [int(t.get("blockTime") or 0) for t in txs if t.get("blockTime")]
        oldest = min(times) if times else window_end
        full_window = len(txs) < EARLY_TX_LIMIT  # pas tronqué : couvre toute la fenêtre
        start = window_start if full_window else oldest
        minutes = max((window_end - start) / 60.0, 1 / 60)
        uniq = len({b for _s, _t, b, _q in buys})
        return uniq / minutes, uniq, full_window

    r_recent, u_recent, full_recent = rate(recent_full, now - 900, now)
    r_older, u_older, full_older = rate(older_full, now - 900, now - 300)
    out["onchain_buyers_per_min_recent"] = _r(r_recent)
    out["onchain_buyers_per_min_5_15m"] = _r(r_older)
    if r_recent is not None and r_older:
        out["onchain_buyers_trend"] = _r(r_recent / r_older)
    if recent_full is not None and full_recent:
        buys = buys_from_transactions(recent_full, mint)
        out["onchain_unique_buyers_15m"] = len({b for _s, _t, b, _q in buys})
        out["onchain_unique_buyers_5m"] = len({b for _s, t, b, _q in buys if t >= now - 300})
    return out


def is_fresh(signatures: Optional[list[dict]], now: float) -> Optional[bool]:
    """Wallet « frais » : peu d'historique ET première activité récente."""
    if signatures is None:
        return None
    if len(signatures) >= FRESH_SIG_LIMIT:
        return False
    times = [s.get("blockTime") for s in signatures if s.get("blockTime")]
    if not times:
        return None
    return (now - min(times)) <= FRESH_MAX_AGE_HOURS * 3600


# ------------------------------------------------------------------ l'agent

class _Budget:
    def __init__(self, daily: int):
        self.daily = max(0, int(daily))
        self.day = date.today().isoformat()
        self.used = 0
        self.lock = threading.Lock()

    def _roll(self) -> None:
        today = date.today().isoformat()
        if today != self.day:
            self.day, self.used = today, 0

    def can(self, credits: int) -> bool:
        with self.lock:
            self._roll()
            return self.used + credits <= self.daily

    def spend(self, credits: int) -> None:
        with self.lock:
            self._roll()
            self.used += credits


class OnchainObserver:
    def __init__(
        self,
        api_key: Optional[str],
        rate_limiter: Any = None,
        enabled: bool = True,
        daily_credits: int = DEFAULT_DAILY_CREDITS,
        max_new_per_cycle: int = DEFAULT_MAX_NEW_PER_CYCLE,
        session: Any = None,
        clock: Callable[[], float] = time.time,
        start_thread: bool = True,
        rdap_enabled: bool = True,
    ):
        self.api_key = api_key
        self.enabled = bool(enabled and api_key)
        self.rate_limiter = rate_limiter
        self.budget = _Budget(daily_credits)
        self.max_new_per_cycle = max_new_per_cycle
        self.session = session or requests.Session()
        self.clock = clock
        self.rdap_enabled = rdap_enabled
        self._queue: "queue.Queue[tuple[str, Optional[str]]]" = queue.Queue(maxsize=QUEUE_SIZE)
        self._results: dict[str, dict[str, Any]] = {}
        self._pending: set[str] = set()
        self._rdap: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self.stats = Counter()
        self._thread: Optional[threading.Thread] = None
        self._credits_this_job = 0
        self._priority: set[str] = set()
        self._job_priority = False
        # Réserve active seulement au budget de production (>= 20 000/jour).
        self.reserve = RESERVED_PRIORITY_CREDITS if self.budget.daily >= DEFAULT_DAILY_CREDITS else 0
        self._last_error: Optional[str] = None
        self._quota_pause_jusqu_a = 0.0
        self._quota_signale = False
        if self.enabled and start_thread:
            self._thread = threading.Thread(target=self._worker, name="onchain-observer", daemon=True)
            self._thread.start()

    # ---------------------------------------------------- côté boucle (O(1))

    def request_many(self, candidates: list[Any], priority: bool = False) -> int:
        """Mise en file non bloquante des tokens pas encore (ou plus) en cache."""
        if not self.enabled:
            return 0
        queued = 0
        now = self.clock()
        for candidate in candidates:
            if queued >= self.max_new_per_cycle:
                break
            token = getattr(candidate, "token_address", None)
            if not token:
                continue
            with self._lock:
                cached = self._results.get(token)
                stale_budget = priority and cached and cached.get("onchain_status") == "budget"
                if token in self._pending or (cached and now - cached["_ts"] < CACHE_TTL_SECONDS and not stale_budget):
                    continue
                self._pending.add(token)
                if priority:
                    self._priority.add(token)
            website = social_links(candidate).get("social_website_domain")
            try:
                self._queue.put_nowait((token, website))
                queued += 1
            except queue.Full:
                with self._lock:
                    self._pending.discard(token)
                self.stats["queue_full"] += 1
                break
        return queued

    def snapshot(self, token: str, decision_ts: Optional[float] = None) -> dict[str, Any]:
        """Champs connus AVANT `decision_ts`. Jamais d'appel réseau."""
        decision_ts = self.clock() if decision_ts is None else decision_ts
        empty = {name: None for name in ONCHAIN_FIELDS + SOCIAL_BG_FIELDS}
        empty.update(empty_insider())
        empty["onchain_version"] = ONCHAIN_VERSION
        if not self.enabled:
            empty["onchain_status"] = "disabled"
            return empty
        with self._lock:
            result = self._results.get(token)
            pending = token in self._pending
        if not result or result["_ts"] > decision_ts:
            empty["onchain_status"] = "pending" if (pending or result) else "missing"
            return empty
        out = dict(empty)
        out.update({k: v for k, v in result.items() if not k.startswith("_")})
        out["onchain_age_at_decision_sec"] = round(decision_ts - result["_ts"], 1)
        return out

    # ---------------------------------------------------- fil d'arrière-plan

    def _worker(self) -> None:
        while True:
            token, website = self._queue.get()
            with self._lock:
                self._job_priority = token in self._priority
                self._priority.discard(token)
            try:
                result = self.compute(token, website)
            except Exception as exc:  # jamais d'exception hors du fil
                result = {"onchain_status": "error", "_error": _mask(f"{type(exc).__name__}: {exc}")}
            result["_ts"] = self.clock()
            with self._lock:
                self._results[token] = result
                self._pending.discard(token)
                self._purge()
            self.stats[result.get("onchain_status") or "?"] += 1

    def _purge(self) -> None:
        now = self.clock()
        for token in [t for t, r in self._results.items() if now - r["_ts"] > 4 * CACHE_TTL_SECONDS]:
            del self._results[token]

    # ------------------------------------------------------------- transport

    def _rpc(self, method: str, params: Any, credits: int) -> Optional[Any]:
        if method not in ALLOWED_METHODS:
            raise ValueError(f"méthode {method} interdite (lecture seule)")
        if self.clock() < getattr(self, "_quota_pause_jusqu_a", 0.0):
            raise _BudgetExceeded()  # quota du mois épuisé : aucun appel pendant la pause
        needed = credits if self._job_priority else credits + self.reserve
        if not self.budget.can(needed):
            raise _BudgetExceeded()
        if self.rate_limiter is not None:
            if not self.rate_limiter.acquire(timeout=5.0):
                return None
        url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"
        try:
            response = self.session.post(
                url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=RPC_TIMEOUT,
            )
            if getattr(response, "status_code", 200) != 200:
                self.stats[f"http_{response.status_code}"] += 1
                if response.status_code == 429 and "max usage" in str(getattr(response, "text", "")).lower():
                    self._quota_pause_jusqu_a = self.clock() + QUOTA_MOIS_PAUSE_S
                    if not getattr(self, "_quota_signale", False):
                        self._quota_signale = True
                        print("[OnChain] quota Helius du mois épuisé (429 max usage) : "
                              f"pause de {QUOTA_MOIS_PAUSE_S // 60} min, entrées notées « budget »")
                    raise _BudgetExceeded()
                return None
            self._quota_signale = False
            body = response.json()
        except _BudgetExceeded:
            raise  # quota du mois : doit remonter jusqu'au statut « budget », pas finir en rpc_error
        except Exception as exc:  # réseau, JSON : silencieux, clé masquée
            self.stats["rpc_error"] += 1
            self._last_error = _mask(f"{type(exc).__name__}: {exc}")
            return None
        if not isinstance(body, dict) or body.get("error"):
            self.stats["rpc_error"] += 1
            return None
        self.budget.spend(credits)
        self._credits_this_job += credits
        return body.get("result")

    def _txs(self, address: str, **opts) -> Optional[list[dict]]:
        params = {
            "transactionDetails": "full", "encoding": "jsonParsed",
            "maxSupportedTransactionVersion": 1, "limit": EARLY_TX_LIMIT,
        }
        params.update(opts)
        result = self._rpc("getTransactionsForAddress", [address, params], 10)
        return None if result is None else list(result.get("data") or [])

    def _rdap_age_days(self, domain: Optional[str]) -> dict[str, Any]:
        if not domain or not self.rdap_enabled:
            return {"social_website_domain_age_days": None,
                    "social_website_rdap_status": None if not domain else "disabled"}
        cached = self._rdap.get(domain)
        if cached and self.clock() - cached[0] < RDAP_CACHE_TTL_SECONDS:
            return cached[1]
        out = {"social_website_domain_age_days": None, "social_website_rdap_status": "error"}
        try:
            response = self.session.get(f"https://rdap.org/domain/{domain}", timeout=RDAP_TIMEOUT)
            if response.status_code == 200:
                events = response.json().get("events") or []
                reg = next((e.get("eventDate") for e in events
                            if e.get("eventAction") == "registration"), None)
                if reg:
                    created = datetime.fromisoformat(reg.replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - created).total_seconds() / 86400
                    out = {"social_website_domain_age_days": round(age, 1),
                           "social_website_rdap_status": "ok"}
                else:
                    out["social_website_rdap_status"] = "no_registration_date"
            else:
                out["social_website_rdap_status"] = f"http_{response.status_code}"
        except Exception:
            pass
        self._rdap[domain] = (self.clock(), out)
        return out

    # ---------------------------------------------------------------- calcul

    def compute(self, mint: str, website: Optional[str] = None) -> dict[str, Any]:
        started = self.clock()
        self._credits_this_job = 0
        out: dict[str, Any] = {"onchain_version": ONCHAIN_VERSION}
        out.update(self._rdap_age_days(website))
        try:
            self._compute_onchain(mint, out, started)
            meta = {"onchain_version", "onchain_status", "onchain_computed_at",
                    "onchain_compute_sec", "onchain_credits_used", "onchain_age_at_decision_sec"}
            ok = sum(1 for k in ONCHAIN_FIELDS if k not in meta and out.get(k) is not None)
            out["onchain_status"] = "ok" if ok >= 20 else ("partial" if ok else "error")
        except _BudgetExceeded:
            out["onchain_status"] = "budget"
        out.pop("_first_buyers", None)
        out["onchain_computed_at"] = round(self.clock(), 1)
        out["onchain_compute_sec"] = round(self.clock() - started, 2)
        out["onchain_credits_used"] = self._credits_this_job
        return out

    def _wallet_concentration(
        self, ranked: list[tuple[str, float]], supply: float
    ) -> dict[str, Any]:
        """1 crédit : programme propriétaire des 20 plus gros détenteurs."""
        owners = [o for o, _v in ranked[:20]]
        res = self._rpc("getMultipleAccounts", [owners, {
            "encoding": "base64", "dataSlice": {"offset": 0, "length": 0}}], 1)
        if res is None:
            return {}
        values = res.get("value") or []
        if len(values) != len(owners):
            return {}
        programs = {o: (v or {}).get("owner") if v else None for o, v in zip(owners, values)}
        return wallet_concentration(ranked[:20], programs, supply)

    def _compute_onchain(self, mint: str, out: dict[str, Any], now: float) -> None:
        info = self._rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}], 1)
        parsed = (((info or {}).get("value") or {}).get("data") or {})
        mint_info = (parsed.get("parsed") or {}).get("info") if isinstance(parsed, dict) else None
        supply = None
        if mint_info:
            out["onchain_mint_authority_revoked"] = mint_info.get("mintAuthority") is None
            out["onchain_freeze_authority_revoked"] = mint_info.get("freezeAuthority") is None
            try:
                supply = int(mint_info.get("supply")) / (10 ** int(mint_info.get("decimals") or 0))
            except (TypeError, ValueError):
                supply = None
            out["onchain_supply"] = _r(supply, 0)

        # Top 20 comptes -> propriétaires (agrégés par propriétaire).
        owners: dict[str, float] = {}
        largest = self._rpc("getTokenLargestAccounts", [mint], 1)
        accounts = [a for a in ((largest or {}).get("value") or []) if a.get("address")]
        if accounts:
            multi = self._rpc(
                "getMultipleAccounts", [[a["address"] for a in accounts[:20]], {"encoding": "jsonParsed"}], 1
            )
            for acc, val in zip(accounts[:20], (multi or {}).get("value") or []):
                owner = ((((val or {}).get("data") or {}).get("parsed") or {}).get("info") or {}).get("owner")
                if owner:
                    owners[owner] = owners.get(owner, 0.0) + float(acc.get("uiAmount") or 0)
        ranked = sorted(owners.items(), key=lambda kv: -kv[1])
        if supply and ranked:
            out["onchain_top1_pct"] = _r(100 * ranked[0][1] / supply)
            out["onchain_top10_pct"] = _r(100 * sum(v for _o, v in ranked[:10]) / supply)
            out.update(self._wallet_concentration(ranked, supply))

        early = self._txs(mint, sortOrder="asc", filters={"status": "succeeded"})
        launch = launch_metrics(early or [], mint, supply)
        first_buyers = launch.get("_first_buyers") or []
        out.update({k: v for k, v in launch.items() if not k.startswith("_")})
        if launch.get("onchain_launch_time"):
            out["onchain_token_age_min"] = round((now - launch["onchain_launch_time"]) / 60, 1)
        creator = launch.get("onchain_creator")
        if creator and supply:
            out["onchain_creator_pct_top20"] = _r(100 * owners.get(creator, 0.0) / supply)
        if early is not None and supply is not None and owners:
            in_top = [b for b in first_buyers if b in owners]
            out["onchain_first_buyers_in_top20"] = len(in_top)
            out["onchain_first_buyers_top20_pct"] = _r(100 * sum(owners[b] for b in in_top) / supply)

        if creator:
            sold = self._rpc("getTransactionsForAddress", [creator, {
                "transactionDetails": "signatures", "limit": 1000,
                "filters": {"tokenAccounts": "balanceChanged",
                            "tokenTransfer": {"mint": mint, "direction": "out"}},
            }], 10)
            if sold is not None:
                n_out = len(sold.get("data") or [])
                out["onchain_dev_out_transfers"] = n_out
                out["onchain_dev_sold"] = n_out > 0

        sigs = self._rpc("getTransactionsForAddress", [mint, {
            "transactionDetails": "signatures", "limit": 1000, "sortOrder": "desc",
            "filters": {"blockTime": {"gte": int(now - 900)}},
        }], 10)
        recent = self._txs(mint, sortOrder="desc",
                           filters={"status": "succeeded", "blockTime": {"gte": int(now - 900)}})
        older = self._txs(mint, sortOrder="desc", filters={
            "status": "succeeded", "blockTime": {"gte": int(now - 900), "lte": int(now - 300)}})
        out.update(activity_metrics(
            None if sigs is None else list(sigs.get("data") or []), recent, older, mint, now))

        # Wallets frais parmi les 10 plus gros porteurs et les 10 premiers acheteurs.
        top10 = [o for o, _v in ranked[:10]]
        early10 = first_buyers[:10]
        wallets = list(dict.fromkeys(top10 + early10))[:FRESH_CHECK_MAX_WALLETS]
        fresh: dict[str, Optional[bool]] = {}
        signatures: dict[str, Optional[list[dict]]] = {}
        for wallet in wallets:
            res = self._rpc("getSignaturesForAddress", [wallet, {"limit": FRESH_SIG_LIMIT}], 1)
            signatures[wallet] = res
            fresh[wallet] = is_fresh(res, now) if res is not None else None
        checked = [w for w in wallets if fresh.get(w) is not None]
        if checked:
            out["onchain_fresh_checked"] = len(checked)
            out["onchain_fresh_top10"] = sum(1 for w in top10 if fresh.get(w))
            out["onchain_fresh_first_buyers"] = sum(1 for w in early10 if fresh.get(w))

        # DÉTECTEUR INSIDER (2026-10-18, observation seule) : réutilise tout ce
        # qui précède ; seul ajout réseau = <= MAX_FUNDER_LOOKUPS getTransaction
        # (1 crédit). Isolé : budget ou panne ici ne change JAMAIS le statut
        # on-chain (donc ni la porte anti_rug), seulement `insider_status`.
        out.update(self._insider(mint, supply, owners, wallets, creator, fresh,
                                 signatures, early, recent, now))

    def _insider(self, mint, supply, owners, wallets, creator, fresh, signatures,
                 early, recent, now) -> dict[str, Any]:
        before = self._credits_this_job
        funders: dict[str, Optional[str]] = {}
        status = None
        try:
            for wallet in [w for w in wallets if fresh.get(w)][:MAX_FUNDER_LOOKUPS]:
                sig, _t = first_activity(signatures.get(wallet))
                if not sig:
                    continue
                tx = self._rpc("getTransaction", [sig, {
                    "encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}], 1)
                funders[wallet] = funder_of(tx, wallet)
        except _BudgetExceeded:
            status = "budget_partial"
        try:
            res = insider_metrics(
                mint=mint, supply=supply, holdings=owners, candidates=list(wallets),
                creator=creator, fresh=fresh, signatures=signatures, funders=funders,
                early_txs=early, recent_txs=recent, now=now,
            )
            if status:
                res["insider_status"] = status
        except Exception as exc:  # une mesure ne casse jamais l'observateur
            res = empty_insider("error")
            self._last_error = _mask(f"insider {type(exc).__name__}: {exc}")
        res["insider_credits_used"] = self._credits_this_job - before
        return res


def wallet_concentration(
    ranked: list[tuple[str, float]], owner_programs: dict[str, Optional[str]], supply: float
) -> dict[str, Any]:
    """Top 10 des WALLETS (hors pool/courbe/lockers détenus par un programme, hors burn).

    `owner_programs[owner]` = programme propriétaire du compte `owner` (None si
    le compte n'existe pas : wallet vidé de ses SOL, donc bien un wallet).
    """
    programs = [
        (o, v) for o, v in ranked
        if o in BURN_OWNERS or owner_programs.get(o) not in (None, SYSTEM_PROGRAM)
    ]
    wallets = [(o, v) for o, v in ranked if (o, v) not in programs]
    return {
        "onchain_top10_wallets_pct": _r(100 * sum(v for _o, v in wallets[:10]) / supply),
        "onchain_program_holders_top20": len(programs),
        "onchain_program_holders_pct": _r(100 * sum(v for _o, v in programs) / supply),
    }


# Helius répond `429 max usage reached` quand les crédits du MOIS sont épuisés (mesuré le 2026-11-21).
# Insister ne sert à rien : chaque candidat refaisait ses appels pour rien et finissait en « error ».
# On s'arrête une heure, puis on retente une fois (le quota peut se recharger en cours de journée).
QUOTA_MOIS_PAUSE_S = 3600


class _BudgetExceeded(Exception):
    pass
