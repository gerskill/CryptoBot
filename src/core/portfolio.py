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
from dataclasses import asdict, fields, replace
from datetime import datetime
from typing import Any, Callable, Optional

from src.core.correlation import classify as classify_sector
from src.core.exit_fees import ENTRY_COST_VERSION, exit_leg_pct, half_round_trip_pct
from src.core.journal import TradeJournal
from src.core.models import Candidate
from src.core.stats import longest_drawdown
from src.core.positions import (
    cost_vs_entry,
    ExitAction,
    Position,
    apply_exit,
    apply_scale_in,
    evaluate_exits,
    scale_in_eligible,
    update_water_marks,
)

# Après un contrôle d'ajout raté (devis, sécurité, coût), on ne redemande pas
# de devis avant ce délai : sinon chaque tick de 5 s dans la fenêtre coûterait
# 3 requêtes Jupiter (palier à 1 req/s) pour une réponse qui n'a pas changé.
SCALE_IN_RETRY_SECONDS = 60.0

MAX_POSITION_PCT_OF_CAPITAL = 0.05
CONSECUTIVE_LOSSES_TRIGGER = 3
COOLDOWN_HOURS = 2
KELLY_MIN_TRADES = 20
KELLY_WIN_RATE_FLOOR = 0.40

# Champs de Position typés `tuple` : JSON les rend en liste, il faut les
# reconvertir à la restauration (voir `_load_positions`).
TUPLE_FIELDS = ("exit_reasons",)
# `ladder` est un tuple de TUPLES : la conversion plate ci-dessus laisserait
# des listes à l'intérieur, et `_ladder_exits` dépaqueterait alors des listes
# là où il attend des paires. Traité à part, pas ajouté à TUPLE_FIELDS.
NESTED_TUPLE_FIELDS = ("ladder",)

# Le tick de surveillance mesure le coût HORS du verrou des livres, puis
# passe le résultat ici. `PREPARED_UNSET` veut dire « pas préparé » : on
# mesure comme avant. `None` veut dire « déjà tenté, rien de mesurable » :
# on ne rappelle pas le réseau sous le verrou.
PREPARED_UNSET = object()


def normalise_ladder(raw: Any) -> tuple[tuple[float, float], ...]:
    """`[[10, 0.2], [20, 0.2]]` -> `((10.0, 0.2), (20.0, 0.2))`, trié.

    TRIÉ PAR NIVEAU, parce que `_ladder_exits` parcourt les barreaux dans
    l'ordre et s'arrête au premier non atteint : une échelle saisie en
    désordre bloquerait sur son premier barreau haut et n'en remplirait
    jamais aucun autre.

    Une entrée illisible est IGNORÉE et n'interrompt pas la lecture des
    autres — une faute de frappe dans un document de bras ne doit pas
    supprimer silencieusement toute la stratégie de sortie ni faire tomber
    l'ouverture de position.
    """
    if not raw:
        return ()
    rungs: list[tuple[float, float]] = []
    for entry in raw:
        try:
            niveau, fraction = entry[0], entry[1]
            niveau, fraction = float(niveau), float(fraction)
        except (TypeError, ValueError, IndexError, KeyError):
            print(f"[Portfolio] barreau d'échelle ignoré (illisible) : {entry!r}")
            continue
        if fraction <= 0:
            continue
        rungs.append((niveau, fraction))
    return tuple(sorted(rungs, key=lambda rung: rung[0]))

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
    "(tests, ou clients Jupiter/Helius désactivés). Avec lui, l'achat et la "
    "vente paient chacun la moitié du même aller-retour mesuré — voir "
    "src/core/exit_fees.py. Les positions ouvertes avant buy-leg-v1 gardent "
    "l'ancien décompte, tout sur la sortie."
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
        partial_cost_estimator: Optional[Callable[[], Optional[float]]] = None,
        scale_in_checker: Optional[Callable[..., Any]] = None,
        depuis: Optional[float] = None,
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
        # Coût des VENTES PARTIELLES, estimé sur les mesures passées de ce
        # bras (ADR 012). Absent ou sous son plancher d'échantillon, les
        # jambes partielles restent au prix nu, comme avant.
        self.partial_cost_estimator = partial_cost_estimator
        # Vérificateur d'ajout (entrée progressive, bras `scaled_be`) :
        # `(position, add_usd, observed_price, liquidity_usd) -> ScaleInCheck`.
        # Injecté par `main.py`, comme le mesureur de coût : aucun I/O ici.
        self.scale_in_checker = scale_in_checker
        self._scale_in_attempt_at: dict[str, float] = {}
        self._scale_in_last_skip: dict[str, str] = {}
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
        # Début du compte courant (src/core/compte.py). None = tout le journal, comme avant.
        # Seules les positions OUVERTES depuis comptent pour le solde et les statistiques.
        self.depuis = depuis

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
        engaged = sum(
            p.size_usd * p.remaining_fraction for p in self.positions.values() if self._du_compte(p)
        )
        # Frais d'entrée déjà pris sur la part encore ouverte. Les jambes
        # vendues l'ont dans le journal ; on ne le retire pas une seconde fois.
        open_entry_fees = sum(
            p.size_usd * p.remaining_fraction * p.entry_cost_pct / 100.0
            for p in self.positions.values()
            if p.entry_cost_version and self._du_compte(p)
        )

        self.capital = capital + realized - engaged - open_entry_fees
        self.peak_capital = max(capital, self.capital + engaged)

        if realized or self.positions:
            print(
                f"[Portfolio] repris : {self.closed_count} trades clôturés "
                f"({realized:+.2f} $), {len(self.positions)} position(s) ouverte(s) "
                f"({engaged:.2f} $ engagés) — capital {self.capital:.2f} $"
            )

    def _du_compte(self, position: Any) -> bool:
        """Position ouverte dans le compte courant ? Une position d'avant finit sa vie dans l'ancien."""
        depuis = getattr(self, "depuis", None)
        return depuis is None or float(getattr(position, "entry_time", 0) or 0) >= depuis

    def _lignes_du_compte(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        depuis = getattr(self, "depuis", None)
        if depuis is None:
            return rows
        from src.core.compte import Compte
        compte = Compte(depuis=depuis, capital=self.baseline)
        return [r for r in rows if compte.contient(r)]

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
        rows = self._lignes_du_compte(self.journal.read_positions())
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
                for name in NESTED_TUPLE_FIELDS:
                    if clean.get(name):
                        clean[name] = normalise_ladder(clean[name])
                position = Position(**clean)
                restored[position.id] = position
            except (TypeError, ValueError) as exc:
                print(f"[Portfolio] position ignorée ({exc})")
        return restored

    def set_pair_address(self, position_id: str, pair_address: str) -> bool:
        """Paire DexScreener résolue après l'entrée (sans effet sur le trade)."""
        position = self.positions.get(position_id)
        if position is None or not pair_address or position.pair_address == pair_address:
            return False
        self.positions[position_id] = replace(
            position, pair_address=pair_address, pair_resolved_at=time.time()
        )
        self.save_positions()
        return True

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

    def _engaged_size(self, params: dict[str, Any], size_usd: float) -> float:
        """Taille vraiment achetée maintenant. L'entrée progressive n'engage qu'une fraction."""
        entry_rules = params.get("entry_rules", {}) or {}
        trigger = float(entry_rules.get("scale_in_trigger_pct", 0) or 0)
        initial_fraction = float(entry_rules.get("scale_in_initial_fraction", 1.0) or 1.0)
        if trigger > 0 and 0 < initial_fraction < 1:
            return round(size_usd * initial_fraction, 4)
        return size_usd

    def open(
        self,
        candidate: Candidate,
        params: dict[str, Any],
        size_usd: float,
        entry_context: Optional[dict[str, Any]] = None,
        entry_quote: Any = PREPARED_UNSET,
    ) -> Position:
        exits = params.get("exit_rules", {})
        # ENTRÉE PROGRESSIVE (opt-in, `entry_rules.scale_in_trigger_pct` > 0) :
        # `size_usd` reste la taille NORMALE décidée par l'appelant (Kelly +
        # garde économie, identique au témoin) ; seule la fraction initiale
        # est engagée maintenant, le reste attend le déclencheur.
        entry_rules = params.get("entry_rules", {}) or {}
        trigger = float(entry_rules.get("scale_in_trigger_pct", 0) or 0)
        max_pct = float(entry_rules.get("scale_in_max_pct", 0) or 0)
        initial_fraction = float(entry_rules.get("scale_in_initial_fraction", 1.0) or 1.0)
        planned_usd = size_usd
        pending_usd = 0.0
        size_usd = self._engaged_size(params, size_usd)
        if size_usd < planned_usd:
            pending_usd = round(planned_usd - size_usd, 4)
        else:
            trigger = 0.0
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
            # Observation seule (liens DexScreener/GMGN), cf. onchain_observer.
            social_score=_social_score(candidate),
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
            # FIGÉ ICI, ET NULLE PART AILLEURS. C'est le seul point du dépôt
            # où le `name` du token existe encore en même temps que la
            # position — `Position` ne garde que `symbol`. Classer plus tard
            # perdrait « Pomeranian » derrière « PMR ».
            sector=classify_sector(candidate.symbol, candidate.name),
            # Échelle de sortie et niveau de breakeven, figés à l'entrée comme
            # toutes les autres règles. `normalise_ladder` trie et rejette le
            # bruit : une échelle mal ordonnée sortirait dans le désordre.
            ladder=normalise_ladder(exits.get("ladder")),
            breakeven_trigger=float(exits.get("breakeven_trigger", 0) or 0),
            breakeven_stop_exact=bool(exits.get("breakeven_stop_exact", False)),
            scale_in_trigger_pct=trigger,
            scale_in_max_pct=max_pct if trigger > 0 else 0.0,
            scale_in_pending_usd=pending_usd,
            initial_entry_price=candidate.price_usd if trigger > 0 else 0.0,
            initial_size_usd=size_usd if trigger > 0 else 0.0,
            entry_context=dict(entry_context or {}),
        )
        self.positions[position.id] = position
        self.capital -= size_usd
        position = self._apply_entry_cost(position, size_usd, measured=entry_quote)
        self.save_positions()
        if self.notifier:
            self.notifier.send_entry(position)
        return position

    def _measure_entry_leg(self, token: str, notional: float) -> Any:
        if self.mode != "PAPER" or self.exit_fee_measurer is None or notional <= 0:
            return None
        try:
            return self.exit_fee_measurer(token, notional)
        except Exception:  # noqa: BLE001
            return None

    def _apply_entry_cost(
        self, position: Position, notional: float, measured: Any = PREPARED_UNSET
    ) -> Position:
        """Moitié du devis d'aller-retour, prise à l'achat. Rien si pas de devis.

        La version est posée dès que le mesureur existe, même si le devis
        a échoué : le trade est d'après le changement, et la sortie ne sera
        coupée en deux que si l'achat a vraiment été facturé.

        `measured` déjà obtenu hors verrou n'est pas redemandé ici.
        """
        if self.mode != "PAPER" or self.exit_fee_measurer is None:
            return position
        if measured is PREPARED_UNSET:
            measured = self._measure_entry_leg(position.token_address, notional)
        half = half_round_trip_pct(measured)
        charged = half is not None
        pct = half or 0.0
        if charged and pct:
            self.capital -= notional * pct / 100.0
        position = replace(
            position,
            entry_cost_version=ENTRY_COST_VERSION,
            entry_cost_pct=pct,
            entry_impact_charged=charged,
        )
        self.positions[position.id] = position
        return position

    def _blend_entry_cost(
        self,
        before: Position,
        position: Position,
        add_usd: float,
        measured: Any = PREPARED_UNSET,
    ) -> Position:
        """L'ajout paie sa propre moitié, puis le taux est la moyenne pondérée."""
        if measured is PREPARED_UNSET:
            measured = self._measure_entry_leg(position.token_address, add_usd)
        half = half_round_trip_pct(measured)
        add_pct = half or 0.0
        if half is not None and add_pct:
            if self._du_compte(position):
                self.capital -= add_usd * add_pct / 100.0
        total = position.size_usd
        if total <= 0:
            return position
        blended = (before.size_usd * before.entry_cost_pct + add_usd * add_pct) / total
        position = replace(
            position,
            entry_cost_pct=round(blended, 6),
            entry_impact_charged=True,
        )
        self.positions[position.id] = position
        return position

    def _exit_pnl(
        self, position: Position, price: float, cost_pct: float
    ) -> tuple[float, float, float]:
        """(P&L cash hors frais d'entrée, P&L journal, coût pour le réalisé).

        Le frais d'entrée a déjà quitté le cash. Le journal et la série de
        pertes le comptent, sinon un gain de prix plus petit que ce frais
        remet le compteur à zéro et la notification annonce un gain.
        """
        on_stake = cost_vs_entry(cost_pct, price, position.entry_price)
        price_pnl = position.pnl_pct(price) - on_stake
        entry_drag = position.entry_cost_pct if position.entry_cost_version else 0.0
        return price_pnl, price_pnl - entry_drag, on_stake + entry_drag

    def quote_final_cost(self, position: Position, price: float) -> Optional[Any]:
        """Devis de la jambe finale, sans rien modifier.

        Appelé HORS du verrou des livres : le devis est un appel réseau, et
        une entrée ne doit pas l'attendre. `None` si rien n'est mesurable.
        """
        if self.exit_fee_measurer is None or price <= 0:
            return None
        fraction = position.remaining_fraction
        notional = (
            position.size_usd * fraction * (price / position.entry_price)
            if position.entry_price > 0
            else position.size_usd * fraction
        )
        try:
            return self.exit_fee_measurer(position.token_address, notional)
        except Exception:  # noqa: BLE001
            return None

    def update(
        self,
        position_id: str,
        price: float,
        liquidity_drop_pct: Optional[float] = None,
        liquidity_usd: Optional[float] = None,
        prepared_final_cost: Any = PREPARED_UNSET,
        prepared_scale_check: Any = PREPARED_UNSET,
        prepared_scale_add: Optional[float] = None,
        prepared_entry_quote: Any = PREPARED_UNSET,
    ) -> list[dict[str, Any]]:
        """Applique les règles de sortie. Retourne les lignes de journal créées.

        `prepared_final_cost` et `prepared_scale_check`, quand ils ne valent
        pas `PREPARED_UNSET`, ont été mesurés avant la prise du verrou. On
        les réutilise tels quels, y compris `None` (mesure ratée) : rappeler
        le réseau ici bloquerait les entrées.
        """
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
            round_trip_pct = None
            cost_estimated = False
            if not action.is_final and self.partial_cost_estimator is not None:
                # JAMBE PARTIELLE (TP1, TP2) : pas de devis — l'ADR 009 tient,
                # un devis par jambe coûterait une requête Jupiter à 1 req/s.
                # On déduit la médiane des coûts DÉJÀ mesurés sur ce bras,
                # part vente seulement, marquée comme estimée. `None` tant
                # qu'il n'y a pas 10 mesures : dans ce cas prix nu, sans rien
                # inventer.
                try:
                    estime = self.partial_cost_estimator()
                except Exception:  # noqa: BLE001
                    estime = None
                if estime is not None:
                    cost_pct = estime
                    cost_estimated = True
            if action.is_final and (
                prepared_final_cost is not PREPARED_UNSET or self.exit_fee_measurer is not None
            ):
                # Notionnel COURANT, pas `size_usd` (figé à l'entrée) : sur une
                # position qui a fait x3, vendre `fraction` des tokens vaut
                # `fraction * (price / entry_price)` fois la mise initiale, pas
                # `fraction` fois. Coder l'ancien montant sous-dimensionnait le
                # devis d'impact de prix sur tout gagnant ayant déjà pris un TP
                # partiel — trouvé en revue adversariale Codex.
                if prepared_final_cost is not PREPARED_UNSET:
                    exit_cost = prepared_final_cost
                else:
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
                    cost_pct, round_trip_pct = exit_leg_pct(
                        exit_cost, position.entry_impact_charged
                    )
                else:
                    round_trip_pct = None

            price_pnl, journal_pnl, realized_cost = self._exit_pnl(
                position, price, cost_pct
            )
            position = apply_exit(position, action, price, cost_pct=realized_cost)
            self.positions[position_id] = position

            if self._du_compte(position):
                self.capital += position.size_usd * fraction * (1 + price_pnl / 100)

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
                        pnl_pct=journal_pnl,
                        fraction=fraction,
                        reason=action.reason,
                        is_final=not position.is_open,
                        exit_cost_pct=(
                            cost_pct if exit_cost is not None or cost_estimated else None
                        ),
                        exit_cost_partial=exit_cost.partial if exit_cost else None,
                        exit_cost_estimated=cost_estimated,
                        exit_cost_round_trip_pct=round_trip_pct,
                        entry_cost_version=position.entry_cost_version,
                        entry_cost_pct=(
                            position.entry_cost_pct if position.entry_cost_version else None
                        ),
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

        if not rows:
            # Entrée progressive : uniquement sur un tick SANS sortie, après
            # les règles de sortie (jamais d'ajout sur un tick qui a vendu).
            self._maybe_scale_in(
                position_id, price, liquidity_usd,
                prepared_scale_check=prepared_scale_check,
                prepared_scale_add=prepared_scale_add,
                prepared_entry_quote=prepared_entry_quote,
            )

        if rows:
            # Une sortie doit survivre à un crash : écriture immédiate.
            self.save_positions()
        else:
            # Ce qu'on risque de perdre entre deux flush : la dérive de
            # high_water_pct / low_water_pct. De l'instrumentation, plus la
            # référence du trailing — qui ne s'arme qu'au-dessus de +200%.
            self.dirty = True
        return rows

    def apply_scale_in_max(self, max_pct: float) -> int:
        """Applique le plafond d'ajout aux positions OUVERTES qui n'en ont pas.

        Les positions ouvertes avant l'introduction du plafond (fichier
        restauré) portent `scale_in_max_pct = 0` = sans plafond. Sans cette
        migration, elles garderaient l'ancien comportement jusqu'à leur
        clôture.
        """
        if max_pct <= 0:
            return 0
        changed = 0
        for pid, position in list(self.positions.items()):
            if position.scale_in_trigger_pct > 0 and not position.scaled_in \
                    and position.scale_in_max_pct <= 0:
                self.positions[pid] = replace(position, scale_in_max_pct=max_pct)
                changed += 1
        if changed:
            self.save_positions()
        return changed

    def _skip_scale_in(
        self, position: Position, reason: str, detail: str, price: float,
        extra: Optional[dict[str, Any]] = None, dedupe: bool = False,
    ) -> None:
        """Journalise SCALE_IN_SKIPPED. `dedupe` : une seule ligne tant que la
        raison ne change pas (prix au-dessus du plafond, tick après tick)."""
        if dedupe and self._scale_in_last_skip.get(position.id) == reason:
            return
        self._scale_in_last_skip[position.id] = reason
        arm = getattr(self.notifier, "arm_name", "")
        print(f"  ⏭️ SCALE_IN_SKIPPED [{arm}] {position.symbol} | {reason} | {detail}")
        try:
            self.journal.record_scale_skip(position, reason, detail, price, extra or {})
        except Exception as exc:  # noqa: BLE001
            print(f"[Portfolio] ⚠️ journal SCALE_IN_SKIPPED impossible ({position.id}) : {exc}")

    def _maybe_scale_in(
        self,
        position_id: str,
        price: float,
        liquidity_usd: Optional[float] = None,
        prepared_scale_check: Any = PREPARED_UNSET,
        prepared_scale_add: Optional[float] = None,
        prepared_entry_quote: Any = PREPARED_UNSET,
    ) -> bool:
        """Achète le complément de l'entrée progressive si les conditions tiennent.

        FENÊTRE [déclencheur, plafond] depuis l'entrée INITIALE, jugée d'abord
        sur le prix observé (aucun devis demandé hors fenêtre), puis sur le
        prix EFFECTIF du devis. Au-dessus du plafond : pas d'ajout, mais il
        reste possible si le prix REVIENT dans la fenêtre tant qu'aucune
        sortie n'a eu lieu (choix documenté : la règle porte sur le prix
        payé, pas sur l'historique du tick).

        Avec un vérificateur (`scale_in_checker`, production) : devis Jupiter
        réel, sécurité et coût de sortie, FAIL CLOSED. Plafond configuré mais
        aucun vérificateur : pas d'ajout (fail closed). Ni l'un ni l'autre :
        ancien comportement (prix observé), gardé pour compatibilité.
        """
        position = self.positions.get(position_id)
        if position is None or price <= 0 or not scale_in_eligible(position):
            return False
        pnl = position.pnl_pct(price)
        if pnl < position.scale_in_trigger_pct:
            self._scale_in_last_skip.pop(position_id, None)
            return False
        max_pct = position.scale_in_max_pct
        if max_pct > 0 and pnl > max_pct:
            self._skip_scale_in(
                position, "above_max",
                f"prix observé {pnl:+.1f}% > plafond +{max_pct:.0f}%", price,
                {"observed_pnl_pct": round(pnl, 3)}, dedupe=True,
            )
            return False

        add_usd = min(position.scale_in_pending_usd, max(0.0, self.capital))
        if add_usd <= 1e-9:
            return False

        effective = price
        check = None
        if self.scale_in_checker is not None or max_pct > 0:
            now = time.time()
            last = self._scale_in_attempt_at.get(position_id)
            if last is not None and now - last < SCALE_IN_RETRY_SECONDS:
                return False
            if prepared_scale_check is not PREPARED_UNSET:
                # Devis déjà fait hors verrou. S'il visait une autre taille
                # (le capital a bougé), on n'ajoute pas et on ne consomme pas
                # le délai de retry : le tick suivant redemandera un devis.
                if (
                    prepared_scale_add is not None
                    and abs(prepared_scale_add - add_usd) > 1e-6
                ):
                    return False
                self._scale_in_attempt_at[position_id] = now
                check = prepared_scale_check
            elif self.scale_in_checker is None:
                self._scale_in_attempt_at[position_id] = now
                self._skip_scale_in(position, "quote_failed",
                                    "aucun vérificateur de devis (fail closed)", price)
                return False
            else:
                self._scale_in_attempt_at[position_id] = now
                try:
                    check = self.scale_in_checker(position, add_usd, price, liquidity_usd)
                except Exception as exc:  # noqa: BLE001
                    self._skip_scale_in(position, "quote_failed",
                                        f"{type(exc).__name__}: {exc}", price)
                    return False
            if check is None or not check.ok or not check.effective_price:
                self._skip_scale_in(
                    position, getattr(check, "reason", "quote_failed") or "quote_failed",
                    getattr(check, "detail", "vérificateur sans réponse"), price,
                    dict(getattr(check, "extra", {}) or {},
                         round_trip_total_pct=getattr(check, "round_trip_total_pct", None),
                         observed_pnl_pct=round(pnl, 3)),
                )
                return False
            effective = check.effective_price
            eff_pnl = position.pnl_pct(effective)
            if max_pct > 0 and eff_pnl > max_pct:
                self._skip_scale_in(
                    position, "above_max",
                    f"prix effectif du devis {eff_pnl:+.1f}% > plafond +{max_pct:.0f}%",
                    price, {"effective_price": effective, "effective_pnl_pct": round(eff_pnl, 3),
                            "observed_pnl_pct": round(pnl, 3)},
                )
                return False

        before = position
        position = apply_scale_in(position, effective, add_usd, observed_price=price)
        self.positions[position_id] = position
        if self._du_compte(position):
            self.capital -= add_usd
        if before.entry_cost_version == ENTRY_COST_VERSION and before.entry_impact_charged:
            position = self._blend_entry_cost(
                before, position, add_usd, measured=prepared_entry_quote
            )
        self.save_positions()
        self._scale_in_attempt_at.pop(position_id, None)
        self._scale_in_last_skip.pop(position_id, None)
        arm = getattr(self.notifier, "arm_name", "")
        print(
            f"  ➕ SCALE_IN [{arm}] {position.symbol} | observé {before.pnl_pct(price):+.1f}% "
            f"/ effectif {before.pnl_pct(effective):+.1f}% (fenêtre "
            f"+{before.scale_in_trigger_pct:.0f}..+{before.scale_in_max_pct:.0f}%) "
            f"| ajout {add_usd:.2f} $ | taille {before.size_usd:.2f} -> {position.size_usd:.2f} $ "
            f"| prix moyen {position.entry_price:.8f} | stop -> breakeven exact"
        )
        try:
            self.journal.record_scale_in(before, position, effective, add_usd, check=check,
                                         observed_price=price)
        except Exception as exc:  # noqa: BLE001
            print(f"[Portfolio] ⚠️ journal SCALE_IN impossible ({position.id}) : {exc}")
        return True

    def flush(self) -> bool:
        """Écrit si quelque chose a bougé. Appelé une fois par tick."""
        if not self.dirty:
            return False
        self.save_positions()
        return True

    def force_close(
        self,
        position_id: str,
        price: float,
        reason: str = "MANUAL_STOP",
        exit_cost: Any = PREPARED_UNSET,
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

        cost_pct = 0.0
        round_trip_pct = None
        if exit_cost is PREPARED_UNSET:
            exit_cost = self.quote_final_cost(position, price)
        if exit_cost is not None:
            cost_pct, round_trip_pct = exit_leg_pct(
                exit_cost, position.entry_impact_charged
            )

        price_pnl, journal_pnl, realized_cost = self._exit_pnl(position, price, cost_pct)
        position = apply_exit(
            position, ExitAction(fraction, reason, is_final=True), price, cost_pct=realized_cost
        )
        self.positions[position_id] = position
        if self._du_compte(position):
            self.capital += position.size_usd * fraction * (1 + price_pnl / 100)

        row = self.journal.record_exit(
            position=position,
            exit_price=price,
            pnl_pct=journal_pnl,
            fraction=fraction,
            reason=reason,
            is_final=True,
            exit_cost_pct=cost_pct if exit_cost is not None else None,
            exit_cost_partial=exit_cost.partial if exit_cost else None,
            exit_cost_round_trip_pct=round_trip_pct,
            entry_cost_version=position.entry_cost_version,
            entry_cost_pct=(
                position.entry_cost_pct if position.entry_cost_version else None
            ),
        )
        self._finalize(position, price)
        return row

    def close_worthless(self, position_id: str, reason: str) -> Optional[dict[str, Any]]:
        """Sortie finale de toute la part restante à prix 0 : -100 % honnête.

        POURQUOI UNE MÉTHODE À PART. `force_close` refuse un prix <= 0, et c'est
        voulu : un prix nul est une donnée manquante, pas une cotation. Mais une
        position dont AUCUNE source ne donne de prix (token mort ou rugué) doit
        pourtant sortir des livres, sinon elle reste ouverte pour toujours et
        disparaît de `read_positions()` (B1). Pas de devis : un token invendable
        n'en a pas, donc coût de sortie `None` et capital crédité de 0. Le frais
        d'entrée déjà payé reste compté dans le P&L du journal, comme
        `_exit_pnl` le fait pour toute sortie finale. Les jambes TP1/TP2 déjà
        encaissées restent dans le journal : seule la part restante est perdue.
        """
        position = self.positions.get(position_id)
        if position is None:
            return None

        fraction = position.remaining_fraction
        price_pnl, journal_pnl, realized_cost = self._exit_pnl(position, 0.0, 0.0)
        position = apply_exit(
            position, ExitAction(fraction, reason, is_final=True), 0.0, cost_pct=realized_cost
        )
        self.positions[position_id] = position
        # price_pnl vaut -100 : la part restante ne rapporte rien.
        if self._du_compte(position):
            self.capital += position.size_usd * fraction * (1 + price_pnl / 100)

        row = self.journal.record_exit(
            position=position,
            exit_price=0.0,
            pnl_pct=journal_pnl,
            fraction=fraction,
            reason=reason,
            is_final=True,
            exit_cost_pct=None,
            entry_cost_version=position.entry_cost_version,
            entry_cost_pct=(
                position.entry_cost_pct if position.entry_cost_version else None
            ),
        )
        self._finalize(position, 0.0)
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
        engaged = sum(
            p.size_usd * p.remaining_fraction for p in self.positions.values() if self._du_compte(p)
        )
        return self.capital + engaged

    def stats(self, journal_rows: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        rows = (journal_rows if journal_rows is not None
                else self._lignes_du_compte(self.journal.read_positions()))
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
            # DURÉE, pas seulement profondeur. Sur quelques dizaines de
            # trades, la profondeur maximale tient à une ou deux positions ;
            # le temps passé sous le plus haut agrège tout l'intervalle et
            # dit s'il faut attendre ou arrêter. `None` = jamais descendu
            # sous son pic, ce qui n'est pas « épisode de durée nulle ».
            "longest_drawdown": (
                longest.as_dict() if (longest := longest_drawdown(rows, self.baseline)) else None
            ),
            "consecutive_losses": self.consecutive_losses,
            "cooldown_min": round(self.cooldown_remaining_min(), 0),
        }


def _social_score(candidate):
    from src.agents.onchain_observer import candidate_social_score  # import tardif
    return candidate_social_score(candidate)
