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
    """Une sortie à exécuter : quelle fraction de la position initiale, pourquoi.

    `rung` : index du barreau d'échelle qui a déclenché, quand la sortie vient
    d'une échelle (`Position.ladder`). `apply_exit` s'en sert pour avancer
    `ladder_filled` — sans lui il faudrait relire la raison en texte pour
    savoir où on en est, ce que fait déjà `tp1_hit`/`tp2_hit` et qu'on ne veut
    pas généraliser à N barreaux.
    """

    fraction: float
    reason: str
    is_final: bool = False
    rung: Optional[int] = None


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
    alpha_score_absolute: float = 0.0
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

    # ÉCHELLE DE SORTIE — N barreaux `(niveau_pct, fraction)`, ordre croissant.
    # Vide = comportement d'origine (TP1/TP2/TP3), et c'est le cas de tous les
    # bras sauf ceux qui la configurent explicitement.
    #
    # POURQUOI ELLE EXISTE. Les trois take-profits ne permettent que DEUX
    # sorties partielles, ce qui oblige à choisir entre « sortir tôt » et
    # « laisser courir » au lieu de faire les deux. Mesuré sur les 196
    # positions de `runner` : le plus-haut médian est +9 %, 46 % des trades
    # touchent +10 %, mais seulement 6,6 % atteignent le TP1 à +75 % et AUCUN
    # les +400 % du TP2. Le bras attendait un niveau qui n'arrive pas et
    # encaissait le stop 86 % du temps.
    ladder: tuple[tuple[float, float], ...] = ()
    # Barreaux déjà consommés. Un entier plutôt qu'un booléen par barreau :
    # l'échelle est ordonnée, donc « j'en ai rempli 3 » suffit à savoir où
    # reprendre, et ça survit à la sérialisation JSON sans structure imbriquée.
    ladder_filled: int = 0

    # NIVEAU d'armement du breakeven, en % de P&L. `0` = désactivé.
    #
    # CE QUE ÇA CORRIGE. `breakeven_trigger` était présent dans les sept
    # documents de bras depuis le début et n'était LU NULLE PART :
    # `breakeven_moved` ne se posait qu'en effet de bord de TAKE_PROFIT_1.
    # Conséquence — un bras ne pouvait protéger sa position qu'en vendant la
    # moitié, et un bras dont le TP1 est haut (quality, consensus : +150)
    # restait exposé au stop plein jusque-là.
    #
    # L'armement ne peut qu'être PLUS PRÉCOCE que l'ancien comportement,
    # jamais plus tardif : TP1 continue d'armer le breakeven de son côté.
    breakeven_trigger: float = 0.0

    # Meta (secteur) du token, FIGÉE À L'ENTRÉE. Décidée par
    # `src/core/correlation.py` à partir du symbole ET du nom ; le nom
    # n'existe que sur `Candidate`, donc reclasser plus tard depuis le seul
    # symbole donnerait un secteur différent et une exposition qui dépendrait
    # du moment où on la regarde. `None` = non classé, jamais un secteur.
    sector: Optional[str] = None

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


def _ladder_exits(position: Position, pnl: float) -> list[ExitAction]:
    """Barreaux franchis depuis le dernier tick, dans l'ordre.

    PLUSIEURS BARREAUX PEUVENT TOMBER SUR UN MÊME TICK. Le monitoring
    échantillonne toutes les 5 à 20 s ; sur un memecoin, un tick peut passer
    de +8 % à +140 %. Ne remplir qu'un barreau par tick laisserait les quatre
    autres derrière alors que le prix les a tous traversés — et le suivant
    s'exécuterait au prix redescendu.

    Le DERNIER barreau qui épuise la fraction restante est marqué `is_final` :
    c'est ce qui déclenche la mesure du coût réel de sortie côté portefeuille
    (voir `PaperPortfolio.update`), qui ne doit se payer qu'une fois.
    """
    actions: list[ExitAction] = []
    reste = position.remaining_fraction
    for index in range(position.ladder_filled, len(position.ladder)):
        niveau, fraction = position.ladder[index]
        if pnl < niveau:
            break
        prise = min(fraction, reste)
        if prise <= 1e-9:
            break
        reste -= prise
        actions.append(
            ExitAction(
                prise,
                f"LADDER_{index + 1} (+{niveau:.0f}% cible, {pnl:+.0f}% réel)",
                is_final=reste <= 1e-9,
                rung=index,
            )
        )
    return actions


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

    # 5. ÉCHELLE DE SORTIE, quand le bras en configure une. Elle REMPLACE les
    #    trois take-profits — les mélanger ferait sortir deux fois la même
    #    fraction au même tick.
    if position.ladder:
        return _ladder_exits(position, pnl)

    # 5 bis. TAKE PROFITS — cascade possible sur un même tick si le prix a sauté
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
    if action.rung is not None:
        # `max` et pas `+1` : plusieurs barreaux peuvent tomber au même tick,
        # et `PaperPortfolio.update` les applique un par un. Repartir de
        # l'index du barreau garantit qu'aucun ne sera rejoué même si l'ordre
        # d'application changeait.
        updates["ladder_filled"] = max(position.ladder_filled, action.rung + 1)
    elif action.reason.startswith("TAKE_PROFIT_1"):
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
        # BREAKEVEN PAR NIVEAU, et non plus seulement en effet de bord du TP1.
        # Armé ICI parce que c'est une transition d'ÉTAT provoquée par le
        # prix, au même titre que le trailing juste au-dessus — pas une
        # sortie. L'armer dans `evaluate_exits` le lierait à un tick où une
        # sortie se produit, alors qu'un pic peut passer sans rien déclencher.
        #
        # Sur le plus-haut et pas sur le prix courant : un niveau franchi puis
        # reperdu A ÉTÉ franchi, et c'est précisément là que la protection
        # devait s'armer.
        if (
            not position.breakeven_moved
            and position.breakeven_trigger > 0
            and pnl >= position.breakeven_trigger
        ):
            updates["breakeven_moved"] = True
    if pnl < position.low_water_pct:
        updates["low_water_pct"] = pnl
        updates["low_water_at"] = time.time()

    return replace(position, **updates) if updates else position


# Ancien nom conservé : le trailing stop n'est qu'un des usages désormais.
update_high_water = update_water_marks
