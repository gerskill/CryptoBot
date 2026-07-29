"""Module Helius — données on-chain Solana (holders, concentration, autorités).

Repli de Birdeye pour les holders. Sans clé, les méthodes retournent None et
le pipeline continue en mode dégradé.
"""

from dataclasses import dataclass
from typing import Any, Optional

import requests

from src.core.ratelimit import RateLimiter

REQUEST_TIMEOUT = 15
MAX_HOLDER_PAGES = 5  # au-delà on renvoie une borne inférieure
PAGE_LIMIT = 1000


@dataclass(frozen=True)
class HolderStats:
    """Statistiques de détention d'un mint."""

    holder_count: int
    is_exact: bool  # False = borne inférieure (pagination tronquée)
    top_holder_pct: Optional[float] = None
    top10_holder_pct: Optional[float] = None
    supply: Optional[float] = None


class HeliusAPI:
    """Client RPC Helius (JSON-RPC + DAS)."""

    def __init__(self, api_key: Optional[str]):
        self.api_key = api_key
        self.enabled = bool(api_key)
        self.session = requests.Session()
        self.rate_limiter = RateLimiter(600, 60.0, name="helius")
        self._request_id = 0

    @property
    def _url(self) -> str:
        return f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"

    def _rpc(self, method: str, params: Any) -> Optional[Any]:
        """Appel JSON-RPC. Retourne None en cas d'échec (jamais d'exception)."""
        if not self.enabled:
            return None
        self.rate_limiter.acquire()
        self._request_id += 1
        payload = {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
        try:
            response = self.session.post(self._url, json=payload, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            body = response.json()
            if "error" in body:
                print(f"[Helius] {method} erreur RPC : {body['error'].get('message')}")
                return None
            return body.get("result")
        except requests.exceptions.RequestException as exc:
            print(f"[Helius] {method} erreur réseau : {exc}")
        except ValueError as exc:
            print(f"[Helius] {method} JSON invalide : {exc}")
        return None

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
