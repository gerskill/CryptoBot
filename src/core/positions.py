"""Positions papier et règles de sortie — étape 4 du workflow.

ORDRE DE SORTIE — écart assumé vs la spec : la détection de rug pull y est
listée en 7e position, elle est évaluée EN PREMIER ici. Un rug vide la
liquidité en secondes ; l'évaluer après les take-profits garantirait de sortir
sur un carnet déjà vide.
"""

import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Optional

RUG_LIQUIDITY_DROP_PCT = -50.0


@dataclass(frozen=True)
class ExitAction:
    """Une sortie à exécuter : quelle fraction de la position initiale, pourquoi."""

    fraction: float
    reason: str
    is_final: bool = False


@dataclass(frozen=True)
class Position:
    token_address: str
    symbol: str
    chain: str
    entry_price: float
    size_usd: float
    mode: str = "PAPER"
    pair_address: Optional[str] = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    entry_time: float = field(default_factory=time.time)

    alpha_score: float = 0.0
    liquidity_at_entry: float = 0.0
    holders_at_entry: Optional[int] = None
    volume_1h_at_entry: float = 0.0
    rugcheck_score: Optional[float] = None
    social_score: Optional[int] = None
    age_hours_at_entry: float = 0.0
    params_version: str = ""

    # Règles figées à l'entrée : un changement de params ne doit pas modifier
    # rétroactivement les règles d'une position déjà ouverte.
    stop_loss_pct: float = -25.0
    take_profit_1: float = 100.0
    take_profit_2: float = 300.0
    take_profit_3: float = 500.0
    partial_sell_tp1_pct: float = 0.5
    partial_sell_tp2_pct: float = 0.5
    trailing_stop_activation: float = 200.0
    trailing_stop_distance_pct: float = 50.0
    max_hold_time_minutes: float = 240.0

    # Tampon anticipant le glissement du stop loss (voir `effective_stop_loss_pct`)
    stop_loss_slippage_buffer_pct: float = 0.0
    # Contexte de prix à l'entrée — sert à comprendre ce que le score achète
    price_change_5m_at_entry: float = 0.0

    remaining_fraction: float = 1.0
    high_water_pct: float = 0.0
    # Instrumentation : quand le pic a été atteint, et le point bas traversé.
    high_water_at: Optional[float] = None
    low_water_pct: float = 0.0
    low_water_at: Optional[float] = None
    tp1_hit: bool = False
    tp2_hit: bool = False
    breakeven_moved: bool = False
    trailing_active: bool = False
    realized_pnl_usd: float = 0.0
    exit_reasons: tuple[str, ...] = ()

    @property
    def is_open(self) -> bool:
        return self.remaining_fraction > 1e-9

    @property
    def effective_stop_loss_pct(self) -> float:
        """Seuil de sortie réel, tampon de glissement inclus.

        Mesuré sur les 8 premières sorties SL : 8/8 sortaient SOUS leur seuil,
        de 4.6 points en moyenne, le prix traversant le niveau entre deux
        mesures. Déclencher plus tôt d'autant vise un atterrissage au seuil
        voulu plutôt qu'en dessous.
        """
        base = 0.0 if self.breakeven_moved else self.stop_loss_pct
        return base + self.stop_loss_slippage_buffer_pct

    @property
    def minutes_to_peak(self) -> Optional[float]:
        """Délai entre l'entrée et le plus haut — calibre les take profits."""
        if self.high_water_at is None:
            return None
        return (self.high_water_at - self.entry_time) / 60

    @property
    def minutes_to_trough(self) -> Optional[float]:
        """Délai entre l'entrée et le plus bas.

        Le pendant de `minutes_to_peak`, et la pièce qui manquait pour rejouer
        les règles de sortie : avec les deux dates, on sait si le prix a touché
        le stop AVANT ou APRÈS le take profit. Sans elle, un trade qui a fait
        +130% puis -11% est indistinguable de l'inverse, et le rejeu doit
        deviner.
        """
        if self.low_water_at is None:
            return None
        return (self.low_water_at - self.entry_time) / 60

    def pnl_pct(self, price: float) -> float:
        if self.entry_price <= 0:
            return 0.0
        return 100 * (price - self.entry_price) / self.entry_price

    def duration_minutes(self, now: Optional[float] = None) -> float:
        return ((now or time.time()) - self.entry_time) / 60


def evaluate_exits(
    position: Position, price: float, liquidity_drop_pct: Optional[float] = None
) -> list[ExitAction]:
    """Sorties déclenchées par ce tick. Liste vide = on garde la position."""
    if not position.is_open:
        return []

    pnl = position.pnl_pct(price)

    # 1. RUG PULL — évalué en premier (voir docstring du module)
    if liquidity_drop_pct is not None and liquidity_drop_pct <= RUG_LIQUIDITY_DROP_PCT:
        return [
            ExitAction(
                position.remaining_fraction,
                f"RUG_PULL (liquidité {liquidity_drop_pct:.0f}%)",
                is_final=True,
            )
        ]

    # 2. STOP LOSS (ou breakeven après TP1)
    if pnl <= position.effective_stop_loss_pct:
        label = "BREAKEVEN_STOP" if position.breakeven_moved else "STOP_LOSS"
        return [ExitAction(position.remaining_fraction, f"{label} ({pnl:.1f}%)", is_final=True)]

    # 3. TIME STOP
    if position.duration_minutes() >= position.max_hold_time_minutes:
        return [
            ExitAction(
                position.remaining_fraction,
                f"TIME_STOP ({position.duration_minutes():.0f} min, {pnl:+.1f}%)",
                is_final=True,
            )
        ]

    # 4. TRAILING STOP (actif seulement après TP2 ou +200%)
    if position.trailing_active:
        drawdown = position.high_water_pct - pnl
        if drawdown >= position.trailing_stop_distance_pct:
            return [
                ExitAction(
                    position.remaining_fraction,
                    f"TRAILING_STOP (plus haut {position.high_water_pct:.0f}%, "
                    f"retour {pnl:+.1f}%)",
                    is_final=True,
                )
            ]

    # 5. TAKE PROFITS — cascade possible sur un même tick si le prix a sauté
    actions: list[ExitAction] = []
    if pnl >= position.take_profit_3:
        actions.append(
            ExitAction(position.remaining_fraction, f"TAKE_PROFIT_3 ({pnl:+.0f}%)", is_final=True)
        )
        return actions

    if not position.tp1_hit and pnl >= position.take_profit_1:
        actions.append(ExitAction(position.partial_sell_tp1_pct, f"TAKE_PROFIT_1 ({pnl:+.0f}%)"))
    if not position.tp2_hit and pnl >= position.take_profit_2:
        remaining_after_tp1 = position.remaining_fraction - sum(a.fraction for a in actions)
        actions.append(
            ExitAction(
                remaining_after_tp1 * position.partial_sell_tp2_pct,
                f"TAKE_PROFIT_2 ({pnl:+.0f}%)",
            )
        )
    return actions


def apply_exit(
    position: Position, action: ExitAction, price: float, cost_pct: float = 0.0
) -> Position:
    """Applique une sortie et retourne la nouvelle position (immuable).

    `cost_pct` : coût RÉEL mesuré (impact de prix + priority fee, voir
    `src/core/exit_fees.py`), en points de pourcentage, positif = coûte.
    0.0 par défaut = comportement d'origine (prix nu, aucun frais).
    Reste PUR — aucun I/O ici : le coût est mesuré par l'appelant et transmis,
    jamais recalculé depuis ce module.
    """
    fraction = min(action.fraction, position.remaining_fraction)
    pnl_pct = position.pnl_pct(price) - cost_pct
    realized = position.size_usd * fraction * pnl_pct / 100

    updates = {
        "remaining_fraction": max(0.0, position.remaining_fraction - fraction),
        "realized_pnl_usd": position.realized_pnl_usd + realized,
        "exit_reasons": position.exit_reasons + (action.reason,),
    }
    if action.reason.startswith("TAKE_PROFIT_1"):
        updates["tp1_hit"] = True
        updates["breakeven_moved"] = True
    elif action.reason.startswith("TAKE_PROFIT_2"):
        updates["tp2_hit"] = True
        updates["trailing_active"] = True
    return replace(position, **updates)


def update_water_marks(position: Position, price: float) -> Position:
    """Suit le plus haut ET le plus bas P&L atteints.

    Le plus haut sert de référence au trailing stop ; les deux servent à
    calibrer les take profits et le stop loss sur ce qui se produit
    réellement, au lieu de valeurs devinées.
    """
    pnl = position.pnl_pct(price)
    updates: dict = {}

    if pnl > position.high_water_pct:
        updates["high_water_pct"] = pnl
        updates["high_water_at"] = time.time()
        if not position.trailing_active and pnl >= position.trailing_stop_activation:
            updates["trailing_active"] = True
    if pnl < position.low_water_pct:
        updates["low_water_pct"] = pnl
        updates["low_water_at"] = time.time()

    return replace(position, **updates) if updates else position


# Ancien nom conservé : le trailing stop n'est qu'un des usages désormais.
update_high_water = update_water_marks
