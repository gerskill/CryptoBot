"""Journal des trades — étape 5 du workflow.

JSONL append-only : résistant aux crashs, directement importable dans
Airtable/Notion/Sheets. C'est ce fichier que lit `learning.py`.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from src.core.positions import Position


class TradeJournal:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def record_exit(
        self,
        position: Position,
        exit_price: float,
        pnl_pct: float,
        fraction: float,
        reason: str,
        is_final: bool,
    ) -> dict[str, Any]:
        row = {
            "id": f"{position.id}-{len(position.exit_reasons)}",
            "position_id": position.id,
            "timestamp_entry": datetime.fromtimestamp(
                position.entry_time, timezone.utc
            ).isoformat(),
            "timestamp_exit": datetime.now(timezone.utc).isoformat(),
            "token": position.symbol,
            "token_address": position.token_address,
            "chain": position.chain,
            "mode": position.mode,
            "entry_price": position.entry_price,
            "exit_price": exit_price,
            "position_size": round(position.size_usd * fraction, 4),
            "fraction_of_initial": round(fraction, 4),
            "is_final_exit": is_final,
            "pnl_pct": round(pnl_pct, 2),
            "pnl_usd": round(position.size_usd * fraction * pnl_pct / 100, 4),
            "duration_min": round(position.duration_minutes(), 1),
            "exit_reason": reason,
            "score_alpha": position.alpha_score,
            "liquidity_at_entry": position.liquidity_at_entry,
            "holders_at_entry": position.holders_at_entry,
            "volume_1h_at_entry": position.volume_1h_at_entry,
            "social_score": position.social_score,
            "rugcheck_score": position.rugcheck_score,
            "age_hours_at_entry": position.age_hours_at_entry,
            "params_version": position.params_version,
        }
        self._append(row)
        return row

    def _append(self, row: dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        """Ligne corrompue ignorée plutôt que de tout perdre."""
        if not os.path.exists(self.path):
            return []
        rows = []
        for line in self._iter_lines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def read_final_exits(self) -> list[dict[str, Any]]:
        """Un trade = une sortie finale. Les ventes partielles ne comptent pas double."""
        return [row for row in self.read_all() if row.get("is_final_exit")]

    def _iter_lines(self) -> Iterator[str]:
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield line

    def last_trade(self) -> Optional[dict[str, Any]]:
        rows = self.read_all()
        return rows[-1] if rows else None
