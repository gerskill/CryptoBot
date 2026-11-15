"""Journal des trades — étape 5 du workflow.

JSONL append-only : résistant aux crashs, directement importable dans
Airtable/Notion/Sheets. C'est ce fichier que lit `learning.py`.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from src.core.positions import Position

# Correctif coût du 2026-10-24 (bot redémarré 10:59:35 Europe/Zurich).
# Avant cette date, `pnl_pct`/`pnl_usd` retranchaient un coût exprimé en %
# du notionnel REVENDU comme s'il était en % de la MISE (KAITE : -3 828 %).
# Les lignes antérieures sont corrigées À LA LECTURE (le fichier n'est jamais
# réécrit) pour que capital reconstruit, apprentissage et dashboard lisent
# le même PnL. Valeur d'origine conservée dans `pnl_pct_journal`/`pnl_usd_journal`.
COST_FIX_CUTOFF = "2026-10-24T08:59:00+00:00"


def correct_legacy_cost(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("cost_basis") == "stake" or "pnl_pct_journal" in row:
        return row
    cost = row.get("exit_cost_pct")
    entry, exit_ = row.get("entry_price"), row.get("exit_price")
    ts = str(row.get("timestamp_exit") or "")
    if not cost or not entry or not exit_ or entry <= 0 or not ts or ts >= COST_FIX_CUTOFF:
        return row
    pnl = row.get("pnl_pct") or 0.0
    corrected = pnl + cost - cost * (exit_ / entry)
    usd = row.get("pnl_usd") or 0.0
    if pnl:
        new_usd = usd * corrected / pnl
    else:
        new_usd = (row.get("position_size") or 0.0) * (row.get("fraction_of_initial") or 1.0) * corrected / 100
    row["pnl_pct_journal"], row["pnl_usd_journal"] = pnl, usd
    row["pnl_pct"], row["pnl_usd"] = round(corrected, 4), round(new_usd, 4)
    row["cost_basis"] = "stake_corrected_on_read"
    return row


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
        exit_cost_pct: Optional[float] = None,
        exit_cost_partial: Optional[bool] = None,
        exit_cost_estimated: bool = False,
        exit_cost_round_trip_pct: Optional[float] = None,
        entry_cost_version: str = "",
        entry_cost_pct: Optional[float] = None,
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
            "score_alpha_absolute": position.alpha_score_absolute,
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
            # Coût réel déduit à la sortie (impact de prix + priority fee),
            # voir `src/core/exit_fees.py`. `None` = non mesuré à ce moment,
            # distinct de « mesuré et nul » — même invariant que le reste.
            "exit_cost_pct": (
                round(exit_cost_pct, 3) if exit_cost_pct is not None else None
            ),
            "exit_cost_partial": exit_cost_partial,
            # MESURÉ vs ESTIMÉ. `True` = coût déduit de la médiane des
            # mesures passées (jambe partielle, voir ADR 012), pas d'un
            # devis Jupiter. Toute analyse qui recalibre un coût doit
            # exclure ces lignes, sinon l'estimation se confirme
            # elle-même. `PartialCostEstimator` les filtre déjà.
            "exit_cost_estimated": exit_cost_estimated,
            # Aller-retour brut quand la vente n'en déduit que la moitié
            # (buy-leg-v1). Les estimations de ventes partielles lisent CE
            # champ, pas la moitié. Absent = l'ancien modèle, tout sur la vente.
            "exit_cost_round_trip_pct": (
                round(exit_cost_round_trip_pct, 3)
                if exit_cost_round_trip_pct is not None else None
            ),
            "entry_cost_version": entry_cost_version or None,
            "entry_cost_pct": (
                round(entry_cost_pct, 3) if entry_cost_pct is not None else None
            ),
            "cost_basis": "stake",
        }
        # JOURNAL ENRICHI (observationnel) : contexte figé à l'entrée. Ne
        # remplace JAMAIS une colonne existante (setdefault).
        for key, value in (getattr(position, "entry_context", None) or {}).items():
            row.setdefault(key, value)
        if position.scale_in_trigger_pct > 0:
            # ENTRÉE PROGRESSIVE (bras `scaled_be` seulement : champs absents
            # des lignes des autres bras). `entry_price` = prix MOYEN après
            # ajout ; `position_size`/`pnl_usd` portent la taille réelle.
            row.update(
                {
                    "scale_in_planned": True,
                    "scale_in_trigger_pct": position.scale_in_trigger_pct,
                    "scaled_in": position.scaled_in,
                    "initial_entry_price": position.initial_entry_price,
                    "initial_size_usd": position.initial_size_usd,
                    "total_size_usd": position.size_usd,
                    "scale_in_price": position.scale_in_price,
                    "scale_in_at": (
                        datetime.fromtimestamp(position.scale_in_at, timezone.utc).isoformat()
                        if position.scale_in_at
                        else None
                    ),
                }
            )
        self._append(row)
        return row

    @property
    def events_path(self) -> str:
        """Événements hors sorties (SCALE_IN), à côté du journal.

        Fichier SÉPARÉ et pas une ligne de `trades_log` : `read_positions`
        additionne `position_size` de toutes les lignes d'une position, et
        tous les lecteurs (apprentissage, dashboard, estimateur de coût)
        prennent chaque ligne pour une jambe de sortie.
        """
        return os.path.join(os.path.dirname(os.path.abspath(self.path)), "scale_events.jsonl")

    def record_scale_in(
        self, before: Position, after: Position, price: float, add_usd: float,
        check: Any = None, observed_price: Optional[float] = None,
    ) -> dict[str, Any]:
        """`price` = prix EFFECTIF utilisé ; `observed_price` = prix du tick."""
        row = {
            "event": "SCALE_IN",
            "position_id": after.id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "timestamp_entry": datetime.fromtimestamp(after.entry_time, timezone.utc).isoformat(),
            "token": after.symbol,
            "token_address": after.token_address,
            "mode": after.mode,
            "trigger_pct": before.scale_in_trigger_pct,
            "max_pct": before.scale_in_max_pct,
            "pnl_pct_at_add": round(before.pnl_pct(price), 3),
            "observed_pnl_pct_at_add": round(
                before.pnl_pct(observed_price if observed_price else price), 3
            ),
            "initial_entry_price": before.entry_price,
            "observed_price": observed_price if observed_price else price,
            "scale_in_price": price,
            "price_source": "jupiter_quote" if check is not None else "observed",
            "buy_impact_pct": getattr(check, "buy_impact_pct", None),
            "round_trip_total_pct": getattr(check, "round_trip_total_pct", None),
            "avg_entry_price": after.entry_price,
            "size_before_usd": round(before.size_usd, 4),
            "add_usd": round(add_usd, 4),
            "size_after_usd": round(after.size_usd, 4),
            "minutes_after_entry": round(after.duration_minutes(), 2),
            "new_stop_pct_vs_avg": after.effective_stop_loss_pct,
            "stop_mode": "BREAKEVEN_EXACT",
        }
        self._append_event(row)
        return row

    def record_scale_skip(
        self, position: Position, reason: str, detail: str, price: float,
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        row = {
            "event": "SCALE_IN_SKIPPED",
            "reason": reason,
            "detail": detail,
            "position_id": position.id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "token": position.symbol,
            "token_address": position.token_address,
            "mode": position.mode,
            "trigger_pct": position.scale_in_trigger_pct,
            "max_pct": position.scale_in_max_pct,
            "initial_entry_price": position.entry_price,
            "observed_price": price,
            "observed_pnl_pct": round(position.pnl_pct(price), 3),
            "pending_usd": round(position.scale_in_pending_usd, 4),
            "minutes_after_entry": round(position.duration_minutes(), 2),
        }
        for key, value in (extra or {}).items():
            row.setdefault(key, value)
        self._append_event(row)
        return row

    def _append_event(self, row: dict[str, Any]) -> None:
        with open(self.events_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

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
                rows.append(correct_legacy_cost(json.loads(line)))
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

        Une position dont la jambe finale porte `excluded_from_learning` est
        ignorée ici aussi (incident du 2026-08-06 : seuil de stop loss effectif
        positif sur 4 bras, sorties en ~12s sans trajectoire de prix observée
        après l'entrée — aucune donnée pour reconstruire ce qui se serait
        « vraiment » passé, donc exclue plutôt qu'inventée). La ligne brute
        reste dans le fichier : `read_all()` la voit toujours, seule
        `read_positions()` — la source unique du capital, des stats et de
        l'apprentissage — l'écarte.
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
            if final.get("excluded_from_learning"):
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
