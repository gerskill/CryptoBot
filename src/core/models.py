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

    # --- Smart money (GMGN, lecture seule) ---
    smart_money_buys_30m: Optional[int] = None
    smart_money_sells_30m: Optional[int] = None
    smart_money_wallets_30m: Optional[int] = None
    smart_money_volume_usd: Optional[float] = None
    # Achats pondérés par la fraîcheur : un achat vieux de 29 min pèse moins
    # qu'un achat d'il y a 30 s. Le compte brut les traitait à égalité.
    smart_money_weighted_buys: Optional[float] = None
    smart_money_newest_age_min: Optional[float] = None

    # --- Manipulation et qualité du carnet (GMGN market trending) ---
    # Tous rendus par le MÊME appel que la découverte : les filtrer ne coûte
    # aucune requête. `None` = donnée absente et ne rejette jamais.
    swaps: Optional[int] = None
    sniper_count: Optional[int] = None  # bots ayant acheté au lancement
    bot_degen_count: Optional[int] = None
    renowned_count: Optional[int] = None  # wallets KOL identifiés
    bundler_rate: Optional[float] = None  # 0-1, achats groupés dans un bloc
    insider_rate: Optional[float] = None  # 0-1
    entrapment_ratio: Optional[float] = None  # 0-1, piège à acheteurs
    rug_ratio: Optional[float] = None
    is_wash_trading: Optional[bool] = None
    bluechip_owner_pct: Optional[float] = None

    # --- Réputation du créateur ---
    burn_ratio: Optional[float] = None
    dev_token_burn_ratio: Optional[float] = None
    creator_token_status: Optional[str] = None  # creator_hold / creator_sell...
    twitter_rename_count: Optional[int] = None
    twitter_create_token_count: Optional[int] = None  # tokens déjà lancés par ce compte

    # --- Contexte de lancement ---
    has_social: Optional[bool] = None
    launchpad: Optional[str] = None
    initial_liquidity: Optional[float] = None
    history_highest_market_cap: Optional[float] = None
    price_change_1m: Optional[float] = None
    # Promotion payée sur DexScreener : signal d'intention, pas de qualité.
    dexscreener_paid: Optional[bool] = None

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
    def drawdown_from_ath_pct(self) -> Optional[float]:
        """Recul depuis le plus haut market cap historique, en %.

        0 = au sommet. Achète-t-on le haut de la bougie, ou un repli ?
        """
        if not self.history_highest_market_cap or not self.market_cap:
            return None
        return 100 * (1 - self.market_cap / self.history_highest_market_cap)

    @property
    def liquidity_growth_ratio(self) -> Optional[float]:
        """Liquidité actuelle / liquidité initiale. <1 = la LP se vide."""
        if not self.initial_liquidity:
            return None
        return self.liquidity_usd / self.initial_liquidity

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
