"""Volatilité réalisée — l'écart-type des rendements, et son unité.

CE QUE ÇA MESURE. L'écart-type des rendements logarithmiques entre snapshots
consécutifs, ramené à une base horaire. Une volatilité de 40 %/h signifie
qu'à une déviation standard, le prix bouge de ±40 % en une heure.

POURQUOI DES RENDEMENTS LOGARITHMIQUES et non des variations en pourcentage :
sur ces amplitudes la différence cesse d'être cosmétique. Un token qui fait
+100 % puis −50 % revient à son prix ; en pourcentages simples la moyenne des
variations vaut +25 %, en logarithmique elle vaut 0. Sur des trajectoires dont
le pic médian est +4,9 % mais dont 15 % dépassent +100 %, le biais des
pourcentages simples n'est pas négligeable.

POURQUOI RAMENER À L'HEURE. Les snapshots arrivent à cadence de cycle (90 s)
mais le monitoring d'une position tourne à 5 s : deux séries de la même
volatilité réelle donneraient des écarts-types dans un rapport de √18. Sans
normalisation, comparer un token surveillé à un token seulement scanné
comparerait des cadences d'échantillonnage, pas des marchés.

CE QUE ÇA NE MESURE PAS. La volatilité réalisée est rétrospective : elle dit
ce qui a bougé, pas ce qui va bouger. Sur un token de moins de six heures elle
est de surcroît dominée par la phase de lancement. Cet agent MESURE, il ne
dimensionne rien — le sizing reste à `economics.size_for_cost`, qui s'appuie
sur un devis Jupiter réel plutôt que sur une extrapolation.
"""

import math
import statistics
import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from src.agents import _journal
from src.analysis.technical import Snapshot

SECONDS_PER_HOUR = 3600.0
# Deux rendements suffisent arithmétiquement à un écart-type, et ne disent
# rien. Huit points, à 90 s de cycle, couvrent une dizaine de minutes.
MIN_RETURNS = 8
# Au-delà, ce n'est plus de la volatilité, c'est un lancement ou une erreur de
# prix. On garde la valeur mais on la signale : l'écrêter masquerait le cas.
EXTREME_HOURLY_PCT = 500.0


@dataclass(frozen=True)
class VolatilityReading:
    """Volatilité horaire en pourcentage, et ce qui la porte."""

    hourly_pct: Optional[float]
    samples: int
    median_interval_sec: Optional[float]
    extreme: bool
    reason: str

    @property
    def known(self) -> bool:
        return self.hourly_pct is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "volatility_hourly_pct": (
                round(self.hourly_pct, 2) if self.hourly_pct is not None else None
            ),
            "samples": self.samples,
            "median_interval_sec": (
                round(self.median_interval_sec, 1)
                if self.median_interval_sec is not None else None
            ),
            "extreme": self.extreme,
            "reason": self.reason,
        }


def log_returns(snapshots: Sequence[Snapshot]) -> list[tuple[float, float]]:
    """(rendement logarithmique, intervalle en secondes) entre snapshots.

    Les prix nuls ou négatifs et les intervalles nuls sont écartés : ils
    viennent d'un flux dégradé, pas d'un mouvement de marché, et un `log(0)`
    ferait tomber la boucle — ce que l'invariant « la boucle ne meurt jamais »
    interdit de laisser passer jusque-là.
    """
    sortis = sorted(snapshots, key=lambda s: s.ts)
    out = []
    for avant, apres in zip(sortis, sortis[1:]):
        delta = apres.ts - avant.ts
        if delta <= 0 or avant.price <= 0 or apres.price <= 0:
            continue
        out.append((math.log(apres.price / avant.price), delta))
    return out


class VolatilityAgent:
    """Volatilité réalisée d'un token depuis ses snapshots. Ne dimensionne rien."""

    def __init__(self, log_path: Optional[str] = None, min_returns: int = MIN_RETURNS):
        self.log_path = log_path
        self.min_returns = min_returns

    def read(self, snapshots: Sequence[Snapshot]) -> VolatilityReading:
        paires = log_returns(snapshots)
        if len(paires) < self.min_returns:
            return VolatilityReading(
                hourly_pct=None, samples=len(paires), median_interval_sec=None,
                extreme=False,
                reason=(
                    f"{len(paires)} rendements exploitables, il en faut "
                    f"{self.min_returns}"
                ),
            )

        rendements = [r for r, _ in paires]
        intervalles = [d for _, d in paires]
        intervalle_median = statistics.median(intervalles)

        # Écart-type par pas d'échantillonnage, puis mise à l'échelle horaire
        # en √temps — la volatilité d'un processus à accroissements
        # indépendants croît comme la racine de l'horizon, pas linéairement.
        ecart_par_pas = statistics.stdev(rendements)
        pas_par_heure = SECONDS_PER_HOUR / intervalle_median
        horaire = ecart_par_pas * math.sqrt(pas_par_heure)
        pourcentage = 100.0 * horaire

        return VolatilityReading(
            hourly_pct=pourcentage,
            samples=len(paires),
            median_interval_sec=intervalle_median,
            extreme=pourcentage > EXTREME_HOURLY_PCT,
            reason=(
                f"écart-type de {len(paires)} rendements log, échantillonnés "
                f"toutes les {intervalle_median:.0f} s, ramenés à l'heure en √t"
            ),
        )

    def observe(
        self,
        token_address: str,
        symbol: str,
        snapshots: Sequence[Snapshot],
        extra: Optional[dict[str, Any]] = None,
    ) -> VolatilityReading:
        """Lit et journalise, lecture inconnue comprise.

        Comme pour le RSI : ne garder que les lectures connues empêcherait plus
        tard de distinguer « jamais volatil » de « jamais mesuré ».

        `extra` porte le contexte du trade. Une volatilité sans son P&L ne dit
        rien — c'est leur mise en regard qui répondra à « le stop est-il trop
        serré pour ce régime de volatilité ? ».
        """
        reading = self.read(snapshots)
        if self.log_path:
            _journal.append(self.log_path, {
                "ts": time.time(),
                "token_address": token_address,
                "symbol": symbol,
                **reading.as_dict(),
                **(extra or {}),
            })
        return reading
