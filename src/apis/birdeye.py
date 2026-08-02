"""Module Birdeye — holders exacts, bougies OHLCV réelles, nouveaux listings.

Remplace deux approximations du pipeline :
  - le compte de holders Helius (3 appels, borne inférieure) -> 1 appel exact
  - les bougies reconstruites depuis les snapshots -> vraies bougies

⚠️ DÉBIT : mesuré en live, deux appels enchaînés déclenchent
`429 {'success': False, 'message': 'Too many requests'}`. Le plan Starter est
à ~1 req/s. `/defi/token_security` renvoie 401 : non inclus dans le plan.
"""

import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

from src.core.ratelimit import RateLimiter

BASE_URL = "https://public-api.birdeye.so"
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class OverviewStats:
    """Vue marché + holders d'un token, en un seul appel."""

    holder_count: Optional[int] = None
    liquidity_usd: Optional[float] = None
    price_usd: Optional[float] = None
    volume_1h_usd: Optional[float] = None
    price_change_1h: Optional[float] = None
    market_cap: Optional[float] = None


@dataclass(frozen=True)
class OHLCVCandle:
    unix_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class BirdeyeAPI:
    """Client Birdeye. Sans clé, toutes les méthodes retournent None."""

    def __init__(self, api_key: Optional[str], chain: str = "solana"):
        self.api_key = api_key
        self.enabled = bool(api_key)
        self.chain = chain
        self.session = requests.Session()
        # 1 req/s : contrainte du plan, vérifiée par un 429 sur 2 appels enchaînés.
        self.rate_limiter = RateLimiter(1, 1.0, name="birdeye")
        self.request_count = 0

    def _headers(self) -> dict[str, str]:
        return {"X-API-KEY": self.api_key or "", "x-chain": self.chain}

    def _get(self, path: str, params: dict[str, Any]) -> Optional[dict[str, Any]]:
        """GET avec limiteur, retry sur 429. None si échec définitif."""
        if not self.enabled:
            return None

        for attempt in range(MAX_RETRIES):
            self.rate_limiter.acquire()
            try:
                response = self.session.get(
                    f"{BASE_URL}{path}",
                    params=params,
                    headers=self._headers(),
                    timeout=REQUEST_TIMEOUT,
                )
                self.request_count += 1

                if response.status_code == 429:
                    time.sleep(2 ** (attempt + 1))
                    continue
                if response.status_code in (401, 403):
                    print(f"[Birdeye] {response.status_code} non autorisé sur {path}")
                    return None

                # QUOTA MENSUEL ÉPUISÉ. Birdeye répond 400, pas 429, avec
                # « Compute units usage limit exceeded » — un code qui ressemble
                # à une requête malformée. Mesuré : 489 requêtes brûlées sur un
                # quota déjà mort, à 1 req/s, soit 23 s de chaque cycle de 90 s
                # dépensées pour rien. Se couper est la seule réponse utile :
                # un quota mensuel ne se rouvre pas en réessayant.
                if response.status_code == 400 and "compute unit" in response.text.lower():
                    print(
                        "[Birdeye] quota mensuel de Compute Units épuisé — module "
                        "désactivé. Les holders passent sur Helius (borne "
                        "inférieure) et l'analyse technique sur des bougies "
                        "reconstruites."
                    )
                    self.enabled = False
                    return None

                response.raise_for_status()
                body = response.json()
                if not body.get("success"):
                    print(f"[Birdeye] {path} : {str(body.get('message'))[:80]}")
                    return None
                return body.get("data")

            except requests.exceptions.RequestException as exc:
                print(f"[Birdeye] {path} erreur réseau ({attempt + 1}/{MAX_RETRIES}) : {exc}")
            except ValueError as exc:
                print(f"[Birdeye] {path} JSON invalide : {exc}")
                return None
            time.sleep(2**attempt)
        return None

    def get_overview(self, token_address: str) -> Optional[OverviewStats]:
        """Holders EXACTS + marché en un appel (remplace 3 appels Helius)."""
        data = self._get("/defi/token_overview", {"address": token_address})
        if not data:
            return None
        holder = data.get("holder")
        return OverviewStats(
            holder_count=int(holder) if holder is not None else None,
            liquidity_usd=_f(data.get("liquidity")) or None,
            price_usd=_f(data.get("price")) or None,
            volume_1h_usd=_f(data.get("v1hUSD")) or None,
            price_change_1h=data.get("priceChange1hPercent"),
            market_cap=_f(data.get("marketCap")) or None,
        )

    def get_price(self, token_address: str) -> Optional[float]:
        """Prix courant — utilisé pour le monitoring des positions."""
        data = self._get("/defi/price", {"address": token_address})
        return _f(data.get("value")) if data else None

    def get_top_holders(self, token_address: str, limit: int = 10) -> list[dict[str, Any]]:
        """Top holders. Le format des champs varie : on normalise en `pct`."""
        data = self._get(
            "/defi/v3/token/holder", {"address": token_address, "limit": limit, "offset": 0}
        )
        items = (data or {}).get("items") or []
        total = sum(_f(i.get("ui_amount") or i.get("uiAmount") or i.get("amount")) for i in items)
        holders = []
        for item in items:
            amount = _f(item.get("ui_amount") or item.get("uiAmount") or item.get("amount"))
            holders.append(
                {
                    "owner": item.get("owner") or item.get("address"),
                    "amount": amount,
                    "pct_of_top": round(100 * amount / total, 2) if total else 0.0,
                }
            )
        return holders

    def get_concentration(self, token_address: str, supply: Optional[float]) -> Optional[float]:
        """% de supply du plus gros holder. Nécessite la supply (Helius/RugCheck)."""
        if not supply:
            return None
        holders = self.get_top_holders(token_address, limit=1)
        if not holders:
            return None
        return round(100 * holders[0]["amount"] / supply, 2)

    def get_ohlcv(
        self, token_address: str, interval: str = "5m", lookback_seconds: int = 7200
    ) -> list[OHLCVCandle]:
        """Vraies bougies. Supprime l'attente de chauffe de l'historique maison."""
        now = int(time.time())
        data = self._get(
            "/defi/ohlcv",
            {
                "address": token_address,
                "type": interval,
                "time_from": now - lookback_seconds,
                "time_to": now,
            },
        )
        items = (data or {}).get("items") or []
        return [
            OHLCVCandle(
                unix_time=int(item.get("unixTime") or 0),
                open=_f(item.get("o")),
                high=_f(item.get("h")),
                low=_f(item.get("l")),
                close=_f(item.get("c")),
                volume=_f(item.get("v")),
            )
            for item in items
        ]

    def get_new_listings(self, limit: int = 20) -> list[dict[str, Any]]:
        """Source de découverte plus fraîche que /token-profiles de DexScreener."""
        data = self._get("/defi/v2/tokens/new_listing", {"limit": limit})
        return (data or {}).get("items") or []
