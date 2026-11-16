"""Client LECTURE SEULE de l'API publique Hyperliquid (`POST /info`).

Les marchés de prédiction de Liquid sont des marchés HIP-4 d'Hyperliquid. Leur
lecture ne demande aucune clé. Ce module ne contient volontairement aucun
appel à `/exchange` : un pari réel est impossible depuis ce code.

Formes de réponse relevées dans la documentation publique le 2026-11-16, pas
encore confrontées à l'API depuis le Mac : chaque lecture est donc tolérante
et renvoie `None` ou une liste vide plutôt que d'inventer une valeur.
"""

import time
from typing import Any, Optional

import requests

from src.core.ratelimit import RateLimiter

BASE_URL = "https://api.hyperliquid.xyz/info"
REQUEST_TIMEOUT = 10
MAX_RETRIES = 3


def _f(value: Any) -> Optional[float]:
    """None reste None : un prix absent n'est jamais remplacé par 0."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class HyperliquidInfo:
    def __init__(self, session: Optional[requests.Session] = None,
                 limiter: Optional[RateLimiter] = None):
        self.session = session or requests.Session()
        # Quota public : 1200 de poids par minute. 120 appels/min laisse une
        # marge large même si chaque appel pesait 10.
        self.limiter = limiter or RateLimiter(120, 60.0, name="hyperliquid/info")

    def _post(self, body: dict[str, Any]) -> Optional[Any]:
        for attempt in range(MAX_RETRIES):
            if not self.limiter.acquire(timeout=30):
                return None
            try:
                response = self.session.post(BASE_URL, json=body, timeout=REQUEST_TIMEOUT)
                if response.status_code == 200:
                    return response.json()
                if response.status_code not in (429, 500, 502, 503):
                    return None
            except (requests.RequestException, ValueError):
                pass
            time.sleep(1.5 * (attempt + 1))
        return None

    def outcome_meta(self) -> Optional[dict[str, Any]]:
        data = self._post({"type": "outcomeMeta"})
        return data if isinstance(data, dict) else None

    def all_mids(self) -> dict[str, float]:
        data = self._post({"type": "allMids"})
        if not isinstance(data, dict):
            return {}
        mids = {}
        for coin, value in data.items():
            price = _f(value)
            if price is not None:
                mids[coin] = price
        return mids

    def l2_book(self, coin: str) -> Optional[dict[str, list[tuple[float, float]]]]:
        """Carnet : `bids` et `asks`, meilleur prix en premier, en (prix, taille)."""
        data = self._post({"type": "l2Book", "coin": coin})
        levels = data.get("levels") if isinstance(data, dict) else None
        if not isinstance(levels, list) or len(levels) < 2:
            return None

        def side(rows: Any) -> list[tuple[float, float]]:
            out = []
            for row in rows or []:
                px, sz = _f(row.get("px")), _f(row.get("sz"))
                if px is not None and sz is not None and sz > 0:
                    out.append((px, sz))
            return out

        return {"bids": side(levels[0]), "asks": side(levels[1])}

    def candles(self, coin: str, interval: str, start_ms: int, end_ms: int) -> list[dict[str, float]]:
        """Bougies `{t, T, c}` triées par temps. Liste vide si indisponible."""
        data = self._post({
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval,
                    "startTime": int(start_ms), "endTime": int(end_ms)},
        })
        if not isinstance(data, list):
            return []
        out = []
        for row in data:
            if not isinstance(row, dict):
                continue
            t, close = _f(row.get("t")), _f(row.get("c"))
            if t is None or close is None or close <= 0:
                continue
            out.append({"t": t, "T": _f(row.get("T")) or t, "c": close})
        out.sort(key=lambda r: r["t"])
        return out
