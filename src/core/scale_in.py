"""Contrôles AVANT l'ajout de l'entrée progressive (bras `scaled_be`).

Ajouté le 2026-10-10 (demande Killian) : l'ajout des 2/3 ne se fait plus au
prix observé, sans coût ni plafond. Avant d'ajouter, on refait :

  1. la liquidité minimale du document du bras (copie du témoin) ;
  2. un DEVIS D'ACHAT Jupiter pour le montant ajouté -> prix EFFECTIF
     d'exécution (impact de prix + frais de pool inclus dans `outAmount`),
     utilisé pour le prix moyen et donc pour le stop ;
  3. un devis aller-retour à la TAILLE TOTALE : test de revente (honeypot,
     mêmes règles que `JupiterAPI.sellable`) et coût de sortie estimé,
     comparé à `max_exit_cost_pct_entry`.

FAIL CLOSED : à l'inverse du reste du pipeline (« une donnée manquante ne
rejette jamais »), un devis ou une donnée indisponible ANNULE l'ajout. Ne pas
ajouter est toujours sûr — la position initiale continue avec ses règles.

NE BLOQUE PAS LA BOUCLE : chaque devis a un timeout court, aucun réessai, et
un délai total au-delà duquel on abandonne. Rien n'est exécuté : `/swap/v2/order`
ne rend qu'un devis (voir ALLOWED_PATHS de `src/apis/jupiter.py`).

Module PUR vis-à-vis du portefeuille : il ne modifie rien, il rend un verdict.
"""

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from src.apis.jupiter import HONEYPOT_MIN_EXIT_COST_PCT, MIN_IMPACT_FOR_RATIO, SOL_MINT

QUOTE_TIMEOUT_SECONDS = 3.0
TOTAL_DEADLINE_SECONDS = 8.0

# Raisons journalisées (SCALE_IN_SKIPPED). `above_max` est décidé par le
# portefeuille (prix observé ou prix effectif au-delà du plafond).
ABOVE_MAX = "above_max"
QUOTE_FAILED = "quote_failed"
SAFETY_FAILED = "safety_failed"
COST_TOO_HIGH = "cost_too_high"


@dataclass(frozen=True)
class ScaleInCheck:
    ok: bool
    reason: str = "ok"
    detail: str = ""
    effective_price: Optional[float] = None
    buy_impact_pct: Optional[float] = None
    round_trip_total_pct: Optional[float] = None
    extra: dict = field(default_factory=dict)


def _fail(reason: str, detail: str, **extra: Any) -> ScaleInCheck:
    return ScaleInCheck(
        ok=False, reason=reason, detail=detail,
        round_trip_total_pct=extra.pop("round_trip_total_pct", None), extra=extra,
    )


def effective_price_from_quote(
    usd_in: float, out_amount: int, observed_price: float
) -> tuple[Optional[float], Optional[int]]:
    """Prix effectif (USD par token) d'un devis d'achat SOL -> token.

    `outAmount` est en unités ATOMIQUES et le devis ne donne pas les
    décimales du token. On les déduit : le nombre de tokens attendu au prix
    observé est `usd_in / observed_price`, donc `outAmount / attendu` vaut
    10^décimales à l'écart de prix près. L'arrondi de log10 est exact tant que
    prix effectif et prix observé restent dans un facteur ~3 ; au-delà le
    contrôle de cohérence ci-dessous rejette (None) dès un facteur 1,5.
    """
    if usd_in <= 0 or out_amount <= 0 or observed_price <= 0:
        return None, None
    attendu = usd_in / observed_price
    ratio = out_amount / attendu
    if ratio <= 0:
        return None, None
    decimals = round(math.log10(ratio))
    if decimals < 0 or decimals > 12:
        return None, None
    tokens = out_amount / (10 ** decimals)
    effective = usd_in / tokens
    # Fenêtre ÉTROITE : un écart d'un facteur ~3 ferait basculer l'arrondi
    # des décimales. Un devis à +50 % du prix observé n'est de toute façon
    # pas un ajout acceptable (plafond +10 %).
    if not 0.67 <= effective / observed_price <= 1.5:
        return None, None
    return effective, decimals


def check_scale_in(
    jupiter: Any,
    token_address: str,
    add_usd: float,
    total_usd: float,
    observed_price: float,
    liquidity_usd: Optional[float],
    min_liquidity_usd: float = 0.0,
    max_exit_cost_pct: float = 0.0,
    sol_price_fn: Optional[Callable[[], Optional[float]]] = None,
    timeout: float = QUOTE_TIMEOUT_SECONDS,
    deadline_seconds: float = TOTAL_DEADLINE_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> ScaleInCheck:
    start = clock()

    # 1. Liquidité minimale (donnée du cycle, aucun appel).
    if min_liquidity_usd and min_liquidity_usd > 0:
        if liquidity_usd is None:
            return _fail(SAFETY_FAILED, "liquidité courante inconnue (fail closed)")
        if liquidity_usd < min_liquidity_usd:
            return _fail(
                SAFETY_FAILED,
                f"liquidité {liquidity_usd:.0f} $ < minimum {min_liquidity_usd:.0f} $",
                liquidity_usd=liquidity_usd,
            )

    if jupiter is None or not getattr(jupiter, "enabled", False):
        return _fail(QUOTE_FAILED, "Jupiter indisponible")

    sol_price = None
    try:
        sol_price = sol_price_fn() if sol_price_fn else None
    except Exception:  # noqa: BLE001
        sol_price = None
    if not sol_price or sol_price <= 0:
        return _fail(QUOTE_FAILED, "prix SOL indisponible")

    def quote(inp: str, out: str, amount: int):
        if clock() - start > deadline_seconds:
            return "deadline"
        try:
            return jupiter.quote(inp, out, amount, timeout=timeout, max_retries=1)
        except Exception:  # noqa: BLE001
            return None

    # 2. Devis d'achat du complément -> prix effectif.
    add_lamports = int(add_usd / sol_price * 1e9)
    q_add = quote(SOL_MINT, token_address, add_lamports)
    if q_add == "deadline":
        return _fail(QUOTE_FAILED, "délai dépassé")
    if q_add is None or q_add.out_amount <= 0:
        return _fail(QUOTE_FAILED, "aucun devis d'achat pour l'ajout")
    effective, decimals = effective_price_from_quote(add_usd, q_add.out_amount, observed_price)
    if effective is None:
        return _fail(QUOTE_FAILED, "devis d'achat incohérent avec le prix observé")

    # 3. Aller-retour à la taille TOTALE : revente possible + coût de sortie.
    total_lamports = int(total_usd / sol_price * 1e9)
    q_buy = quote(SOL_MINT, token_address, total_lamports)
    if q_buy == "deadline":
        return _fail(QUOTE_FAILED, "délai dépassé", effective_price=effective)
    if q_buy is None or q_buy.out_amount <= 0:
        return _fail(QUOTE_FAILED, "aucun devis d'achat à la taille totale")
    q_sell = quote(token_address, SOL_MINT, q_buy.out_amount)
    if q_sell == "deadline":
        return _fail(QUOTE_FAILED, "délai dépassé", effective_price=effective)
    if q_sell is None:
        return _fail(SAFETY_FAILED, "AUCUNE ROUTE DE SORTIE — honeypot probable")
    if q_sell.out_amount <= 0:
        return _fail(SAFETY_FAILED, "la vente rend 0 — honeypot probable")
    if (
        q_buy.total_cost_pct >= MIN_IMPACT_FOR_RATIO
        and q_sell.total_cost_pct >= HONEYPOT_MIN_EXIT_COST_PCT
        and q_sell.total_cost_pct > q_buy.total_cost_pct * 5
    ):
        return _fail(
            SAFETY_FAILED,
            f"sortie {q_sell.total_cost_pct:.1f}% contre entrée "
            f"{q_buy.total_cost_pct:.1f}% — asymétrie de honeypot",
        )
    retour = q_sell.out_amount / total_lamports if total_lamports else 0
    if retour < 0.5:
        return _fail(SAFETY_FAILED, f"la vente ne rend que {retour:.0%} de la mise — piège")

    round_trip = round(q_buy.total_cost_pct + q_sell.total_cost_pct, 3)
    if max_exit_cost_pct and max_exit_cost_pct > 0 and round_trip > max_exit_cost_pct:
        return _fail(
            COST_TOO_HIGH,
            f"coût aller-retour {round_trip:.2f}% à {total_usd:.2f} $ > {max_exit_cost_pct:.1f}%",
            round_trip_total_pct=round_trip,
        )

    return ScaleInCheck(
        ok=True,
        effective_price=effective,
        buy_impact_pct=round(q_add.total_cost_pct, 3),
        round_trip_total_pct=round_trip,
        detail=f"devis OK ({decimals} déc.), aller-retour {round_trip:.2f}%",
        extra={"decimals": decimals, "elapsed_s": round(clock() - start, 2)},
    )
