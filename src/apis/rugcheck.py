"""Module RugCheck.xyz — audit sécurité des tokens Solana.

API publique (pas de clé pour les endpoints /report). Rate limit non documenté
officiellement : on se cale prudemment sur 60 req/min.

⚠️ CONVENTION DE SCORE — point important :
RugCheck renvoie un score de RISQUE : plus il est HAUT, plus le token est
DANGEREUX. Le params.json du bot utilise `min_rugcheck_score: 70` au sens
inverse (plus haut = plus sûr). Ce module convertit donc en SCORE DE SÛRETÉ :

    safety_score = 100 - min(score_normalised, 100)

C'est `safety_score` qui est comparé à filters.min_rugcheck_score.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

import requests

from src.core.ratelimit import RateLimiter

BASE_URL = "https://api.rugcheck.xyz/v1"
REQUEST_TIMEOUT = 12

# Risques qui déclenchent un rejet immédiat quel que soit le score.
CRITICAL_RISKS = {
    "Freeze Authority still enabled",
    "Mint Authority still enabled",
    "Honeypot",
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class RugCheckReport:
    """Rapport de sécurité normalisé."""

    safety_score: float  # 0-100, PLUS HAUT = PLUS SÛR
    raw_risk_score: float
    risks: tuple[str, ...] = ()
    has_critical_risk: bool = False
    lp_locked_pct: Optional[float] = None
    mint_authority_revoked: Optional[bool] = None
    freeze_authority_revoked: Optional[bool] = None
    top_holder_pct: Optional[float] = None
    dev_wallet_pct: Optional[float] = None
    rugged: bool = False
    insiders_detected: Optional[bool] = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class RugCheckAPI:
    """Client RugCheck avec normalisation du score."""

    def __init__(self, max_calls_per_min: int = 60):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "MemeCoinAlphaLoop/2.0"})
        self.rate_limiter = RateLimiter(max_calls_per_min, 60.0, name="rugcheck")

    def _get(self, path: str) -> Optional[dict[str, Any]]:
        self.rate_limiter.acquire()
        try:
            response = self.session.get(f"{BASE_URL}{path}", timeout=REQUEST_TIMEOUT)
            if response.status_code == 404:
                return None  # token inconnu de RugCheck
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as exc:
            print(f"[RugCheck] Erreur {path} : {exc}")
        except ValueError as exc:
            print(f"[RugCheck] JSON invalide {path} : {exc}")
        return None

    def get_report(self, mint: str, full: bool = True) -> Optional[RugCheckReport]:
        """Rapport d'un token.

        `full=True` (défaut) : /report — coûte le même slot de quota que le
        résumé mais fournit en plus le créateur, son solde (dev wallet %), le
        flag `rugged`, les réseaux d'insiders et les autorités mint/freeze.
        `full=False` : /report/summary — score + risques + lpLockedPct.
        """
        path = f"/tokens/{mint}/report" if full else f"/tokens/{mint}/report/summary"
        data = self._get(path)
        if data is None:
            return None
        return self._parse(data)

    def _parse(self, data: dict[str, Any]) -> RugCheckReport:
        raw_risk = _f(data.get("score_normalised"), _f(data.get("score"), 0.0))
        safety = max(0.0, 100.0 - min(raw_risk, 100.0))

        risks = tuple(r.get("name", "") for r in (data.get("risks") or []))
        rugged = bool(data.get("rugged"))
        has_critical = rugged or any(
            any(critical.lower() in risk.lower() for critical in CRITICAL_RISKS) for risk in risks
        )

        insiders = data.get("graphInsidersDetected")

        return RugCheckReport(
            safety_score=round(safety, 1),
            raw_risk_score=raw_risk,
            risks=risks,
            has_critical_risk=has_critical,
            lp_locked_pct=self._lp_locked_pct(data),
            mint_authority_revoked=self._authority_revoked(data, "mintAuthority"),
            freeze_authority_revoked=self._authority_revoked(data, "freezeAuthority"),
            top_holder_pct=self._top_holder_pct(data),
            dev_wallet_pct=self._dev_wallet_pct(data),
            rugged=rugged,
            insiders_detected=None if insiders is None else bool(insiders),
            raw=data,
        )

    # ------------------------------------------------------------- parsing

    @staticmethod
    def _lp_locked_pct(data: dict[str, Any]) -> Optional[float]:
        """Présent au top-level dans /summary, sous markets[].lp dans /report."""
        if data.get("lpLockedPct") is not None:
            return _f(data["lpLockedPct"])
        markets = data.get("markets") or []
        if markets:
            lp = (markets[0] or {}).get("lp") or {}
            if lp.get("lpLockedPct") is not None:
                return _f(lp["lpLockedPct"])
        return None

    @staticmethod
    def _authority_revoked(data: dict[str, Any], key: str) -> Optional[bool]:
        """True = autorité révoquée (bon signe). None = information absente."""
        if key in data:
            return data.get(key) in (None, "", "11111111111111111111111111111111")
        token = data.get("token")
        if isinstance(token, dict) and key in token:
            return token.get(key) in (None, "", "11111111111111111111111111111111")
        return None

    @staticmethod
    def _top_holder_pct(data: dict[str, Any]) -> Optional[float]:
        holders = data.get("topHolders") or []
        if not holders:
            return None
        # Les comptes marqués `insider=False` incluent les pools : on les ignore.
        real = [h for h in holders if not h.get("owner", "").startswith("Pool")]
        return _f((real or holders)[0].get("pct")) or None

    @staticmethod
    def _dev_wallet_pct(data: dict[str, Any]) -> Optional[float]:
        """% de supply détenu par le créateur (rapport complet uniquement)."""
        balance = data.get("creatorBalance")
        supply = ((data.get("token") or {}).get("supply"))
        if balance is None or not supply:
            return None
        return round(100 * _f(balance) / _f(supply), 3)
