"""Portefeuille papier : ouverture, suivi et clôture des positions.

Applique les règles de sécurité non négociables de la spec :
  - jamais plus de `max_concurrent_positions` positions
  - taille jamais > 5% du capital, quoi que dise le Kelly
  - pause forcée de 2h après 3 pertes consécutives
  - un token déjà en position ne peut pas être racheté

Mode PAPER uniquement : aucun ordre n'est transmis nulle part. Les prix de
sortie sont les prix observés, sans slippage ni frais — le P&L papier est donc
OPTIMISTE par construction (voir SLIPPAGE_NOTE).
"""

import time
from typing import Any, Optional

from src.core.journal import TradeJournal
from src.core.models import Candidate
from src.core.positions import (
    ExitAction,
    Position,
    apply_exit,
    evaluate_exits,
    update_high_water,
)

MAX_POSITION_PCT_OF_CAPITAL = 0.05
CONSECUTIVE_LOSSES_TRIGGER = 3
COOLDOWN_HOURS = 2
KELLY_MIN_TRADES = 20
KELLY_WIN_RATE_FLOOR = 0.40

SLIPPAGE_NOTE = (
    "P&L papier sans slippage ni frais. En réel, un memecoin à 20K de liquidité "
    "coûte plusieurs % à l'aller comme au retour."
)


class PaperPortfolio:
    def __init__(
        self,
        capital: float,
        journal: TradeJournal,
        notifier: Any = None,
        mode: str = "PAPER",
    ):
        self.initial_capital = capital
        self.capital = capital
        self.journal = journal
        self.notifier = notifier
        self.mode = mode
        self.positions: dict[str, Position] = {}
        self.closed_count = 0
        self.consecutive_losses = 0
        self.cooldown_until: Optional[float] = None
        self.peak_capital = capital
        self.max_drawdown_pct = 0.0

    # -------------------------------------------------------------- garde-fous

    @property
    def in_cooldown(self) -> bool:
        return self.cooldown_until is not None and time.time() < self.cooldown_until

    def cooldown_remaining_min(self) -> float:
        if not self.in_cooldown:
            return 0.0
        return (self.cooldown_until - time.time()) / 60

    def can_open(self, candidate: Candidate, max_positions: int) -> Optional[str]:
        """None = ouverture autorisée. Sinon, la raison du refus."""
        if self.in_cooldown:
            return f"cooldown actif ({self.cooldown_remaining_min():.0f} min restantes)"
        if len(self.positions) >= max_positions:
            return f"{len(self.positions)}/{max_positions} positions déjà ouvertes"
        if any(p.token_address == candidate.token_address for p in self.positions.values()):
            return "position déjà ouverte sur ce token"
        if self.capital <= 0:
            return "capital épuisé"
        return None

    # ---------------------------------------------------------------- sizing

    def position_size(self, risk_per_trade: float, win_rate: float, total_trades: int) -> float:
        """Kelly ajusté, plafonné à 5% du capital.

        Le facteur Kelly ne s'applique qu'après `KELLY_MIN_TRADES` trades : sur
        un petit échantillon, un win rate de 60% sur 5 trades n'est pas un edge.
        """
        base = self.capital * risk_per_trade
        if total_trades >= KELLY_MIN_TRADES and win_rate > KELLY_WIN_RATE_FLOOR:
            base *= 1 + (win_rate - KELLY_WIN_RATE_FLOOR)
        return round(min(base, self.capital * MAX_POSITION_PCT_OF_CAPITAL), 4)

    # ------------------------------------------------------------- ouverture

    def open(self, candidate: Candidate, params: dict[str, Any], size_usd: float) -> Position:
        exits = params.get("exit_rules", {})
        position = Position(
            token_address=candidate.token_address,
            symbol=candidate.symbol,
            chain=candidate.chain,
            pair_address=candidate.pair_address,
            entry_price=candidate.price_usd,
            size_usd=size_usd,
            mode=self.mode,
            alpha_score=candidate.alpha_score,
            liquidity_at_entry=candidate.liquidity_usd,
            holders_at_entry=candidate.holders,
            volume_1h_at_entry=candidate.volume_1h,
            rugcheck_score=candidate.rugcheck_score,
            social_score=candidate.social_mentions_1h,
            age_hours_at_entry=candidate.age_hours,
            params_version=str(params.get("version", "")),
            stop_loss_pct=exits.get("stop_loss_pct", -25),
            take_profit_1=exits.get("take_profit_1", 100),
            take_profit_2=exits.get("take_profit_2", 300),
            take_profit_3=exits.get("take_profit_3", 500),
            partial_sell_tp1_pct=exits.get("partial_sell_tp1_pct", 0.5),
            partial_sell_tp2_pct=exits.get("partial_sell_tp2_pct", 0.5),
            trailing_stop_activation=exits.get("trailing_stop_activation", 200),
            trailing_stop_distance_pct=exits.get("trailing_stop_distance_pct", 50),
            max_hold_time_minutes=exits.get("max_hold_time_minutes", 240),
        )
        self.positions[position.id] = position
        self.capital -= size_usd
        if self.notifier:
            self.notifier.send_entry(position)
        return position

    # -------------------------------------------------------------- suivi

    def update(
        self, position_id: str, price: float, liquidity_drop_pct: Optional[float] = None
    ) -> list[dict[str, Any]]:
        """Applique les règles de sortie. Retourne les lignes de journal créées."""
        position = self.positions.get(position_id)
        if position is None or price <= 0:
            return []

        position = update_high_water(position, price)
        self.positions[position_id] = position

        rows = []
        for action in evaluate_exits(position, price, liquidity_drop_pct):
            fraction = min(action.fraction, position.remaining_fraction)
            if fraction <= 1e-9:
                continue
            pnl_pct = position.pnl_pct(price)
            position = apply_exit(position, action, price)
            self.positions[position_id] = position

            # Le produit de la vente revient au capital
            self.capital += position.size_usd * fraction * (1 + pnl_pct / 100)

            rows.append(
                self.journal.record_exit(
                    position=position,
                    exit_price=price,
                    pnl_pct=pnl_pct,
                    fraction=fraction,
                    reason=action.reason,
                    is_final=not position.is_open,
                )
            )
            if not position.is_open:
                self._finalize(position, price)
                break
        return rows

    def force_close(
        self, position_id: str, price: float, reason: str = "MANUAL_STOP"
    ) -> Optional[dict[str, Any]]:
        """Sortie totale immédiate, hors règles — utilisée par le panic button."""
        position = self.positions.get(position_id)
        if position is None or price <= 0:
            return None

        fraction = position.remaining_fraction
        pnl_pct = position.pnl_pct(price)
        position = apply_exit(position, ExitAction(fraction, reason, is_final=True), price)
        self.positions[position_id] = position
        self.capital += position.size_usd * fraction * (1 + pnl_pct / 100)

        row = self.journal.record_exit(
            position=position,
            exit_price=price,
            pnl_pct=pnl_pct,
            fraction=fraction,
            reason=reason,
            is_final=True,
        )
        self._finalize(position, price)
        return row

    def _finalize(self, position: Position, exit_price: float) -> None:
        """Clôture définitive : stats, cooldown, notification."""
        del self.positions[position.id]
        self.closed_count += 1

        total_pnl_pct = 100 * position.realized_pnl_usd / position.size_usd
        if position.realized_pnl_usd < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= CONSECUTIVE_LOSSES_TRIGGER:
                self.cooldown_until = time.time() + COOLDOWN_HOURS * 3600
                print(
                    f"⛔ COOLDOWN {COOLDOWN_HOURS}h — "
                    f"{self.consecutive_losses} pertes consécutives"
                )
                if self.notifier:
                    self.notifier.send(
                        f"⛔ <b>COOLDOWN {COOLDOWN_HOURS}h</b>\n"
                        f"{self.consecutive_losses} pertes consécutives — "
                        f"aucune nouvelle entrée."
                    )
        else:
            self.consecutive_losses = 0

        equity = self.equity()
        self.peak_capital = max(self.peak_capital, equity)
        if self.peak_capital > 0:
            drawdown = 100 * (self.peak_capital - equity) / self.peak_capital
            self.max_drawdown_pct = max(self.max_drawdown_pct, drawdown)

        if self.notifier:
            self.notifier.send_exit(
                position,
                total_pnl_pct,
                " + ".join(position.exit_reasons),
                position.duration_minutes(),
            )

    # ---------------------------------------------------------------- stats

    def equity(self) -> float:
        """Capital libre + valeur d'entrée des positions ouvertes (approximation)."""
        engaged = sum(p.size_usd * p.remaining_fraction for p in self.positions.values())
        return self.capital + engaged

    def stats(self, journal_rows: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        rows = journal_rows if journal_rows is not None else self.journal.read_final_exits()
        wins = [r for r in rows if r.get("pnl_usd", 0) > 0]
        losses = [r for r in rows if r.get("pnl_usd", 0) <= 0]
        gross_win = sum(r.get("pnl_usd", 0) for r in wins)
        gross_loss = abs(sum(r.get("pnl_usd", 0) for r in losses))

        return {
            "mode": self.mode,
            "capital": round(self.capital, 2),
            "equity": round(self.equity(), 2),
            "total_pnl_usd": round(self.equity() - self.initial_capital, 2),
            "open_positions": len(self.positions),
            "total_trades": len(rows),
            "win_rate": round(100 * len(wins) / len(rows), 1) if rows else 0.0,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else 0.0,
            "avg_win_pct": round(sum(r["pnl_pct"] for r in wins) / len(wins), 1) if wins else 0.0,
            "avg_loss_pct": round(sum(r["pnl_pct"] for r in losses) / len(losses), 1)
            if losses
            else 0.0,
            "max_drawdown_pct": round(self.max_drawdown_pct, 1),
            "consecutive_losses": self.consecutive_losses,
            "cooldown_min": round(self.cooldown_remaining_min(), 0),
        }
