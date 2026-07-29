"""Calcul du SCORE ALPHA (0-100) — étape 2 du workflow.

DEUX SCORES, DEUX USAGES — distinction critique
-----------------------------------------------
`alpha_score_absolute` : seuils de saturation fixes, comparable d'un scan à
l'autre. C'est LUI qui autorise une entrée.

`alpha_score` : 60% absolu + 40% position dans le batch courant. C'est LUI qui
classe les candidats entre eux.

Pourquoi les séparer : avec du relatif dans la porte d'entrée, le meilleur
candidat d'un lot médiocre décroche 100/100 sur les composants relatifs et
franchit le seuil mécaniquement. Une nuit creuse produirait des entrées sur
les moins mauvais déchets disponibles.
"""

import math
from typing import Optional, Sequence

from src.core.models import Candidate

ABSOLUTE_WEIGHT = 0.6
RELATIVE_WEIGHT = 0.4

LIQUIDITY_SATURATION_USD = 150_000
VOLUME_RATIO_SATURATION = 3.0
SOCIAL_SATURATION_MENTIONS = 100
SOCIAL_SATURATION_AUTHORS = 30
SOCIAL_SATURATION_ENGAGEMENT = 1000
SPAM_RATIO_FLOOR = 0.5  # au-dessus de 50% d'auteurs distincts, aucune pénalité
SMART_MONEY_SATURATION_BUYS = 10


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _relative(value: float, values: Sequence[float]) -> float:
    """Position min-max de `value` dans `values`, sur 0-100."""
    if not values:
        return 50.0
    low, high = min(values), max(values)
    if high - low < 1e-12:
        return 50.0
    return 100.0 * (value - low) / (high - low)


def liquidity_absolute(candidate: Candidate) -> float:
    """Échelle log : 15K -> ~40, 50K -> ~70, 150K+ -> 100."""
    liquidity = max(candidate.liquidity_usd, 1.0)
    return _clamp(100 * math.log10(liquidity) / math.log10(LIQUIDITY_SATURATION_USD))


def volume_momentum_absolute(candidate: Candidate) -> float:
    """Ratio volume/liquidité, variation 5m et pression acheteuse."""
    ratio = _clamp(100 * candidate.volume_liquidity_ratio / VOLUME_RATIO_SATURATION)
    momentum = _clamp(50 + candidate.price_change_5m)
    pressure = _clamp(100 * candidate.buy_sell_ratio_5m)
    return 0.5 * ratio + 0.3 * momentum + 0.2 * pressure


def social_absolute(candidate: Candidate) -> Optional[float]:
    """Volume, diversité d'auteurs, engagement, modulés par l'accélération.

    Le nombre brut de mentions est un mauvais signal isolé : 200 tweets de
    3 comptes est une ferme à bots, pas de l'engouement.
    """
    mentions = candidate.social_mentions_1h
    if mentions is None:
        return None

    volume = _clamp(100 * mentions / SOCIAL_SATURATION_MENTIONS)

    authors = candidate.social_unique_authors
    diversity = _clamp(100 * authors / SOCIAL_SATURATION_AUTHORS) if authors is not None else volume

    engagement = candidate.social_engagement
    engagement_score = (
        _clamp(100 * math.log10(1 + engagement) / math.log10(SOCIAL_SATURATION_ENGAGEMENT))
        if engagement
        else 0.0
    )

    score = 0.40 * volume + 0.35 * diversity + 0.25 * engagement_score

    sample = candidate.social_sample_size
    if sample and authors is not None:
        score *= 0.5 + 0.5 * min((authors / sample) / SPAM_RATIO_FLOOR, 1.0)

    velocity = candidate.social_velocity_15m
    if velocity is not None and mentions > 0:
        score *= 0.75 + 0.25 * min(velocity * 4 / mentions, 2.0)

    return _clamp(score)


def smart_money_absolute(candidate: Candidate) -> Optional[float]:
    if candidate.smart_money_buys_30m is None:
        return None
    return _clamp(100 * candidate.smart_money_buys_30m / SMART_MONEY_SATURATION_BUYS)


def rugcheck_absolute(candidate: Candidate) -> Optional[float]:
    """Déjà une échelle absolue 0-100 (sûreté), sans normalisation batch."""
    return None if candidate.rugcheck_score is None else _clamp(candidate.rugcheck_score)


# Composant -> (fonction de score absolu, valeur utilisée pour le rang relatif)
COMPONENTS = {
    "liquidity": (liquidity_absolute, lambda c: c.liquidity_usd),
    "volume_momentum": (volume_momentum_absolute, lambda c: c.volume_liquidity_ratio),
    "social_sentiment": (social_absolute, lambda c: float(c.social_mentions_1h or 0)),
    "smart_money": (smart_money_absolute, lambda c: float(c.smart_money_buys_30m or 0)),
    # La sûreté ne se compare pas au batch : un token sûr l'est dans l'absolu.
    "rugcheck": (rugcheck_absolute, None),
}


def score_candidates(
    candidates: Sequence[Candidate], weights: dict[str, float]
) -> list[Candidate]:
    """Calcule les deux scores de chaque candidat. Retourne une liste triée."""
    if not candidates:
        return []

    batches: dict[str, list[float]] = {}
    for name, (score_fn, rank_fn) in COMPONENTS.items():
        if rank_fn is None:
            continue
        batches[name] = [rank_fn(c) for c in candidates if score_fn(c) is not None]

    scored: list[Candidate] = []
    for candidate in candidates:
        absolutes: dict[str, float] = {}
        blended: dict[str, float] = {}

        for name, (score_fn, rank_fn) in COMPONENTS.items():
            value = score_fn(candidate)
            if value is None:
                continue
            absolutes[name] = value
            if rank_fn is None:
                blended[name] = value
            else:
                blended[name] = _clamp(
                    ABSOLUTE_WEIGHT * value
                    + RELATIVE_WEIGHT * _relative(rank_fn(candidate), batches[name])
                )

        total_weight = sum(weights.get(k, 0.0) for k in absolutes)
        if total_weight <= 0:
            alpha = alpha_absolute = 0.0
        else:
            alpha = sum(v * weights.get(k, 0.0) for k, v in blended.items()) / total_weight
            alpha_absolute = (
                sum(v * weights.get(k, 0.0) for k, v in absolutes.items()) / total_weight
            )

        sub_scores = {k: round(v, 1) for k, v in blended.items()}
        sub_scores["_weights_used"] = round(total_weight, 3)
        scored.append(
            candidate.with_fields(
                alpha_score=round(alpha, 2),
                alpha_score_absolute=round(alpha_absolute, 2),
                sub_scores=sub_scores,
            )
        )

    scored.sort(key=lambda c: c.alpha_score, reverse=True)
    return scored
