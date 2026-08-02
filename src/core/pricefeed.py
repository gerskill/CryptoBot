"""Cache de marché pour UN tick de monitoring. Zéro I/O, mémoire pure.

LE PROBLÈME QU'IL RÉSOUT. Le monitoring interroge le prix par POSITION. Avec
7 stratégies pouvant détenir le même token, le même prix serait demandé 7 fois
par tick, 12 ticks par minute — 84 requêtes pour une seule information.
DexScreener plafonne à 270 effectives par minute et son limiteur BLOQUE au
lieu d'échouer : dépasser n'affiche aucune erreur, ça étire silencieusement le
tick de 5 s, ce qui ressort en dépassement de stop loss.

CE N'EST PAS QU'UNE OPTIMISATION. `PriceHistory.record_raw` alimente la deque
que lit `liquidity_drop_pct` pour détecter les rugs. Sept snapshots identiques
au même horodatage fausseraient la fenêtre de détection. Un enregistrement par
TOKEN par tick est une exigence de correction, pas d'économie.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Optional

# Part du quota DexScreener réservée au monitoring. Le reste va à la
# découverte et au rafraîchissement des rejets suivis.
MONITOR_BUDGET_PER_MIN = 180


@dataclass
class CycleMarketCache:
    """Prix, liquidité et verdict technique mémoïsés pour un tick.

    `reset()` est explicite et non optionnel : pas de TTL, pas d'horloge, pas
    de thread. Un cache qui expire tout seul pendant un tick rendrait deux
    prix différents pour la même position dans la même itération.
    """

    market: dict[str, tuple[Optional[float], Optional[float]]] = field(default_factory=dict)
    technical: dict[str, Any] = field(default_factory=dict)
    _tokens: dict[str, tuple[Optional[float], Optional[float]]] = field(default_factory=dict)

    def reset(self) -> None:
        self.market.clear()
        self._tokens.clear()

    def reset_cycle(self) -> None:
        """Le verdict technique vit un CYCLE (90 s), pas un tick (5 s) :
        il coûte une requête Birdeye à 1 req/s."""
        self.technical.clear()

    @staticmethod
    def key(chain: str, pair_address: Optional[str], token_address: str) -> str:
        """Clé par PAIRE : c'est l'unité que DexScreener facture."""
        return f"{chain}:{pair_address}" if pair_address else f"token:{token_address}"

    def put(
        self,
        chain: str,
        pair_address: Optional[str],
        token_address: str,
        price: Optional[float],
        liquidity: Optional[float],
    ) -> None:
        self.market[self.key(chain, pair_address, token_address)] = (price, liquidity)
        if price:
            self._tokens[token_address] = (price, liquidity)

    def get(
        self, chain: str, pair_address: Optional[str], token_address: str
    ) -> tuple[Optional[float], Optional[float]]:
        return self.market.get(self.key(chain, pair_address, token_address), (None, None))

    def price(self, token_address: str) -> Optional[float]:
        return self._tokens.get(token_address, (None, None))[0]

    def by_token(self) -> list[tuple[str, Optional[float], Optional[float]]]:
        """Une entrée par TOKEN — ce que l'historique de prix doit recevoir."""
        return [(addr, p, liq) for addr, (p, liq) in self._tokens.items()]

    @property
    def size(self) -> int:
        return len(self.market)


def effective_monitor_interval(
    base_interval: float, distinct_pairs: int, budget_per_min: int = MONITOR_BUDGET_PER_MIN
) -> float:
    """Allonge le tick si le nombre de paires dépasse le budget de requêtes.

    Garde EXPLICITE plutôt qu'un calcul de tête : le limiteur DexScreener
    bloque silencieusement, donc un dépassement ne se voit nulle part — il se
    manifeste seulement plus tard, en stops qui sortent trop bas.
    """
    if distinct_pairs <= 0 or base_interval <= 0:
        return base_interval
    calls_per_min = (60.0 / base_interval) * distinct_pairs
    if calls_per_min <= budget_per_min:
        return base_interval
    return round(math.ceil(60.0 * distinct_pairs / budget_per_min), 1)
