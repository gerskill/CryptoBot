"""Probabilités estimées à partir des bougies du sous-jacent.

Trois estimateurs, tous sans dérive supposée et sans paramètre ajusté sur le
résultat des paris. Chacun renvoie `None` quand l'historique ne suffit pas :
pas d'estimation vaut mieux qu'une estimation inventée.
"""

import math
from typing import Optional

MIN_WINDOWS = 50


def horizon_returns(closes: list[float], steps: int) -> list[float]:
    """Rendements logarithmiques sur `steps` bougies, fenêtres chevauchantes."""
    if steps < 1 or len(closes) <= steps:
        return []
    return [math.log(closes[i + steps] / closes[i]) for i in range(len(closes) - steps)]


def prob_above_empirical(spot: float, target: float, closes: list[float], steps: int) -> Optional[float]:
    """Part des fenêtres passées qui, rejouées depuis `spot`, finissent au-dessus de `target`."""
    returns = horizon_returns(closes, steps)
    if len(returns) < MIN_WINDOWS or spot <= 0 or target <= 0:
        return None
    needed = math.log(target / spot)
    return sum(1 for r in returns if r > needed) / len(returns)


def prob_above_lognormal(spot: float, target: float, closes: list[float], steps: int) -> Optional[float]:
    """Loi log-normale, volatilité réalisée des bougies, mise à l'échelle en racine du temps."""
    one_step = horizon_returns(closes, 1)
    if len(one_step) < MIN_WINDOWS or spot <= 0 or target <= 0 or steps < 1:
        return None
    mean = sum(one_step) / len(one_step)
    variance = sum((r - mean) ** 2 for r in one_step) / (len(one_step) - 1)
    sigma = math.sqrt(variance * steps)
    if sigma <= 0:
        return None
    z = math.log(target / spot) / sigma
    return 0.5 * math.erfc(z / math.sqrt(2))


def prob_touch(spot: float, target: float, closes: list[float], steps: int) -> Optional[float]:
    """Probabilité de TOUCHER `target` avant l'échéance.

    Principe de réflexion : toucher ≈ 2 × finir au-delà, plafonné à 1.
    Approximation, valable sans dérive ; à comparer aux règlements réels avant
    de lui faire confiance.
    """
    above = prob_above_lognormal(spot, target, closes, steps)
    if above is None:
        return None
    beyond = above if target >= spot else 1 - above
    return min(1.0, 2 * beyond)
