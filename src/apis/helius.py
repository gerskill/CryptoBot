"""Module Helius — données on-chain Solana (holders, concentration, autorités).

Repli de Birdeye pour les holders. Sans clé, les méthodes retournent None et
le pipeline continue en mode dégradé.

Un seul limiteur pour tout le processus (scan et surveillance des positions).
La surveillance passe devant. Après un 429, on attend, on ne rappelle pas
tout de suite, et on s'arrête au bout de quelques essais.
"""

import contextvars
import json
import random
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Optional

import requests

REQUEST_TIMEOUT = 15
MAX_HOLDER_PAGES = 5  # au-delà on renvoie une borne inférieure
PAGE_LIMIT = 1000

# LISTE BLANCHE DES MÉTHODES RPC. `gmgn.py` (ALLOWED_COMMANDS) et `jupiter.py`
# (ALLOWED_PATHS) vérifient AVANT la requête ; `_rpc()` ici acceptait N'IMPORTE
# QUELLE méthode, sans y être jamais poussé par une entrée dynamique — mais
# l'absence de garde structurel était une incohérence avec le reste du dépôt,
# trouvée en auditant l'ajout de `getRecentPrioritizationFees`. Toutes des
# méthodes de LECTURE ; aucune ne signe ni ne soumet de transaction.
ALLOWED_RPC_METHODS = frozenset({
    "getAsset",
    "getTokenAccounts",
    "getTokenLargestAccounts",
    "getTokenSupply",
    "getRecentPrioritizationFees",
})

# Un swap Jupiter simple (une route, pas d'aggrégation multi-hop) consomme
# généralement 150-300k unités de calcul. C'est une HYPOTHÈSE, pas une mesure
# — `getRecentPrioritizationFees` rend un PRIX par unité (micro-lamports/CU),
# jamais un total ; il faut le multiplier par un budget de CU pour obtenir un
# coût en lamports, et ce budget n'est connu qu'à la construction réelle de la
# transaction (hors périmètre : `/swap/v2/build` est absent d'ALLOWED_PATHS).
# 200k est le milieu de la fourchette usuelle ; documenté ici pour qu'un
# lecteur futur sache que c'est une supposition, pas un fait mesuré.
ASSUMED_SWAP_COMPUTE_UNITS = 200_000

# Données stables. `getTokenAccounts` n'y est pas : c'est le compte de
# holders lu par les filtres, on ne le fige pas.
CACHE_TTL_SECONDS = {
    "getTokenSupply": 300.0,
    "getTokenLargestAccounts": 120.0,
    "getAsset": 3600.0,
    "getRecentPrioritizationFees": 20.0,
}
# 2/s : sous le palier gratuit (souvent 10/s), qui répondait 429 en rafale.
DEFAULT_MAX_PER_SECOND = 2
MAX_RPC_ATTEMPTS = 3
MAX_BACKOFF_SECONDS = 8.0

_HELIUS_PRIORITY: contextvars.ContextVar[str] = contextvars.ContextVar(
    "helius_priority", default="normal"
)


class use_watch_priority:
    """Les appels Helius du bloc passent devant ceux du scan."""

    def __enter__(self) -> "use_watch_priority":
        self._token = _HELIUS_PRIORITY.set("watch")
        return self

    def __exit__(self, *exc: object) -> None:
        _HELIUS_PRIORITY.reset(self._token)


class PriorityLimiter:
    """Fenêtre glissante, partagée, avec priorité `watch` sur `normal`.

    `max_calls` est le plafond réel sur `period` secondes, pas 90 % de
    ce chiffre. Thread-safe. Un 429 noté ici fait attendre tout le monde.
    """

    def __init__(
        self,
        max_calls: int,
        period: float = 1.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_calls <= 0:
            raise ValueError("max_calls doit être > 0")
        self.max_calls = int(max_calls)
        self.period = float(period)
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._waiters = {"watch": 0, "normal": 0}
        self._backoff_until = 0.0
        self._cv = threading.Condition()

    def note_backoff(self, seconds: float) -> None:
        delay = max(0.0, float(seconds))
        with self._cv:
            self._backoff_until = max(self._backoff_until, self._clock() + delay)
            self._cv.notify_all()

    def _sleep_backoff(self) -> None:
        with self._cv:
            delay = self._backoff_until - self._clock()
        if delay > 0:
            self._sleep(min(delay, MAX_BACKOFF_SECONDS))

    def waiting(self, priority: str) -> int:
        with self._cv:
            return self._waiters.get(priority, 0)

    def acquire(self, timeout: Optional[float] = None, priority: Optional[str] = None) -> bool:
        """Prend un créneau. `watch` passe avant `normal`. False si timeout."""
        priority = priority or "normal"
        if priority not in self._waiters:
            priority = "normal"
        self._sleep_backoff()
        deadline = None if timeout is None else self._clock() + timeout
        with self._cv:
            self._waiters[priority] += 1
            try:
                while True:
                    now = self._clock()
                    self._evict(now)
                    watch_ahead = self._waiters["watch"] > (1 if priority == "watch" else 0)
                    if len(self._calls) < self.max_calls and not (
                        priority != "watch" and watch_ahead
                    ):
                        self._calls.append(now)
                        return True
                    if deadline is not None and now >= deadline:
                        return False
                    wait = self.period
                    if self._calls:
                        wait = min(wait, self.period - (now - self._calls[0]))
                    if deadline is not None:
                        wait = min(wait, max(0.0, deadline - now))
                    self._cv.wait(timeout=max(wait, 0.01))
            finally:
                self._waiters[priority] -= 1
                self._cv.notify_all()

    def _evict(self, now: float) -> None:
        while self._calls and now - self._calls[0] >= self.period:
            self._calls.popleft()


@dataclass(frozen=True)
class HolderStats:
    """Statistiques de détention d'un mint."""

    holder_count: int
    is_exact: bool  # False = borne inférieure (pagination tronquée)
    top_holder_pct: Optional[float] = None
    top10_holder_pct: Optional[float] = None
    supply: Optional[float] = None


class ForbiddenRpcMethod(RuntimeError):
    """Méthode RPC absente de la liste blanche — refusée avant l'appel."""


def _safe_error(exc: Exception) -> str:
    """Message d'erreur sans la clé API.

    L'URL Helius porte la clé en query string et `requests` l'inclut dans
    chaque message d'exception. Un log est un fichier qu'on partage, qu'on
    colle dans un ticket, qu'on donne à une IA : la clé ne doit jamais y
    entrer. On garde le type et le code, on jette l'URL.
    """
    texte = re.sub(r"api-key=[^&\s\"']+", "api-key=<masquée>", str(exc))
    return f"{type(exc).__name__} — {texte[:160]}"


def _retry_delay(headers: Any, attempt: int, rng: Callable[[], float]) -> float:
    """Secondes à attendre après un 429. `Retry-After` d'abord, sinon exponentiel."""
    header = None
    if headers is not None:
        header = headers.get("Retry-After") if hasattr(headers, "get") else None
    if header:
        try:
            return min(MAX_BACKOFF_SECONDS, max(0.0, float(header)))
        except (TypeError, ValueError):
            try:
                when = parsedate_to_datetime(str(header))
                return min(MAX_BACKOFF_SECONDS, max(0.0, when.timestamp() - time.time()))
            except (TypeError, ValueError, OverflowError):
                pass
    # attempt 1 -> 1s, 2 -> 2s, 3 -> 4s, plafonné, avec gigue 0,5–1,5.
    base = min(MAX_BACKOFF_SECONDS, float(2 ** max(0, attempt - 1)))
    jitter = 0.5 + rng()
    return min(MAX_BACKOFF_SECONDS, base * jitter)


_MISS = object()


def _cache_key(method: str, params: Any) -> Optional[tuple]:
    if method not in CACHE_TTL_SECONDS:
        return None
    try:
        return (method, json.dumps(params, sort_keys=True, default=str))
    except (TypeError, ValueError):
        return None


class HeliusAPI:
    """Client RPC Helius (JSON-RPC + DAS)."""

    def __init__(
        self,
        api_key: Optional[str],
        max_per_second: int = DEFAULT_MAX_PER_SECOND,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
    ):
        self.api_key = api_key
        self.enabled = bool(api_key)
        self.session = requests.Session()
        self._clock = clock
        self._rng = rng
        # Un seul limiteur pour ce client. L'observateur on-chain reçoit
        # le même objet : scan et surveillance ne peuvent plus additionner
        # leurs rafales.
        self.rate_limiter = PriorityLimiter(
            max(1, int(max_per_second)), 1.0, clock=clock, sleep=sleep,
        )
        self.rate_limited = 0
        self._request_id = 0
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._cache_lock = threading.Lock()

    @property
    def _url(self) -> str:
        return f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"

    def _cached(self, key: tuple) -> Any:
        ttl = CACHE_TTL_SECONDS.get(key[0])
        if ttl is None:
            return _MISS
        with self._cache_lock:
            item = self._cache.get(key)
            if item is None:
                return _MISS
            stored_at, value = item
            if self._clock() - stored_at >= ttl:
                del self._cache[key]
                return _MISS
            return value

    def _store(self, key: Optional[tuple], value: Any) -> None:
        if key is None or value is None:
            return
        with self._cache_lock:
            self._cache[key] = (self._clock(), value)

    def _rpc(self, method: str, params: Any) -> Optional[Any]:
        """Appel JSON-RPC. Retourne None en cas d'échec (jamais d'exception).

        Refuse toute méthode absente de la liste blanche — même garde-fou que
        `gmgn.py`/`jupiter.py`, appliqué AVANT la requête. Au plus
        `MAX_RPC_ATTEMPTS` essais. Un 429 attend, il ne relance pas tout de suite.
        """
        if method not in ALLOWED_RPC_METHODS:
            raise ForbiddenRpcMethod(f"méthode '{method}' interdite — lecture seule uniquement")
        if not self.enabled:
            return None
        key = _cache_key(method, params)
        if key is not None:
            cached = self._cached(key)
            if cached is not _MISS:
                return cached
        priority = _HELIUS_PRIORITY.get()
        for attempt in range(1, MAX_RPC_ATTEMPTS + 1):
            if not self.rate_limiter.acquire(timeout=4.0, priority=priority):
                return None
            self._request_id += 1
            payload = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params,
            }
            try:
                response = self.session.post(self._url, json=payload, timeout=REQUEST_TIMEOUT)
            except requests.exceptions.RequestException as exc:
                # FUITE DE CLÉ. `requests` place l'URL COMPLÈTE dans le message
                # d'exception, et l'URL Helius porte la clé en query string.
                print(f"[Helius] {method} erreur réseau : {_safe_error(exc)}")
                return None
            status = getattr(response, "status_code", 200)
            if status == 429:
                self.rate_limited += 1
                delay = _retry_delay(getattr(response, "headers", {}), attempt, self._rng)
                self.rate_limiter.note_backoff(delay)
                print(f"[Helius] {method} 429 — pause {delay:.1f}s (essai {attempt}/{MAX_RPC_ATTEMPTS})")
                if attempt >= MAX_RPC_ATTEMPTS:
                    return None
                continue
            if status >= 400:
                print(f"[Helius] {method} HTTP {status}")
                return None
            try:
                body = response.json()
            except ValueError as exc:
                print(f"[Helius] {method} JSON invalide : {exc}")
                return None
            if isinstance(body, dict) and "error" in body:
                message = body["error"].get("message") if isinstance(body["error"], dict) else "erreur"
                print(f"[Helius] {method} erreur RPC : {message}")
                return None
            result = body.get("result") if isinstance(body, dict) else None
            self._store(key, result)
            return result
        return None

    def get_recent_prioritization_fee_lamports(
        self, compute_units: int = ASSUMED_SWAP_COMPUTE_UNITS
    ) -> Optional[float]:
        """Coût estimé de priorité pour UNE transaction, en lamports.

        `getRecentPrioritizationFees` rend un PRIX (micro-lamports par unité
        de calcul) sur les derniers slots — pas un coût de transaction. On
        prend la MÉDIANE, pas la moyenne : un seul slot en pleine congestion
        ferait exploser une moyenne pour un prix qui n'a duré qu'un slot.

        Multiplié par `compute_units`, hypothèse documentée sur
        `ASSUMED_SWAP_COMPUTE_UNITS` : le vrai budget de CU n'est connu qu'à
        la construction de la transaction, hors périmètre de ce bot.

        `None` = non mesurable (clé absente, RPC indisponible, réponse vide).
        Ne bloque jamais l'appelant — même invariant que le reste du pipeline.
        """
        result = self._rpc("getRecentPrioritizationFees", [])
        if not result:
            return None
        prix = sorted(int(r.get("prioritizationFee", 0)) for r in result if "prioritizationFee" in r)
        if not prix:
            return None
        mediane = prix[len(prix) // 2]
        return (mediane * compute_units) / 1_000_000  # micro-lamports -> lamports

    def get_supply(self, mint: str) -> Optional[float]:
        result = self._rpc("getTokenSupply", [mint])
        if not result:
            return None
        return float((result.get("value") or {}).get("uiAmount") or 0)

    def get_top_holders(self, mint: str, supply: Optional[float] = None) -> list[dict[str, float]]:
        """20 plus gros comptes du mint, avec leur % de supply."""
        result = self._rpc("getTokenLargestAccounts", [mint])
        if not result:
            return []
        supply = supply if supply is not None else self.get_supply(mint)
        if not supply:
            return []
        holders = []
        for account in result.get("value") or []:
            amount = float(account.get("uiAmount") or 0)
            holders.append(
                {"address": account.get("address"), "amount": amount, "pct": 100 * amount / supply}
            )
        return holders

    def count_holders(self, mint: str, stop_after: Optional[int] = None) -> tuple[int, bool]:
        """Compte les comptes détenteurs avec solde > 0.

        `stop_after` : arrêt anticipé dès ce seuil atteint. Retourne
        (compte, exact) — `exact=False` signifie que le compte est une borne
        inférieure, pas une valeur.
        """
        total, cursor, exact = 0, None, True
        for page in range(MAX_HOLDER_PAGES):
            params: dict[str, Any] = {"mint": mint, "limit": PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor
            result = self._rpc("getTokenAccounts", params)
            if not result:
                return total, False

            accounts = result.get("token_accounts") or []
            total += sum(1 for a in accounts if float(a.get("amount") or 0) > 0)
            cursor = result.get("cursor")

            if stop_after is not None and total >= stop_after:
                return total, False
            if not cursor or len(accounts) < PAGE_LIMIT:
                return total, exact
        return total, False

    def get_holder_stats(self, mint: str, min_required: Optional[int] = None) -> Optional[HolderStats]:
        """Bundle holders + concentration. None si Helius indisponible."""
        if not self.enabled:
            return None
        supply = self.get_supply(mint)
        top = self.get_top_holders(mint, supply=supply)
        stop_after = min_required * 4 if min_required else None
        count, exact = self.count_holders(mint, stop_after=stop_after)
        if count == 0 and not top:
            return None
        return HolderStats(
            holder_count=count,
            is_exact=exact,
            top_holder_pct=round(top[0]["pct"], 2) if top else None,
            top10_holder_pct=round(sum(h["pct"] for h in top[:10]), 2) if top else None,
            supply=supply,
        )

    def get_asset(self, mint: str) -> Optional[dict[str, Any]]:
        """Métadonnées DAS : créateurs, autorités, mutabilité."""
        return self._rpc("getAsset", {"id": mint})

    def get_creator_address(self, mint: str) -> Optional[str]:
        """Adresse du créateur (proxy du 'dev wallet'), si exposée."""
        asset = self.get_asset(mint)
        if not asset:
            return None
        creators = asset.get("creators") or []
        if creators:
            return creators[0].get("address")
        authorities = asset.get("authorities") or []
        return authorities[0].get("address") if authorities else None

    def get_dev_wallet_pct(
        self, mint: str, top_holders: Optional[list[dict[str, float]]] = None
    ) -> Optional[float]:
        """% de supply détenu par le créateur.

        Limite connue : ne voit le dev que s'il figure dans le top 20 des
        comptes. Un dev réparti sur plusieurs wallets passe sous le radar —
        RugCheck complète cette vue.
        """
        creator = self.get_creator_address(mint)
        if not creator:
            return None
        holders = top_holders if top_holders is not None else self.get_top_holders(mint)
        for holder in holders:
            if holder.get("address") == creator:
                return round(holder["pct"], 2)
        return 0.0
