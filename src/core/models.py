"""Modèles de données immuables du pipeline de scan."""

from dataclasses import dataclass, field, replace
from typing import Any, Optional


@dataclass(frozen=True)
class Candidate:
    """Un token candidat. Immuable : l'enrichissement retourne une copie."""

    # --- Identité (DexScreener) ---
    token_address: str
    symbol: str
    name: str
    chain: str
    pair_address: Optional[str] = None
    dex: Optional[str] = None
    url: Optional[str] = None

    # --- Marché (DexScreener) ---
    price_usd: float = 0.0
    liquidity_usd: float = 0.0
    market_cap: float = 0.0
    volume_5m: float = 0.0
    volume_1h: float = 0.0
    volume_24h: float = 0.0
    age_hours: float = 0.0
    volume_liquidity_ratio: float = 0.0
    price_change_5m: float = 0.0
    price_change_1h: float = 0.0
    price_change_24h: float = 0.0
    buys_5m: int = 0
    sells_5m: int = 0

    # --- On-chain (Birdeye en primaire, Helius en repli) ---
    holders: Optional[int] = None
    holders_is_exact: Optional[bool] = None  # False = borne inférieure
    top_holder_pct: Optional[float] = None
    top10_holder_pct: Optional[float] = None
    dev_wallet_pct: Optional[float] = None

    # --- Sécurité (RugCheck) ---
    rugcheck_score: Optional[float] = None
    rugcheck_risks: tuple[str, ...] = ()
    lp_locked_pct: Optional[float] = None
    mint_authority_revoked: Optional[bool] = None
    freeze_authority_revoked: Optional[bool] = None

    # --- Social (Twitter) ---
    social_mentions_1h: Optional[int] = None
    social_unique_authors: Optional[int] = None
    social_engagement: Optional[int] = None
    social_velocity_15m: Optional[int] = None
    social_sample_size: Optional[int] = None  # tweets échantillonnés (plafond 100)

    # --- Smart money (pas de source publique, voir README) ---
    smart_money_buys_30m: Optional[int] = None

    # --- Scoring ---
    alpha_score: float = 0.0  # 60% absolu + 40% rang dans le batch -> CLASSEMENT
    alpha_score_absolute: float = 0.0  # échelle fixe -> PORTE D'ENTRÉE
    sub_scores: dict[str, float] = field(default_factory=dict)

    # --- Diagnostic ---
    rejected_reason: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def with_fields(self, **kwargs: Any) -> "Candidate":
        """Retourne une nouvelle instance avec les champs mis à jour."""
        return replace(self, **kwargs)

    @property
    def buy_sell_ratio_5m(self) -> float:
        total = self.buys_5m + self.sells_5m
        if total == 0:
            return 0.5
        return self.buys_5m / total

    @property
    def holders_display(self) -> str:
        """`>=N` quand la pagination a été coupée : N est une borne inférieure."""
        if self.holders is None:
            return "?"
        return f"{self.holders}" if self.holders_is_exact else f"≥{self.holders}"

    def summary(self) -> str:
        return (
            f"{self.symbol:>10} | ${self.price_usd:<12.8f} "
            f"| Liq ${self.liquidity_usd:>9,.0f} "
            f"| Vol1h ${self.volume_1h:>9,.0f} "
            f"| Age {self.age_hours:>5.2f}h "
            f"| Hold {self.holders_display:>6} "
            f"| RC {self.rugcheck_score if self.rugcheck_score is not None else '?':>5} "
            f"| Alpha {self.alpha_score:>5.1f} (abs {self.alpha_score_absolute:>5.1f})"
        )


@dataclass(frozen=True)
class ScanResult:
    """Sortie d'un cycle de scan complet."""

    candidates: tuple[Candidate, ...] = ()
    rejected: tuple[Candidate, ...] = ()
    scanned_count: int = 0
    cached_skipped: int = 0
    duration_sec: float = 0.0

    @property
    def top(self) -> Optional[Candidate]:
        return self.candidates[0] if self.candidates else None
