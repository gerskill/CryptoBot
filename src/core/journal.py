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
            # --- Instrumentation : calibrer TP et SL sur le réel ---
            # `peak_pct` répond à « le TP1 à +100% était-il seulement
            # atteignable ? ». `minutes_to_peak` dit quand sécuriser.
            "peak_pct": round(position.high_water_pct, 2),
            "minutes_to_peak": round(position.minutes_to_peak, 2)
            if position.minutes_to_peak is not None
            else None,
            "trough_pct": round(position.low_water_pct, 2),
            "minutes_to_trough": round(position.minutes_to_trough, 2)
            if position.minutes_to_trough is not None
            else None,
            # Le prix montait-il déjà fort à l'achat ? Teste l'hypothèse
            # « le score achète le haut de la bougie ».
            "price_change_5m_at_entry": position.price_change_5m_at_entry,
            "stop_loss_target_pct": position.stop_loss_pct,
            "stop_loss_trigger_pct": position.effective_stop_loss_pct,
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
        """Un trade = une sortie finale. Les ventes partielles ne comptent pas double.

        ATTENTION : correct pour COMPTER les trades, faux pour SOMMER le P&L.
        Un TP1 est une vente partielle, donc une ligne séparée sans
        `is_final_exit` : son gain est absent de ce qui est retourné ici.
        Pour toute mesure de performance, utiliser `read_positions()`.
        """
        return [row for row in self.read_all() if row.get("is_final_exit")]

    def read_positions(self) -> list[dict[str, Any]]:
        """Un trade = UNE POSITION, toutes ses jambes agrégées.

        Le bug que ça corrige : sur 40 lignes de journal réelles,
        `read_final_exits()` voyait 1 gagnant sur 36 pour -214,39 $, alors que
        4 positions étaient gagnantes pour -166,46 $. Les quatre avaient touché
        TP1 entre +100% et +117%, et ces 47,93 $ de profit partaient dans une
        ligne `is_final_exit: false` que tout le monde jetait — statistiques,
        apprentissage, backtest, dashboard, verrou du mode réel.

        Les jambes d'une même position partagent `position_id`. Le P&L en
        dollars s'additionne ; le P&L en pourcentage se repondère par la
        fraction vendue, sinon un TP1 à +100% sur la moitié de la position
        compterait comme +100% sur la totalité. Le contexte d'entrée et
        l'instrumentation sont identiques sur toutes les jambes : on prend
        ceux de la jambe finale.

        Une position sans jambe finale est encore ouverte : elle est ignorée.
        Une ligne sans `position_id` (journal ancien ou écrit à la main) ne
        peut être rattachée à rien : elle vaut une position à elle seule.
        Surtout pas de repli sur `id`, qui n'est unique que par jambe.
        """
        grouped: dict[Any, list[dict[str, Any]]] = {}
        order: list[Any] = []
        for index, row in enumerate(self.read_all()):
            key = row.get("position_id") or ("__orphelin__", index)
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(row)

        positions = []
        for key in order:
            legs = grouped[key]
            final = next((leg for leg in legs if leg.get("is_final_exit")), None)
            if final is None:
                continue

            pnl_usd = sum(leg.get("pnl_usd", 0) or 0 for leg in legs)
            weight = sum(leg.get("fraction_of_initial", 0) or 0 for leg in legs)
            pnl_pct = (
                sum(
                    (leg.get("pnl_pct", 0) or 0) * (leg.get("fraction_of_initial", 0) or 0)
                    for leg in legs
                )
                / weight
                if weight > 0
                else final.get("pnl_pct", 0)
            )

            position = dict(final)
            position.update(
                {
                    "legs": len(legs),
                    "pnl_usd": round(pnl_usd, 4),
                    "pnl_pct": round(pnl_pct, 2),
                    # Taille réellement engagée, pas celle de la dernière jambe.
                    "position_size": round(
                        sum(leg.get("position_size", 0) or 0 for leg in legs), 4
                    ),
                    "timestamp_exit": final.get("timestamp_exit"),
                    # La raison qui a fermé la position, plus le chemin complet.
                    "exit_reason": final.get("exit_reason"),
                    "exit_path": [leg.get("exit_reason") for leg in legs],
                    # P&L de la DERNIÈRE jambe seule. Indispensable pour
                    # mesurer le glissement d'un stop : le comparer au P&L
                    # pondéré de la position donnerait +49% contre un seuil de
                    # 0% sur un trade sorti en TP1 puis breakeven.
                    "final_leg_pnl_pct": final.get("pnl_pct"),
                }
            )
            positions.append(position)
        return positions

    def _iter_lines(self) -> Iterator[str]:
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield line

    def last_trade(self) -> Optional[dict[str, Any]]:
        rows = self.read_all()
        return rows[-1] if rows else None
