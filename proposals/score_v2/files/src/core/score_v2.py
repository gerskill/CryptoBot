"""SCORE V2 (PROPOSITION 2026-10-10) — probabilité de trade gagnant, anti-pump.

POURQUOI. Le score alpha v1 est anti-prédictif sur nos propres fills
(docs/score_calibration_2026-10-10.md) : il récompense le momentum 5 min et le
ratio volume/liquidité, c'est-à-dire qu'il achète les tokens qui viennent de
pomper. Ce sont justement ceux qui finissent le plus souvent au stop.

CE QUE C'EST. Une régression logistique à DEUX variables, poids explicites,
ajustée sur la période d'entraînement (2026-08-06 → 2026-09-24, 909 entrées
uniques token+heure) et validée hors échantillon (2026-09-24 → 2026-10-10,
606 entrées uniques) : AUC test 0,62 [IC95 0,57–0,68] pour « PnL net > 0 ».

CE QUE CE N'EST PAS. Ni un edge (aucune tranche ne passe PF > 1 de façon
significative), ni une probabilité assez bien calibrée pour la TAILLE :
les probabilités vont de ~0,14 à ~0,27, Brier à peine meilleur que le taux de
base. Usage autorisé : FILTRE d'entrée sur un bras de test PAPER, opt-in via
`entry_rules.score_v2_min_prob` (> 0). Aucun bras existant n'est touché.

Entrées : uniquement des données connues AVANT la décision (Candidate).
"""

import math
from typing import Any

# Poids explicites (logit). Ajustés sur la période d'entraînement uniquement.
INTERCEPT = -1.2065
W_PRICE_CHANGE_5M = -0.004636   # par point de % de variation 5 min
W_LOG10_VOL_LIQ = -0.115594     # par décade de volume 1h / liquidité

# Bornes identiques à l'entraînement : un pump à +3000 % ne doit pas peser
# 30 fois plus qu'un pump à +100 %.
PRICE_CHANGE_5M_CLIP = (-50.0, 100.0)
VOL_LIQ_FLOOR = 0.01

# Médiane de la probabilité sur l'entraînement (= « garder la moitié haute »).
DEFAULT_MIN_PROB = 0.205


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def score_v2_logit(price_change_5m: float, volume_liquidity_ratio: float) -> float:
    momentum = _clip(float(price_change_5m or 0.0), *PRICE_CHANGE_5M_CLIP)
    ratio = max(float(volume_liquidity_ratio or 0.0), VOL_LIQ_FLOOR)
    return (
        INTERCEPT
        + W_PRICE_CHANGE_5M * momentum
        + W_LOG10_VOL_LIQ * math.log10(ratio)
    )


def score_v2_probability(candidate: Any) -> float:
    """Probabilité (non calibrée finement) que le trade finisse en PnL net > 0."""
    z = score_v2_logit(
        getattr(candidate, "price_change_5m", 0.0),
        getattr(candidate, "volume_liquidity_ratio", 0.0),
    )
    return 1.0 / (1.0 + math.exp(-z))


def score_v2_gate(candidate: Any, min_prob: float) -> tuple[bool, float, str]:
    """(passe, probabilité, raison). `min_prob` <= 0 = porte désactivée."""
    prob = score_v2_probability(candidate)
    if not min_prob or min_prob <= 0:
        return True, prob, ""
    if prob >= min_prob:
        return True, prob, ""
    return False, prob, f"score_v2 {prob:.3f} < {min_prob:.3f}"
