"""Portefeuille papier : ouverture, suivi et clôture des positions.

Applique les règles de sécurité non négociables de la spec :
  - jamais plus de `max_concurrent_positions` positions
  - taille jamais > 5% du capital, quoi que dise le Kelly
  - pause forcée de 2h après 3 pertes consécutives
  - un token déjà en position ne peut pas être racheté

Mode PAPER : les prix de sortie sont les prix observés. Le P&L de la sortie
FINALE est corrigé du coût réel mesuré (impact de prix + priority fee) quand
`exit_fee_measurer` est fourni — voir `src/core/exit_fees.py`. Sans lui
(tests, ou clients Jupiter/Helius désactivés), il reste optimiste par
construction, exactement comme avant.
"""

import json
import os
import tempfile
import time
from dataclasses import asdict, fields
from datetime import datetime
from typing import Any, Callable, Optional

from src.core.journal import TradeJournal
from src.core.models import Candidate
from src.core.positions import (
    ExitAction,
    Position,
    apply_exit,
    evaluate_exits,
    update_water_marks,
)

MAX_POSITION_PCT_OF_CAPITAL = 0.05
CONSECUTIVE_LOSSES_TRIGGER = 3
COOLDOWN_HOURS = 2
KELLY_MIN_TRADES = 20
KELLY_WIN_RATE_FLOOR = 0.40

# Champs de Position typés `tuple` : JSON les rend en liste, il faut les
# reconvertir à la restauration (voir `_load_positions`).
TUPLE_FIELDS = ("exit_reasons",)

def _parse_iso(value: Any) -> Optional[float]:
    """Horodatage ISO -> epoch. None si absent ou illisible."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return None


SLIPPAGE_NOTE = (
    "P&L papier sans slippage ni frais QUAND `exit_fee_measurer` est absent "
    "(tests, ou clients Jupiter/Helius désactivés). Avec lui, la sortie "
    "finale déduit le coût réel mesuré — voir src/core/exit_fees.py."
)


class PaperPortfolio:
    def __init__(
        self,
        capital: float,
        journal: TradeJournal,
        notifier: Any = None,
        mode: str = "PAPER",
        positions_path: Optional[str] = None,
        cooldown_hours: float = COOLDOWN_HOURS,
        losses_trigger: int = CONSECUTIVE_LOSSES_TRIGGER,
        max_drawdown_stop_pct: float = 0.0,
        exit_fee_measurer: Optional[Callable[[str, float], Optional[Any]]] = None,
    ):
        # COUPE-CIRCUIT. Le cooldown réagit à 3 pertes CONSÉCUTIVES ; il ne
        # voit rien d'une érosion lente qui alterne gains et pertes. Sur ce
        # journal, le drawdown a atteint 17,2 % sans jamais déclencher trois
        # pertes d'affilée. Un plafond de drawdown est le seul garde-fou qui
        # regarde la trajectoire plutôt que la séquence. 0 = désactivé.
        self.max_drawdown_stop_pct = max_drawdown_stop_pct
        # `cooldown_hours = 0` désactive la pause. Voulu en PAPER : une pause
        # de 2h après 3 pertes bloque la collecte des 20 trades de validation.
        self.cooldown_hours = cooldown_hours
        self.losses_trigger = losses_trigger
        self.journal = journal
        self.notifier = notifier
        self.mode = mode
        # Coût réel à la sortie FINALE, mesuré en direct (`(token_address,
        # size_usd) -> ExitCost | None`). Callable injecté, pas de clients
        # I/O tenus ici : `portfolio.py` reste testable sans réseau, comme le
        # reste du dépôt. `None` = comportement d'origine (prix nu).
        self.exit_fee_measurer = exit_fee_measurer
        self.positions_path = positions_path
        self.positions: dict[str, Position] = {}
        # Écriture différée : `update()` sauvegardait à CHAQUE tick, même sans
        # rien à faire. 3 positions x 12 ticks/min = 36 écritures atomiques
        # par minute, x7 stratégies = 252. Une SORTIE reste écrite tout de
        # suite (durabilité) ; le reste attend `flush()`, une fois par tick.
        self.dirty = False
        self.closed_count = 0
        self.consecutive_losses = 0
        self.cooldown_until: Optional[float] = None
        self.max_drawdown_pct = 0.0  # recalculé depuis le journal ci-dessous

        # `baseline` = la mise de départ configurée, jamais recalculée. Elle
        # sert de référence au P&L cumulé.
        self.baseline = capital

        # Le capital est RECONSTRUIT depuis le journal : sans ça, chaque
        # redémarrage repart de la mise initiale et efface l'historique.
        realized = self._realized_pnl_from_journal()
        # Les positions ouvertes sont RESTAURÉES : sans ça, un redémarrage les
        # perd définitivement — elles ne sont jamais clôturées, jamais
        # journalisées, et le trade disparaît sans laisser de trace.
        self.positions = self._load_positions()
        engaged = sum(p.size_usd * p.remaining_fraction for p in self.positions.values())

        self.capital = capital + realized - engaged
        self.peak_capital = max(capital, self.capital + engaged)

        if realized or self.positions:
            print(
                f"[Portfolio] repris : {self.closed_count} trades clôturés "
                f"({realized:+.2f} $), {len(self.positions)} position(s) ouverte(s) "
                f"({engaged:.2f} $ engagés) — capital {self.capital:.2f} $"
            )

    def _realized_pnl_from_journal(self) -> float:
        """P&L cumulé, pertes consécutives et drawdown max, reconstruits.

        Le drawdown doit lui aussi être rejoué : calculé uniquement à la
        clôture d'un trade, il repartait à 0% après chaque redémarrage et le
        dashboard affichait « drawdown max 0% » sur un compte en perte.

        Agrégé PAR POSITION (`read_positions`) et pas par sortie finale : une
        position sortie en deux fois voyait son gain de TP1 disparaître du
        capital reconstruit, et une position gagnante grâce à son TP1 était
        comptée comme une perte consécutive de plus.
        """
        rows = self.journal.read_positions()
        self.closed_count = len(rows)

        streak = 0
        for row in reversed(rows):
            if row.get("pnl_usd", 0) < 0:
                streak += 1
            else:
                break
        self.consecutive_losses = streak

        # Le cooldown doit survivre au redémarrage, sinon relancer le bot
        # devient un moyen trivial de contourner la pause après 3 pertes.
        if self.cooldown_hours > 0 and streak >= self.losses_trigger and rows:
            last_exit = _parse_iso(rows[-1].get("timestamp_exit"))
            if last_exit is not None:
                until = last_exit + self.cooldown_hours * 3600
                if until > time.time():
                    self.cooldown_until = until
                    print(
                        f"[Portfolio] cooldown repris — {streak} pertes consécutives, "
                        f"{(until - time.time()) / 60:.0f} min restantes"
                    )

        # Courbe d'equity chronologique -> plus fort recul depuis un sommet.
        equity = self.baseline
        peak = self.baseline
        worst = 0.0
        for row in rows:
            equity += row.get("pnl_usd", 0)
            peak = max(peak, equity)
            if peak > 0:
                worst = max(worst, 100 * (peak - equity) / peak)
        self.max_drawdown_pct = round(worst, 2)

        return round(equity - self.baseline, 4)

    # ------------------------------------------------- persistance positions

    def _load_positions(self) -> dict[str, Position]:
        """Restaure les positions ouvertes. Un fichier illisible n'est pas fatal."""
        if not self.positions_path or not os.path.exists(self.positions_path):
            return {}
        try:
            with open(self.positions_path, encoding="utf-8") as fh:
                rows = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[Portfolio] positions illisibles ({exc}) — repart sans position")
            return {}

        restored = {}
        known = {f.name for f in fields(Position)}
        for row in rows:
            try:
                # Ignorer les champs inconnus : le format peut évoluer sans
                # rendre un fichier existant incompatible.
                clean = {k: v for k, v in row.items() if k in known}
                # JSON n'a pas de tuple : il sérialise en liste. Sans cette
                # reconversion, `exit_reasons + (raison,)` lève un TypeError à
                # la première sortie — la position restaurée devenait
                # IMPOSSIBLE à fermer et saignait indéfiniment.
                for name in TUPLE_FIELDS:
                    if isinstance(clean.get(name), list):
                        clean[name] = tuple(clean[name])
                position = Position(**clean)
                restored[position.id] = position
            except (TypeError, ValueError) as exc:
                print(f"[Portfolio] position ignorée ({exc})")
        return restored

    def save_positions(self) -> None:
        """Écriture atomique après chaque ouverture ou sortie."""
        if not self.positions_path:
            return
        directory = os.path.dirname(os.path.abspath(self.positions_path))
        os.makedirs(directory, exist_ok=True)
        payload = [asdict(p) for p in self.positions.values()]
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp_path, self.positions_path)
            self.dirty = False
        except Exception as exc:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            print(f"[Portfolio] sauvegarde des positions impossible : {exc}")

    @property
    def in_cooldown(self) -> bool:
        return self.cooldown_until is not None and time.time() < self.cooldown_until

    def cooldown_remaining_min(self) -> float:
        if not self.in_cooldown:
            return 0.0
        return (self.cooldown_until - time.time()) / 60

    @property
    def drawdown_breached(self) -> bool:
        return (
            self.max_drawdown_stop_pct > 0
            and self.max_drawdown_pct >= self.max_drawdown_stop_pct
        )

    def can_open(self, candidate: Candidate, max_positions: int) -> Optional[str]:
        """None = ouverture autorisée. Sinon, la raison du refus."""
        # Vérifié EN PREMIER : c'est un arrêt, pas une pause. Contrairement au
        # cooldown il ne s'efface pas avec le temps — il faut décider
        # explicitement de relever le plafond ou de repartir.
        if self.drawdown_breached:
            return (
                f"COUPE-CIRCUIT — drawdown {self.max_drawdown_pct:.1f}% ≥ "
                f"{self.max_drawdown_stop_pct:.0f}%, plus aucune entrée"
            )
        if self.in_cooldown:
            return f"cooldown actif ({self.cooldown_remaining_min():.0f} min restantes)"
        if len(self.positions) >= max_positions:
            return f"{len(self.positions)}/{max_positions} positions déjà ouvertes"
        if any(p.token_address == candidate.token_address for p in self.positions.values()):
            return "position déjà ouverte sur ce token"
        if self.capital <= 0:
            return "capital épuisé"
        return None

    def position_size(self, risk_per_trade: float, win_rate: float, total_trades: int) -> float:
        """Kelly ajusté, plafonné à 5% du capital.

        Le facteur Kelly ne s'applique qu'après `KELLY_MIN_TRADES` trades : sur
        un petit échantillon, un win rate de 60% sur 5 trades n'est pas un edge.
        """
        base = self.capital * risk_per_trade
        if total_trades >= KELLY_MIN_TRADES and win_rate > KELLY_WIN_RATE_FLOOR:
            base *= 1 + (win_rate - KELLY_WIN_RATE_FLOOR)
        return round(min(base, self.capital * MAX_POSITION_PCT_OF_CAPITAL), 4)

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
            alpha_score_absolute=candidate.alpha_score_absolute,
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
            stop_loss_slippage_buffer_pct=exits.get("stop_loss_slippage_buffer_pct", 0.0),
            price_change_5m_at_entry=candidate.price_change_5m,
        )
        self.positions[position.id] = position
        self.capital -= size_usd
        self.save_positions()
        if self.notifier:
            self.notifier.send_entry(position)
        return position

    def update(
        self, position_id: str, price: float, liquidity_drop_pct: Optional[float] = None
    ) -> list[dict[str, Any]]:
        """Applique les règles de sortie. Retourne les lignes de journal créées."""
        position = self.positions.get(position_id)
        if position is None or price <= 0:
            return []

        position = update_water_marks(position, price)
        self.positions[position_id] = position

        rows = []
        for action in evaluate_exits(position, price, liquidity_drop_pct):
            fraction = min(action.fraction, position.remaining_fraction)
            if fraction <= 1e-9:
                continue

            # COÛT RÉEL, MESURÉ UNE SEULE FOIS PAR POSITION, SUR LA JAMBE
            # FINALE. `action.is_final` implique `fraction == remaining_fraction`
            # (voir `evaluate_exits`) : c'est le seul point où « ce qui sort »
            # coïncide avec « tout ce qui reste », donc le seul où mesurer
            # coûte un devis unique plutôt qu'un par jambe partielle (TP1,
            # TP2). Les jambes partielles restent au prix nu, comme avant.
            exit_cost = None
            cost_pct = 0.0
            if action.is_final and self.exit_fee_measurer is not None:
                # Notionnel COURANT, pas `size_usd` (figé à l'entrée) : sur une
                # position qui a fait x3, vendre `fraction` des tokens vaut
                # `fraction * (price / entry_price)` fois la mise initiale, pas
                # `fraction` fois. Coder l'ancien montant sous-dimensionnait le
                # devis d'impact de prix sur tout gagnant ayant déjà pris un TP
                # partiel — trouvé en revue adversariale Codex.
                exit_notional = (
                    position.size_usd * fraction * (price / position.entry_price)
                    if position.entry_price > 0
                    else position.size_usd * fraction
                )
                try:
                    exit_cost = self.exit_fee_measurer(position.token_address, exit_notional)
                except Exception:  # noqa: BLE001
                    # Une mesure de coût ne doit jamais empêcher une clôture
                    # réelle. Invariant « la boucle ne meurt jamais ».
                    exit_cost = None
                if exit_cost is not None:
                    cost_pct = exit_cost.total_cost_pct

            pnl_pct = position.pnl_pct(price) - cost_pct
            position = apply_exit(position, action, price, cost_pct=cost_pct)
            self.positions[position_id] = position

            self.capital += position.size_usd * fraction * (1 + pnl_pct / 100)

            # Une écriture journal ratée ne doit JAMAIS empêcher la clôture
            # réelle : sinon la position reste à `remaining_fraction == 0`
            # dans `self.positions` pour toujours — invisible aux règles de
            # sortie (`is_open` les court-circuite dès l'entrée d'
            # `evaluate_exits`), mais encore servie au dashboard comme
            # ouverte, gelée. Repéré sur 2 positions bloquées 6 jours après
            # un TRAILING_STOP dont le journal n'avait jamais pu s'écrire.
            try:
                rows.append(
                    self.journal.record_exit(
                        position=position,
                        exit_price=price,
                        pnl_pct=pnl_pct,
                        fraction=fraction,
                        reason=action.reason,
                        is_final=not position.is_open,
                        exit_cost_pct=exit_cost.total_cost_pct if exit_cost else None,
                        exit_cost_partial=exit_cost.partial if exit_cost else None,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[Portfolio] ⚠️ écriture journal impossible pour "
                    f"{position.symbol} ({position.id}) — {type(exc).__name__}: {exc}"
                )

            if not position.is_open:
                self._finalize(position, price)
                break

        if rows:
            # Une sortie doit survivre à un crash : écriture immédiate.
            self.save_positions()
        else:
            # Ce qu'on risque de perdre entre deux flush : la dérive de
            # high_water_pct / low_water_pct. De l'instrumentation, plus la
            # référence du trailing — qui ne s'arme qu'au-dessus de +200%.
            self.dirty = True
        return rows

    def flush(self) -> bool:
        """Écrit si quelque chose a bougé. Appelé une fois par tick."""
        if not self.dirty:
            return False
        self.save_positions()
        return True

    def force_close(
        self, position_id: str, price: float, reason: str = "MANUAL_STOP"
    ) -> Optional[dict[str, Any]]:
        """Sortie totale immédiate, hors règles — utilisée par le panic button.

        Route par le MÊME mesureur de coût que `update()` : un panic close
        arrive précisément dans les moments où le slippage et les priority
        fees sont les pires (congestion, marché stressé) — c'est le pire
        endroit pour laisser le P&L au prix nu. Trouvé en revue adversariale
        Codex, classé `medium` là-bas ; traité ici au même niveau que la
        sortie finale normale, pas en dessous.
        """
        position = self.positions.get(position_id)
        if position is None or price <= 0:
            return None

        fraction = position.remaining_fraction

        exit_cost = None
        cost_pct = 0.0
        if self.exit_fee_measurer is not None:
            exit_notional = (
                position.size_usd * fraction * (price / position.entry_price)
                if position.entry_price > 0
                else position.size_usd * fraction
            )
            try:
                exit_cost = self.exit_fee_measurer(position.token_address, exit_notional)
            except Exception:  # noqa: BLE001
                exit_cost = None
            if exit_cost is not None:
                cost_pct = exit_cost.total_cost_pct

        pnl_pct = position.pnl_pct(price) - cost_pct
        position = apply_exit(
            position, ExitAction(fraction, reason, is_final=True), price, cost_pct=cost_pct
        )
        self.positions[position_id] = position
        self.capital += position.size_usd * fraction * (1 + pnl_pct / 100)

        row = self.journal.record_exit(
            position=position,
            exit_price=price,
            pnl_pct=pnl_pct,
            fraction=fraction,
            reason=reason,
            is_final=True,
            exit_cost_pct=exit_cost.total_cost_pct if exit_cost else None,
            exit_cost_partial=exit_cost.partial if exit_cost else None,
        )
        self._finalize(position, price)
        return row

    def _finalize(self, position: Position, exit_price: float) -> None:
        """Clôture définitive : stats, cooldown, notification."""
        del self.positions[position.id]
        self.save_positions()
        self.closed_count += 1

        total_pnl_pct = 100 * position.realized_pnl_usd / position.size_usd
        if position.realized_pnl_usd < 0:
            self.consecutive_losses += 1
            if self.cooldown_hours > 0 and self.consecutive_losses >= self.losses_trigger:
                self.cooldown_until = time.time() + self.cooldown_hours * 3600
                print(
                    f"⛔ COOLDOWN {self.cooldown_hours}h — "
                    f"{self.consecutive_losses} pertes consécutives"
                )
                if self.notifier:
                    self.notifier.send(
                        f"⛔ <b>COOLDOWN {self.cooldown_hours}h</b>\n"
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

    def equity(self) -> float:
        """Capital libre + valeur d'entrée des positions ouvertes (approximation)."""
        engaged = sum(p.size_usd * p.remaining_fraction for p in self.positions.values())
        return self.capital + engaged

    def stats(self, journal_rows: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        rows = journal_rows if journal_rows is not None else self.journal.read_positions()
        wins = [r for r in rows if r.get("pnl_usd", 0) > 0]
        losses = [r for r in rows if r.get("pnl_usd", 0) <= 0]
        gross_win = sum(r.get("pnl_usd", 0) for r in wins)
        gross_loss = abs(sum(r.get("pnl_usd", 0) for r in losses))

        return {
            "mode": self.mode,
            "baseline": round(self.baseline, 2),
            "capital": round(self.capital, 2),
            "equity": round(self.equity(), 2),
            "total_pnl_usd": round(self.equity() - self.baseline, 2),
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
