"""Porte d'entrée unique avant toute ouverture de position.

POURQUOI UNE SEULE FONCTION. Sept bras, une seule méthode `_open_position` :
tout ce qui autorise une entrée doit y passer, sinon un bras (ou un futur
appel direct) contourne silencieusement un garde-fou. `pre_trade_check` est
ce verrou — Optional[str] : `None` autorise, sinon la raison du refus.

PAPER-loose : compose les gardes déjà en place (`can_open`, concentration
flotte, économie déjà tranchée). Jupiter / prix / vendabilité absents ne
bloquent PAS — même invariant que le reste du pipeline papier : une donnée
manquante n'empêche pas d'apprendre.

LIVE-strict : fail-closed sur données manquantes, exige `live_mode_allowed`,
refuse si `risk_rules` sont désarmés (cooldown ou drawdown à 0). La branche
existe pour le jour où LIVE sera déverrouillé ; elle ne tourne pas tant que
`mode=PAPER`.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from src.core import correlation

# Motif renvoyé par `correlation.verdict` — sert à distinguer `continue`
# (autre secteur possible) de `break` (plus de place / cooldown / LIVE).
_SOFT_REJECT_MARKERS = ("concentration meta", "filtre anti-pump")


def pre_trade_check(
    *,
    mode: str,
    candidate: Any,
    portfolio: Any,
    max_positions: int,
    fleet_exposure: Any = None,
    size_usd: float = 0.0,
    max_sector_positions: int = 0,
    max_sector_exposure_pct: float = 0.0,
    economics_reject: Optional[str] = None,
    live_allowed: Optional[tuple] = None,
    jupiter_ok: Optional[bool] = None,
    sellable: Optional[bool] = None,
    price_ok: Optional[bool] = None,
    risk_rules: Optional[Mapping[str, Any]] = None,
    require_market_data: bool = False,
    max_score_alpha: Optional[float] = None,
    max_volume_liquidity_ratio: Optional[float] = None,
    max_arm_exposure_pct: float = 0.0,
    arm_exposure_size_usd: Optional[float] = None,
) -> Optional[str]:
    """None = ouverture autorisée. Sinon, la raison du refus.

    `max_arm_exposure_pct` : plafond du total engagé PAR BRAS, en % de la base
    prudente du compte du bras (voir `arm_exposure_verdict`). 0 = désactivé.
    `arm_exposure_size_usd` : taille à ajouter pour ce contrôle (défaut : `size_usd`).

    Appeler au début de `_open_position` (chemin unique) avec
    `require_market_data=True` après la garde économique. Un appel anticipé
    depuis `_try_entries` (sans économie ni carnet) garde le court-circuit
    avant l'analyse technique coûteuse.
    """
    is_live = (mode or "").upper() == "LIVE"
    rules: Mapping[str, Any] = risk_rules or {}

    if is_live:
        live_block = _live_strict(
            live_allowed=live_allowed,
            risk_rules=rules,
            require_market_data=require_market_data,
            jupiter_ok=jupiter_ok,
            sellable=sellable,
            price_ok=price_ok,
        )
        if live_block:
            return live_block

    blocker = portfolio.can_open(candidate, max_positions)
    if blocker:
        return blocker

    arm_blocker = arm_exposure_verdict(
        portfolio,
        size_usd if arm_exposure_size_usd is None else arm_exposure_size_usd,
        max_arm_exposure_pct,
    )
    if arm_blocker:
        return arm_blocker

    pump_blocker = anti_pump_verdict(
        candidate,
        max_score_alpha=max_score_alpha,
        max_volume_liquidity_ratio=max_volume_liquidity_ratio,
    )
    if pump_blocker:
        return pump_blocker

    if fleet_exposure is not None:
        sector = correlation.classify(
            getattr(candidate, "symbol", None),
            getattr(candidate, "name", None),
        )
        sector_blocker = correlation.verdict(
            fleet_exposure,
            sector,
            size_usd,
            max_sector_positions=max_sector_positions,
            max_sector_exposure_pct=max_sector_exposure_pct,
        )
        if sector_blocker:
            return sector_blocker

    if economics_reject:
        return economics_reject

    return None


def arm_exposure_verdict(
    portfolio: Any, size_usd: float, max_arm_exposure_pct: float
) -> Optional[str]:
    """Plafond du total engagé par bras (validation Killian attendue, 2026-11-22).

    Refuse un NOUVEAU trade si (déjà engagé + nouveau trade) dépasserait
    `max_arm_exposure_pct` % de la base prudente du compte du bras
    (`portfolio.exposure_base()` = min(solde de départ, valeur actuelle)).
    Ne ferme jamais une position ouverte. 0 ou absent = désactivé.
    Un portefeuille qui ne sait pas mesurer (bancs de test) n'est pas bloqué.
    """
    try:
        pct = float(max_arm_exposure_pct or 0)
    except (TypeError, ValueError):
        return None
    if pct <= 0:
        return None
    engaged_fn = getattr(portfolio, "engaged_total", None)
    base_fn = getattr(portfolio, "exposure_base", None)
    if not callable(engaged_fn) or not callable(base_fn):
        return None
    engaged = float(engaged_fn())
    base = float(base_fn())
    cap = base * pct / 100.0
    size = max(0.0, float(size_usd or 0))
    if engaged + size > cap + 1e-9:
        return (
            f"plafond du bras : {engaged:.2f} $ déjà engagés + {size:.2f} $ "
            f"dépasseraient {pct:g} % du compte ({cap:.2f} $ sur {base:.2f} $)"
        )
    return None


def anti_pump_verdict(
    candidate: Any,
    *,
    max_score_alpha: Optional[float] = None,
    max_volume_liquidity_ratio: Optional[float] = None,
) -> Optional[str]:
    """Filtre anti-pump (bilan du 2026-10-24, validé par Killian).

    Refuse les tokens qui viennent de pomper : score alpha (classement,
    champ `score_alpha` du journal) > seuil OU volume 1 h / liquidité > seuil.
    Hors échantillon (avant/après le 20/10) les exclus avaient PF 0,48 / 0,18
    contre 1,06 / 0,60 pour les gardés. Seuil absent ou <= 0 = désactivé.
    Donnée manquante = ne bloque pas (invariant PAPER).
    """
    if max_score_alpha is not None and float(max_score_alpha) > 0:
        score = getattr(candidate, "alpha_score", None)
        if isinstance(score, (int, float)) and score > float(max_score_alpha):
            return f"filtre anti-pump : score alpha {score:.1f} > {float(max_score_alpha):g}"
    if max_volume_liquidity_ratio is not None and float(max_volume_liquidity_ratio) > 0:
        ratio = getattr(candidate, "volume_liquidity_ratio", None)
        if isinstance(ratio, (int, float)) and ratio > float(max_volume_liquidity_ratio):
            return (
                f"filtre anti-pump : volume/liquidité {ratio:.2f} > "
                f"{float(max_volume_liquidity_ratio):g}"
            )
    return None


def is_soft_reject(reason: Optional[str]) -> bool:
    """True = tenter le candidat suivant (`continue`), False = `break`.

    La concentration sectorielle ne vaut que pour CE secteur — le suivant
    peut être d'une autre meta. Les autres refus (portefeuille plein,
    cooldown, LIVE verrouillé) valent pour tout le reste du lot.
    """
    if not reason:
        return False
    return any(marker in reason for marker in _SOFT_REJECT_MARKERS)


def _live_strict(
    *,
    live_allowed: Optional[tuple],
    risk_rules: Mapping[str, Any],
    require_market_data: bool,
    jupiter_ok: Optional[bool],
    sellable: Optional[bool],
    price_ok: Optional[bool],
) -> Optional[str]:
    """Gardes LIVE fail-closed. N'affecte jamais le mode PAPER."""
    if live_allowed is not None:
        allowed = bool(live_allowed[0]) if live_allowed else False
        why = live_allowed[1] if len(live_allowed) > 1 else ""
        if not allowed:
            return f"LIVE verrouillé — {why}"

    # Même si params.json a laissé cooldown/DD à 0 (réglage PAPER), LIVE
    # refuse d'ouvrir : un risque désarmé n'est pas un risque accepté.
    try:
        cooldown = float(risk_rules.get("cooldown_hours") or 0)
    except (TypeError, ValueError):
        cooldown = 0.0
    try:
        drawdown_stop = float(risk_rules.get("max_drawdown_stop_pct") or 0)
    except (TypeError, ValueError):
        drawdown_stop = 0.0
    if cooldown <= 0:
        return "LIVE — risk_rules.cooldown_hours doit être > 0"
    if drawdown_stop <= 0:
        return "LIVE — risk_rules.max_drawdown_stop_pct doit être > 0"

    if not require_market_data:
        return None

    if jupiter_ok is not True:
        return "LIVE — Jupiter indisponible (fail-closed)"
    if price_ok is not True:
        return "LIVE — prix indisponible (fail-closed)"
    if sellable is False:
        return "LIVE — token non vendable"
    if sellable is not True:
        return "LIVE — vendabilité non vérifiée (fail-closed)"
    return None
