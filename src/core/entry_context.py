"""JOURNAL ENRICHI À L'ENTRÉE (2026-10-10) — purement observationnel.

POURQUOI. La recalibration du score (docs/score_calibration_2026-10-10.md) n'a
pu tester que 8 variables : les composantes du score alpha, le social, le smart
money, la concentration des holders et le coût aller-retour MESURÉ À L'ENTRÉE
n'étaient pas écrits par trade. Pire, les seules mesures de carnet disponibles
(`microstructure_log`, `exit_cost_pct`) sont prises À LA SORTIE : les utiliser
comme prédicteurs d'entrée est du look-ahead.

CE QUE FAIT CE MODULE. Figer, au moment exact de l'ouverture, tout ce que le
bot SAVAIT déjà sur le candidat. Aucun appel réseau, aucune décision : on
recopie des champs déjà présents sur `Candidate` et on recalcule (CPU pur) les
composantes absolues du score. Une valeur absente vaut `None`, jamais 0.

Les clés sont préfixées (`entry_`, `alpha_comp_`, `score_v2_`) pour ne jamais
écraser une colonne historique du journal. `entry_roundtrip_cost_pct` (devis
Jupiter aller-retour de la porte économie, AVANT l'achat) est distinct de
`exit_cost_pct` (mesuré à la sortie).
"""

from typing import Any, Optional

ENTRY_CONTEXT_VERSION = 1

# Champs de `Candidate` recopiés tels quels, sous le préfixe `entry_`.
CANDIDATE_FIELDS = (
    # marché / flux
    "market_cap", "volume_5m", "volume_24h", "volume_liquidity_ratio",
    "price_change_1m", "price_change_1h", "price_change_24h", "buys_5m", "sells_5m",
    # holders
    "holders_is_exact", "top_holder_pct", "top10_holder_pct", "dev_wallet_pct",
    # sécurité / LP
    "lp_locked_pct", "burn_ratio", "dev_token_burn_ratio",
    "mint_authority_revoked", "freeze_authority_revoked",
    # social
    "social_mentions_1h", "social_unique_authors", "social_engagement",
    "social_velocity_15m", "social_sample_size", "has_social",
    # smart money / wallets
    "smart_money_buys_30m", "smart_money_sells_30m", "smart_money_wallets_30m",
    "smart_money_volume_usd", "smart_money_weighted_buys", "smart_money_newest_age_min",
    "wallet_reliability_score",
    # manipulation / créateur (GMGN, même appel que la découverte)
    "swaps", "sniper_count", "bot_degen_count", "renowned_count", "bundler_rate",
    "insider_rate", "entrapment_ratio", "rug_ratio", "is_wash_trading",
    "bluechip_owner_pct", "creator_token_status", "twitter_rename_count",
    "twitter_create_token_count", "launchpad", "initial_liquidity",
    "history_highest_market_cap", "dexscreener_paid", "manipulation_source",
)


def _num(value: Any, digits: int = 4) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return round(value, digits)
    return value


def _safe(fn, *args) -> Optional[float]:
    try:
        value = fn(*args)
    except Exception:  # une mesure ne casse jamais une entrée
        return None
    return None if value is None else round(float(value), 2)


def build_entry_context(
    candidate: Any, entry_roundtrip_cost_pct: Optional[float] = None
) -> dict[str, Any]:
    """Instantané plat, sérialisable JSON, de ce qui était connu à la décision."""
    context: dict[str, Any] = {"entry_context_version": ENTRY_CONTEXT_VERSION}
    try:
        for name in CANDIDATE_FIELDS:
            context[f"entry_{name}"] = _num(getattr(candidate, name, None))

        buys = getattr(candidate, "buys_5m", 0) or 0
        sells = getattr(candidate, "sells_5m", 0) or 0
        context["entry_buy_sell_ratio_5m"] = (
            round(buys / (buys + sells), 4) if (buys + sells) > 0 else None
        )
        risks = getattr(candidate, "rugcheck_risks", ()) or ()
        context["entry_rugcheck_risks"] = list(risks)

        # Composantes ABSOLUES du score (même code que la porte d'entrée).
        from src.core.scoring import COMPONENTS

        for name, (score_fn, _rank) in COMPONENTS.items():
            context[f"alpha_comp_{name}"] = _safe(score_fn, candidate)
        # Composantes MÉLANGÉES (60 % absolu + 40 % rang du lot) telles que
        # calculées au scan — celles du score classant.
        sub = getattr(candidate, "sub_scores", None) or {}
        context["alpha_sub_scores"] = {k: _num(v) for k, v in dict(sub).items()}

        from src.core.score_v2 import score_v2_probability

        try:
            context["score_v2_prob"] = round(score_v2_probability(candidate), 4)
        except Exception:
            context["score_v2_prob"] = None
    except Exception as exc:  # pragma: no cover - défense en profondeur
        context["entry_context_error"] = f"{type(exc).__name__}: {exc}"[:200]

    context["entry_roundtrip_cost_pct"] = (
        round(float(entry_roundtrip_cost_pct), 3)
        if entry_roundtrip_cost_pct is not None
        else None
    )
    return context
