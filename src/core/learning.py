"""Auto-amélioration — étape 6 du workflow.

Recalcule les statistiques après chaque trade clôturé, découpe l'historique en
segments, puis ajuste les paramètres selon la cadence de la spec :
filtres tous les 5 trades, poids et sorties tous les 10, risque tous les 20.

GARDE-FOU CENTRAL : aucun ajustement sans `MIN_SEGMENT_SAMPLE` trades DANS LE
SEGMENT concerné. Sans ça le bot sur-réagit au bruit.
"""

from typing import Any, Optional

from src.core.journal import TradeJournal
from src.core.params import ParamsStore
from src.core.shadow import ShadowTracker

MIN_SEGMENT_SAMPLE = 10
FILTER_CADENCE = 5
WEIGHTS_CADENCE = 10
EXIT_CADENCE = 10
RISK_CADENCE = 20

# Un filtre dont plus de ce % des rejets aurait fait +100% coûte de l'argent.
MISSED_RATE_RELAX_THRESHOLD = 25.0
SHADOW_MIN_SAMPLE = 15

# Étape 6.4 : un ajustement de filtres doit prouver son gain sur l'historique.
BACKTEST_WINDOW = 20
BACKTEST_MIN_IMPROVEMENT = 0.10
BACKTEST_MIN_KEPT = 5

# BORNES DURES — sans elles, les règles d'ajustement ne font que resserrer et
# le bot converge vers "plus aucun candidat ne passe", définitivement.
PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "filters.min_age_hours": (0.0, 3.0),
    "filters.max_age_hours": (2.0, 24.0),
    "filters.min_liquidity_usd": (5_000, 100_000),
    "filters.min_holders": (25, 2_000),
    "filters.min_volume_1h": (2_000, 100_000),
    "filters.min_rugcheck_score": (50, 95),
    "filters.max_top_wallet_concentration": (10, 40),
    "filters.min_social_mentions_1h": (0, 200),
    "filters.min_smart_money_buys_30min": (0, 10),
    "risk_per_trade": (0.01, 0.05),
    "exit_rules.stop_loss_pct": (-50, -10),
    "exit_rules.take_profit_1": (30, 300),
    "exit_rules.trailing_stop_distance_pct": (20, 80),
    "exit_rules.max_hold_time_minutes": (30, 1440),
}

AGE_BUCKETS = [(0, 1, "0-1h"), (1, 2, "1-2h"), (2, 4, "2-4h"), (4, 999, "4h+")]
LIQ_BUCKETS = [
    (0, 10_000, "<10K"),
    (10_000, 30_000, "10-30K"),
    (30_000, 50_000, "30-50K"),
    (50_000, float("inf"), ">50K"),
]
SOCIAL_BUCKETS = [(0, 20, "faible"), (20, 50, "moyen"), (50, float("inf"), "fort")]


def _bucket(value: Optional[float], buckets: list) -> Optional[str]:
    if value is None:
        return None
    for low, high, label in buckets:
        if low <= value < high:
            return label
    return None


def _win_rate(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    return 100 * sum(1 for r in rows if r.get("pnl_usd", 0) > 0) / len(rows)


def segment_stats(rows: list[dict[str, Any]], key: str, buckets: list) -> dict[str, dict]:
    """Win rate et taille d'échantillon par segment."""
    grouped: dict[str, list] = {}
    for row in rows:
        label = _bucket(row.get(key), buckets)
        if label:
            grouped.setdefault(label, []).append(row)
    return {
        label: {"win_rate": round(_win_rate(items), 1), "sample": len(items)}
        for label, items in grouped.items()
    }


class LearningEngine:
    def __init__(
        self,
        params: ParamsStore,
        journal: TradeJournal,
        shadow: Optional[ShadowTracker] = None,
    ):
        self.params = params
        self.journal = journal
        self.shadow = shadow

    def _bounded_set(
        self, path: str, value: float, reason: str, sample: int
    ) -> Optional[str]:
        """Écrit un paramètre en respectant ses bornes. None si rien ne change."""
        low, high = PARAM_BOUNDS.get(path, (float("-inf"), float("inf")))
        clamped = max(low, min(high, value))
        current = self.params.get(path)

        if current is not None and abs(clamped - current) < 1e-9:
            if abs(value - clamped) > 1e-9:
                print(f"🧠 {path} déjà à sa borne ({clamped}) — ajustement ignoré")
            return None

        self.params.set(path, clamped, reason, sample)
        return f"{path} -> {clamped}"

    def refresh_stats(self) -> dict[str, Any]:
        """Étapes 6.1 et 6.2 : stats globales + analyse par segment."""
        rows = self.journal.read_final_exits()
        wins = [r for r in rows if r.get("pnl_usd", 0) > 0]
        losses = [r for r in rows if r.get("pnl_usd", 0) <= 0]

        learning = self.params.get("learning", {})
        learning.update(
            {
                "total_trades": len(rows),
                "winning_trades": len(wins),
                "losing_trades": len(losses),
                "avg_win_pct": round(sum(r["pnl_pct"] for r in wins) / len(wins), 2)
                if wins
                else 0,
                "avg_loss_pct": round(sum(r["pnl_pct"] for r in losses) / len(losses), 2)
                if losses
                else 0,
                "win_rate_by_age": segment_stats(rows, "age_hours_at_entry", AGE_BUCKETS),
                "win_rate_by_liquidity": segment_stats(rows, "liquidity_at_entry", LIQ_BUCKETS),
                "win_rate_by_social_score": segment_stats(rows, "social_score", SOCIAL_BUCKETS),
            }
        )
        self.params.set("learning", learning, log=False)
        return learning

    def run(self, max_drawdown_pct: float = 0.0) -> list[str]:
        """Point d'entrée après chaque trade clôturé. Retourne les changements."""
        learning = self.refresh_stats()
        total = learning["total_trades"]
        applied: list[str] = []

        if self._due("filters", total, FILTER_CADENCE):
            # Photo des filtres AVANT ajustement : le backtest doit pouvoir
            # revenir en arrière si le changement n'améliore rien.
            previous_filters = self.params.get("filters", {})
            filter_changes = self._adjust_filters(learning)
            if filter_changes:
                verdict = self.validate_filter_changes(previous_filters)
                if verdict and "annulés" in verdict:
                    print(f"🧠 BACKTEST | {verdict}")
                    filter_changes = [f"(annulé) {c}" for c in filter_changes]
                elif verdict:
                    print(f"🧠 BACKTEST | {verdict}")
            applied += filter_changes
        if self._due("weights", total, WEIGHTS_CADENCE):
            applied += self._adjust_weights(learning)
        if self._due("exits", total, EXIT_CADENCE):
            applied += self._adjust_exits(learning)
        if self._due("risk", total, RISK_CADENCE):
            applied += self._adjust_risk(learning, max_drawdown_pct)
        return applied

    def _due(self, key: str, total_trades: int, cadence: int) -> bool:
        """Un palier ne déclenche qu'une fois, même si `run` est rappelé."""
        if total_trades < cadence:
            return False
        last = self.params.get(f"learning.last_adjustment_at.{key}", 0) or 0
        if total_trades - last < cadence:
            return False
        self.params.set(f"learning.last_adjustment_at.{key}", total_trades, log=False)
        return True

    def _adjust_filters(self, learning: dict) -> list[str]:
        """Étape 6.3.A — resserre OU relâche les filtres, dans leurs bornes."""
        changes: list[str] = []
        by_age = learning.get("win_rate_by_age", {})
        by_liq = learning.get("win_rate_by_liquidity", {})

        young = by_age.get("0-1h")
        if young and young["sample"] >= MIN_SEGMENT_SAMPLE and young["win_rate"] < 30:
            change = self._bounded_set(
                "filters.min_age_hours",
                self.params.get("filters.min_age_hours", 0.5) + 0.5,
                f"WR {young['win_rate']}% sur tokens 0-1h",
                young["sample"],
            )
            if change:
                changes.append(change)

        old = by_age.get("4h+")
        if old and old["sample"] >= MIN_SEGMENT_SAMPLE and old["win_rate"] < 30:
            change = self._bounded_set(
                "filters.max_age_hours",
                self.params.get("filters.max_age_hours", 6) - 0.5,
                f"WR {old['win_rate']}% sur tokens 4h+",
                old["sample"],
            )
            if change:
                changes.append(change)

        low_liq = by_liq.get("10-30K")
        high_liq = by_liq.get(">50K")
        raise_liq_reason = None
        if low_liq and low_liq["sample"] >= MIN_SEGMENT_SAMPLE and low_liq["win_rate"] < 25:
            raise_liq_reason = f"WR {low_liq['win_rate']}% sous 30K"
        elif high_liq and high_liq["sample"] >= MIN_SEGMENT_SAMPLE and high_liq["win_rate"] > 60:
            raise_liq_reason = f"WR {high_liq['win_rate']}% au-dessus de 50K"
        if raise_liq_reason:
            change = self._bounded_set(
                "filters.min_liquidity_usd",
                self.params.get("filters.min_liquidity_usd", 15000) + 5000,
                raise_liq_reason,
                MIN_SEGMENT_SAMPLE,
            )
            if change:
                changes.append(change)

        changes += self._relax_from_shadow()
        return changes

    def _relax_from_shadow(self) -> list[str]:
        """Relâche un filtre dont les rejets partaient à +100%.

        Sans ce contre-poids, toutes les règles ci-dessus ne font que resserrer
        et le bot finit par ne plus rien acheter du tout.
        """
        if self.shadow is None:
            return []

        relaxations = {
            "liquidity": ("filters.min_liquidity_usd", -5000),
            "holders": ("filters.min_holders", -25),
            "social": ("filters.min_social_mentions_1h", -5),
            "smart_money": ("filters.min_smart_money_buys_30min", -1),
            "concentration": ("filters.max_top_wallet_concentration", +5),
        }

        changes = []
        for family, stats in self.shadow.missed_rate_by_family(SHADOW_MIN_SAMPLE).items():
            target = relaxations.get(family)
            if target is None or stats["missed_rate"] < MISSED_RATE_RELAX_THRESHOLD:
                continue
            path, step = target
            current = self.params.get(path)
            if current is None:
                continue
            change = self._bounded_set(
                path,
                current + step,
                f"{stats['missed_rate']}% des rejets '{family}' auraient fait +100% "
                f"(pic moyen {stats['avg_peak_gain_pct']}%)",
                stats["sample"],
            )
            if change:
                changes.append(change)
        return changes

    def _adjust_weights(self, learning: dict) -> list[str]:
        """Étape 6.3.B — renforce le critère du meilleur segment social."""
        by_social = learning.get("win_rate_by_social_score", {})
        strong = by_social.get("fort")
        weak = by_social.get("faible")
        weights = self.params.get("scoring_weights", {})
        if not weights:
            return []

        delta = 0.0
        reason = ""
        if strong and strong["sample"] >= MIN_SEGMENT_SAMPLE and strong["win_rate"] > 60:
            delta, reason = 0.05, f"WR {strong['win_rate']}% quand le social est fort"
        elif weak and weak["sample"] >= MIN_SEGMENT_SAMPLE and weak["win_rate"] < 30:
            delta, reason = -0.05, f"WR {weak['win_rate']}% quand le social est faible"
        if delta == 0:
            return []

        current = weights.get("social_sentiment", 0.20)
        target = max(0.05, min(0.40, current + delta))
        if abs(target - current) < 1e-9:
            return []

        others = {k: v for k, v in weights.items() if k != "social_sentiment"}
        total_others = sum(others.values())
        shift = target - current
        new_weights = {"social_sentiment": round(target, 3)}
        for key, value in others.items():
            new_weights[key] = round(max(0.05, value - shift * value / total_others), 3)

        self.params.set("scoring_weights", new_weights, reason, MIN_SEGMENT_SAMPLE)
        return [f"scoring_weights.social_sentiment -> {target:.2f}"]

    def _adjust_exits(self, learning: dict) -> list[str]:
        """Étape 6.3.C — recalibre stop loss et take profit sur le vécu."""
        changes = []
        rows = self.journal.read_final_exits()
        stop_loss = self.params.get("exit_rules.stop_loss_pct", -25)
        avg_loss = learning.get("avg_loss_pct", 0)

        # Pertes bien plus grandes que le SL -> le SL n'est pas atteignable
        # (gap ou rug) : le resserrer pour sortir plus tôt.
        if avg_loss and avg_loss < stop_loss - 10:
            change = self._bounded_set(
                "exit_rules.stop_loss_pct",
                round(stop_loss + 5, 1),
                f"perte moyenne {avg_loss}% bien au-delà du SL {stop_loss}%",
                learning.get("losing_trades", 0),
            )
            if change:
                changes.append(change)

        time_stops = [r for r in rows if str(r.get("exit_reason", "")).startswith("TIME_STOP")]
        winning_time_stops = [r for r in time_stops if r.get("pnl_pct", 0) > 30]
        if len(winning_time_stops) >= MIN_SEGMENT_SAMPLE:
            change = self._bounded_set(
                "exit_rules.max_hold_time_minutes",
                self.params.get("exit_rules.max_hold_time_minutes", 240) + 30,
                f"{len(winning_time_stops)} trades gagnants coupés par le time stop",
                len(winning_time_stops),
            )
            if change:
                changes.append(change)

        trailing = [r for r in rows if str(r.get("exit_reason", "")).startswith("TRAILING")]
        if len(trailing) >= MIN_SEGMENT_SAMPLE and _win_rate(trailing) > 80:
            change = self._bounded_set(
                "exit_rules.trailing_stop_distance_pct",
                self.params.get("exit_rules.trailing_stop_distance_pct", 50) + 10,
                f"{len(trailing)} sorties trailing quasi toutes gagnantes : trop serré",
                len(trailing),
            )
            if change:
                changes.append(change)

        return changes

    def _adjust_risk(self, learning: dict, max_drawdown_pct: float = 0.0) -> list[str]:
        """Étape 6.3.D — ajuste le risque par trade, jamais hors des bornes."""
        rows = self.journal.read_final_exits()
        if len(rows) < RISK_CADENCE:
            return []

        wins = [r for r in rows if r.get("pnl_usd", 0) > 0]
        gross_win = sum(r["pnl_usd"] for r in wins)
        gross_loss = abs(sum(r["pnl_usd"] for r in rows if r.get("pnl_usd", 0) <= 0))
        win_rate = 100 * len(wins) / len(rows)
        profit_factor = gross_win / gross_loss if gross_loss > 0 else 0.0
        current = self.params.get("risk_per_trade", 0.03)

        if win_rate > 50 and profit_factor > 2.0:
            new = round(current + 0.005, 4)
            reason = f"WR {win_rate:.0f}% et profit factor {profit_factor:.2f}"
        elif win_rate < 35 or max_drawdown_pct > 20:
            new = round(current - 0.005, 4)
            reason = f"WR {win_rate:.0f}%, drawdown {max_drawdown_pct:.0f}%"
        else:
            return []

        change = self._bounded_set("risk_per_trade", new, reason, len(rows))
        return [change] if change else []

    def simulate_filters(
        self, rows: list[dict[str, Any]], filters: dict[str, Any]
    ) -> dict[str, Any]:
        """Rejoue les trades passés sous un jeu de filtres donné.

        Ne rejoue QUE les filtres d'entrée : le journal conserve la liquidité,
        les holders, l'âge et le social au moment de l'entrée. Les règles de
        SORTIE ne sont pas rejouables — il faudrait la trajectoire de prix de
        chaque position, que le journal ne stocke pas.
        """
        kept = []
        for row in rows:
            liquidity = row.get("liquidity_at_entry")
            if liquidity is not None and liquidity < filters.get("min_liquidity_usd", 0):
                continue
            holders = row.get("holders_at_entry")
            if holders is not None and holders < filters.get("min_holders", 0):
                continue
            age = row.get("age_hours_at_entry")
            if age is not None and not (
                filters.get("min_age_hours", 0) <= age <= filters.get("max_age_hours", 999)
            ):
                continue
            social = row.get("social_score")
            if social is not None and social < filters.get("min_social_mentions_1h", 0):
                continue
            rugcheck = row.get("rugcheck_score")
            if rugcheck is not None and rugcheck < filters.get("min_rugcheck_score", 0):
                continue
            kept.append(row)

        total_pnl = sum(r.get("pnl_usd", 0) for r in kept)
        return {
            "trades": len(kept),
            "total_pnl_usd": round(total_pnl, 4),
            "pnl_per_trade": round(total_pnl / len(kept), 4) if kept else 0.0,
            "win_rate": round(_win_rate(kept), 1),
        }

    def validate_filter_changes(self, previous_filters: dict[str, Any]) -> Optional[str]:
        """Étape 6.4 — annule un ajustement de filtres qui n'améliore rien."""
        rows = self.journal.read_final_exits()[-BACKTEST_WINDOW:]
        if len(rows) < BACKTEST_WINDOW:
            return None  # échantillon trop court pour trancher

        before = self.simulate_filters(rows, previous_filters)
        after = self.simulate_filters(rows, self.params.get("filters", {}))

        if after["trades"] < BACKTEST_MIN_KEPT:
            self.params.set(
                "filters", previous_filters,
                f"backtest : {after['trades']} trades restants sur {len(rows)}, "
                f"filtres trop restrictifs — annulé",
                len(rows),
            )
            return "filtres annulés (trop restrictifs)"

        gain = after["pnl_per_trade"] - before["pnl_per_trade"]
        threshold = abs(before["pnl_per_trade"]) * BACKTEST_MIN_IMPROVEMENT
        if gain < threshold:
            self.params.set(
                "filters", previous_filters,
                f"backtest : P&L/trade {before['pnl_per_trade']} → {after['pnl_per_trade']}, "
                f"pas d'amélioration ≥ {BACKTEST_MIN_IMPROVEMENT:.0%} — annulé",
                len(rows),
            )
            return "filtres annulés (backtest non concluant)"

        return (
            f"filtres validés (P&L/trade {before['pnl_per_trade']} → {after['pnl_per_trade']})"
        )

    def live_mode_allowed(self) -> tuple[bool, str]:
        """Règle 10 de la spec : 20 trades, WR > 40% ET profit factor > 1.5."""
        rows = self.journal.read_final_exits()
        if len(rows) < 20:
            return False, f"{len(rows)}/20 trades papier"

        wins = [r for r in rows if r.get("pnl_usd", 0) > 0]
        win_rate = 100 * len(wins) / len(rows)
        gross_win = sum(r["pnl_usd"] for r in wins)
        gross_loss = abs(sum(r["pnl_usd"] for r in rows if r.get("pnl_usd", 0) <= 0))
        profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

        if win_rate <= 40:
            return False, f"win rate {win_rate:.0f}% ≤ 40%"
        if profit_factor <= 1.5:
            return False, f"profit factor {profit_factor:.2f} ≤ 1.5"
        return True, f"WR {win_rate:.0f}%, PF {profit_factor:.2f} sur {len(rows)} trades"
