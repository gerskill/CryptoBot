"""Snapshot d'état du bot, lu par le dashboard.

Le bot et le dashboard sont volontairement DÉCOUPLÉS : le bot écrit un
fichier JSON à chaque cycle, l'API le lit. Si le dashboard plante ou n'est
pas lancé, le bot continue sans rien savoir.
"""

import json
import os
import tempfile
import time
from typing import Any, Optional


class StateWriter:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def write(self, payload: dict[str, Any]) -> None:
        payload = {**payload, "updated_at": time.time()}
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, default=str)
            os.replace(tmp_path, self.path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def read(self) -> Optional[dict[str, Any]]:
        if not os.path.exists(self.path):
            return None
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None


def candidate_to_dict(candidate: Any) -> dict[str, Any]:
    """Candidat -> JSON pour la colonne « The Hunt »."""
    return {
        "token_address": candidate.token_address,
        "symbol": candidate.symbol,
        "name": candidate.name,
        "chain": candidate.chain,
        "url": candidate.url,
        "price_usd": candidate.price_usd,
        "liquidity_usd": candidate.liquidity_usd,
        "market_cap": candidate.market_cap,
        "volume_1h": candidate.volume_1h,
        "age_hours": candidate.age_hours,
        "price_change_5m": candidate.price_change_5m,
        "price_change_1h": candidate.price_change_1h,
        "buys_5m": candidate.buys_5m,
        "sells_5m": candidate.sells_5m,
        "holders": candidate.holders,
        "holders_is_exact": candidate.holders_is_exact,
        "top_holder_pct": candidate.top_holder_pct,
        "top10_holder_pct": candidate.top10_holder_pct,
        "dev_wallet_pct": candidate.dev_wallet_pct,
        "rugcheck_score": candidate.rugcheck_score,
        "rugcheck_risks": list(candidate.rugcheck_risks),
        "lp_locked_pct": candidate.lp_locked_pct,
        "social_mentions_1h": candidate.social_mentions_1h,
        "social_unique_authors": candidate.social_unique_authors,
        "social_engagement": candidate.social_engagement,
        "social_velocity_15m": candidate.social_velocity_15m,
        # Aucune source publique : le dashboard affiche « aucune source »
        # plutôt qu'un zéro trompeur.
        "smart_money_buys_30m": candidate.smart_money_buys_30m,
        "alpha_score": candidate.alpha_score,
        "alpha_score_absolute": candidate.alpha_score_absolute,
        "sub_scores": candidate.sub_scores,
        "rejected_reason": candidate.rejected_reason,
    }


def position_to_dict(position: Any, price: Optional[float] = None) -> dict[str, Any]:
    """Position -> JSON pour la colonne « Active Positions »."""
    pnl = position.pnl_pct(price) if price else 0.0
    return {
        "id": position.id,
        "symbol": position.symbol,
        "token_address": position.token_address,
        "chain": position.chain,
        "pair_address": position.pair_address,
        "entry_price": position.entry_price,
        "current_price": price,
        "size_usd": position.size_usd,
        "pnl_pct": round(pnl, 2),
        "pnl_usd": round(position.size_usd * position.remaining_fraction * pnl / 100, 2),
        "multiple": round(1 + pnl / 100, 2),  # le trader lit des "x", pas des %
        "remaining_fraction": position.remaining_fraction,
        "duration_min": round(position.duration_minutes(), 1),
        "high_water_pct": position.high_water_pct,
        "alpha_score": position.alpha_score,
        "stop_loss_pct": position.effective_stop_loss_pct,
        "take_profit_1": position.take_profit_1,
        "take_profit_2": position.take_profit_2,
        "take_profit_3": position.take_profit_3,
        "max_hold_time_minutes": position.max_hold_time_minutes,
        "tp1_hit": position.tp1_hit,
        "tp2_hit": position.tp2_hit,
        "breakeven_moved": position.breakeven_moved,
        "trailing_active": position.trailing_active,
        "liquidity_at_entry": position.liquidity_at_entry,
        "holders_at_entry": position.holders_at_entry,
        "entry_time": position.entry_time,
    }
