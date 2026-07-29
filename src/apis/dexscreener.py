"""Module DexScreener — découverte et données marché multi-chain.

API publique gratuite, aucune clé requise.
Quotas : 60 req/min (token-profiles, token-boosts), 300 req/min (pairs, tokens).

  - un RateLimiter par famille d'endpoints (au lieu d'un sleep global)
  - requêtes groupées : /latest/dex/tokens accepte 30 adresses par appel
  - filtrage par le cache AVANT tout appel réseau
"""

import time
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import requests

from src.core.cache import TokenCache
from src.core.models import Candidate
from src.core.ratelimit import RateLimiter

BASE_URL = "https://api.dexscreener.com"
MAX_ADDRESSES_PER_BATCH = 30
REQUEST_TIMEOUT = 10
MAX_RETRIES = 3


def _f(value: Any, default: float = 0.0) -> float:
    """Cast float tolérant (DexScreener renvoie None / str selon les champs)."""
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


class DexScreenerAPI:
    """Client DexScreener pour le bot MemeCoin Alpha Loop V2."""

    def __init__(self, cache: Optional[TokenCache] = None):
        self.cache = cache
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "MemeCoinAlphaLoop/2.0"})
        # Deux quotas distincts côté DexScreener -> deux limiteurs.
        self.rl_discovery = RateLimiter(60, 60.0, name="dexscreener/profiles")
        self.rl_data = RateLimiter(300, 60.0, name="dexscreener/pairs")
        self.request_count = 0

    def _get(self, url: str, limiter: RateLimiter) -> Optional[Any]:
        """GET avec rate limiting, retry exponentiel et gestion du 429."""
        for attempt in range(MAX_RETRIES):
            limiter.acquire()
            try:
                response = self.session.get(url, timeout=REQUEST_TIMEOUT)
                self.request_count += 1

                if response.status_code == 429:
                    backoff = float(response.headers.get("Retry-After", 2 ** (attempt + 1)))
                    print(f"[DexScreener] 429 rate limit -> pause {backoff:.0f}s")
                    time.sleep(backoff)
                    continue

                response.raise_for_status()
                return response.json()

            except requests.exceptions.Timeout:
                print(f"[DexScreener] Timeout ({attempt + 1}/{MAX_RETRIES}) {url}")
            except requests.exceptions.RequestException as exc:
                print(f"[DexScreener] Erreur réseau ({attempt + 1}/{MAX_RETRIES}) : {exc}")
            except ValueError as exc:
                print(f"[DexScreener] Réponse JSON invalide : {exc}")
                return None

            time.sleep(2**attempt)
        return None

    def get_latest_token_profiles(self) -> list[dict[str, Any]]:
        """Derniers profils de tokens ajoutés. Endpoint /token-profiles/latest/v1."""
        data = self._get(f"{BASE_URL}/token-profiles/latest/v1", self.rl_discovery)
        return data if isinstance(data, list) else []

    def get_latest_boosted_tokens(self) -> list[dict[str, Any]]:
        """Tokens boostés (payants = signal de promo active). /token-boosts/latest/v1."""
        data = self._get(f"{BASE_URL}/token-boosts/latest/v1", self.rl_discovery)
        return data if isinstance(data, list) else []

    def search_pairs(self, query: str) -> list[dict[str, Any]]:
        """Recherche libre de paires. /latest/dex/search?q="""
        data = self._get(f"{BASE_URL}/latest/dex/search?q={query}", self.rl_data)
        return (data or {}).get("pairs") or []

    def get_tokens_data(self, addresses: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
        """Données de plusieurs tokens en lots de 30. Retourne {address: [pairs]}."""
        unique = list(dict.fromkeys(a for a in addresses if a))
        out: dict[str, list[dict[str, Any]]] = {}

        for i in range(0, len(unique), MAX_ADDRESSES_PER_BATCH):
            batch = unique[i : i + MAX_ADDRESSES_PER_BATCH]
            data = self._get(f"{BASE_URL}/latest/dex/tokens/{','.join(batch)}", self.rl_data)
            for pair in (data or {}).get("pairs") or []:
                address = (pair.get("baseToken") or {}).get("address")
                if address:
                    out.setdefault(address, []).append(pair)
        return out

    def get_token_data(self, token_address: str, chain: Optional[str] = None) -> list[dict[str, Any]]:
        """Données d'un token unique, optionnellement filtrées par chaîne."""
        pairs = self.get_tokens_data([token_address]).get(token_address, [])
        if chain:
            pairs = [p for p in pairs if p.get("chainId") == chain]
        return pairs

    def get_pair_data(self, chain: str, pair_address: str) -> dict[str, Any]:
        """Données d'une paire précise (utilisé pour le monitoring de position)."""
        data = self._get(f"{BASE_URL}/latest/dex/pairs/{chain}/{pair_address}", self.rl_data)
        return data or {}

    def get_price(self, chain: str, pair_address: str) -> Optional[float]:
        """Prix courant d'une paire. None si indisponible."""
        pairs = self.get_pair_data(chain, pair_address).get("pairs") or []
        return _f(pairs[0].get("priceUsd")) if pairs else None

    def scan_new_meme_coins(
        self,
        chain: str = "solana",
        min_liquidity: float = 15000,
        min_volume_1h: float = 8000,
        max_age_hours: float = 6,
        min_age_hours: float = 0.5,
        include_boosted: bool = True,
        always_include: Optional[Iterable[str]] = None,
    ) -> tuple[list[Candidate], int]:
        """SCAN PRINCIPAL. Retourne (candidats, nb_sautés_par_cache).

        `always_include` : adresses à rafraîchir quoi qu'il arrive (watchlist).
        Indispensable car un token quitte /token-profiles/latest en quelques
        minutes alors qu'on a encore besoin de ses snapshots de prix.
        """
        discovered: dict[str, dict[str, Any]] = {}
        for profile in self.get_latest_token_profiles():
            address = profile.get("tokenAddress")
            if address and profile.get("chainId", chain) == chain:
                discovered[address] = profile
        if include_boosted:
            for boost in self.get_latest_boosted_tokens():
                address = boost.get("tokenAddress")
                if address and boost.get("chainId", chain) == chain:
                    discovered.setdefault(address, boost)

        forced = {a for a in (always_include or ()) if a}
        for address in forced:
            discovered.setdefault(address, {})

        skipped = 0
        to_fetch: list[str] = []
        for address in discovered:
            if self.cache and self.cache.is_blacklisted(address):
                skipped += 1
                continue
            if address not in forced and self.cache and self.cache.is_fresh(address):
                skipped += 1
                continue
            to_fetch.append(address)

        print(
            f"[DexScreener] {len(discovered)} tokens découverts sur {chain} "
            f"({len(forced)} surveillés) | {skipped} sautés (cache/blacklist) "
            f"| {len(to_fetch)} à interroger"
        )
        if not to_fetch:
            return [], skipped

        pairs_by_token = self.get_tokens_data(to_fetch)
        now_ms = datetime.now(timezone.utc).timestamp() * 1000
        candidates: list[Candidate] = []

        for address, pairs in pairs_by_token.items():
            if self.cache:
                self.cache.mark_seen(address)

            pairs = [p for p in pairs if p.get("chainId") == chain]
            if not pairs:
                continue

            best = max(pairs, key=lambda p: _f((p.get("liquidity") or {}).get("usd")))
            liquidity = _f((best.get("liquidity") or {}).get("usd"))
            volume = best.get("volume") or {}
            volume_1h = _f(volume.get("h1"))
            created_at = best.get("pairCreatedAt") or 0
            age_hours = (now_ms - created_at) / 3_600_000 if created_at else 999.0

            if liquidity < min_liquidity:
                continue
            if volume_1h < min_volume_1h:
                continue
            if not (min_age_hours <= age_hours <= max_age_hours):
                continue

            txns_5m = (best.get("txns") or {}).get("m5") or {}
            price_change = best.get("priceChange") or {}
            base = best.get("baseToken") or {}

            candidates.append(
                Candidate(
                    token_address=address,
                    symbol=base.get("symbol") or "UNKNOWN",
                    name=base.get("name") or "Unknown",
                    chain=chain,
                    pair_address=best.get("pairAddress"),
                    dex=best.get("dexId"),
                    url=best.get("url"),
                    price_usd=_f(best.get("priceUsd")),
                    liquidity_usd=liquidity,
                    market_cap=_f(best.get("marketCap")),
                    volume_5m=_f(volume.get("m5")),
                    volume_1h=volume_1h,
                    volume_24h=_f(volume.get("h24")),
                    age_hours=round(age_hours, 2),
                    volume_liquidity_ratio=round(volume_1h / liquidity, 3) if liquidity else 0.0,
                    price_change_5m=_f(price_change.get("m5")),
                    price_change_1h=_f(price_change.get("h1")),
                    price_change_24h=_f(price_change.get("h24")),
                    buys_5m=int(txns_5m.get("buys") or 0),
                    sells_5m=int(txns_5m.get("sells") or 0),
                    raw={"profile": discovered.get(address, {}), "pair": best},
                )
            )

        if self.cache:
            self.cache.save()

        candidates.sort(key=lambda c: c.volume_liquidity_ratio, reverse=True)
        print(f"[DexScreener] {len(candidates)} candidats passent les filtres marché")
        return candidates, skipped
