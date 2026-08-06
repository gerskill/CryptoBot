"""Coût réel d'une sortie — impact de prix + priority fee, mesurés, pas devinés.

POURQUOI CE MODULE EXISTE. `PaperPortfolio` calcule le P&L réalisé sur le prix
SEUL (`position.pnl_pct(price)`) — `SLIPPAGE_NOTE` dans `portfolio.py` le dit
depuis le début : « P&L papier sans slippage ni frais ». `round_trip_cost_pct`
existait déjà mais ne servait qu'à FILTRER l'entrée (`economics.evaluate`) et
DIMENSIONNER la position (`size_for_cost`) — jamais à corriger le P&L réalisé.
Ce module ferme cet écart : le but posé par le propriétaire est que le PAPER
ressemble « au plus juste » au réel, pour que les 6 bras s'améliorent sur des
chiffres honnêtes plutôt que sur un prix nu.

DEUX COMPOSANTES, DEUX SOURCES :

  impact de prix   `jupiter.round_trip_cost_pct` — devis RÉEL, déjà utilisé
                    partout ailleurs dans le dépôt (médiane 3,06 % mesurée).
  priority fee      `helius.get_recent_prioritization_fee_lamports` — NOUVEAU.
                    Prix par unité de calcul mesuré sur les derniers slots,
                    multiplié par un budget de CU HYPOTHÉTIQUE (le vrai budget
                    n'est connu qu'à la construction de la transaction, hors
                    périmètre : `/swap/v2/build` reste absent d'ALLOWED_PATHS).

POURQUOI LE ROUND-TRIP COMPLET, PAS LA SEULE JAMBE DE VENTE. L'entrée n'est
JAMAIS facturée ailleurs dans le dépôt — `round_trip_cost_pct` n'y sert qu'à
FILTRER, jamais à déduire un coût réalisé. Facturer ici l'aller-retour complet
(achat + vente) au moment de la sortie est le choix qui évite de sous-compter
le coût total du trade sur toute sa durée de vie ; le répartir entre l'entrée
et la sortie demanderait de toucher aussi le chemin d'entrée, hors périmètre
de cette demande.

DONNÉE ABSENTE NE REJETTE NI NE BLOQUE JAMAIS — même invariant que le reste
du pipeline. Si une seule des deux composantes est mesurable, elle est quand
même déduite, et `reason` dit laquelle manque. Rien n'est jamais inventé pour
combler l'absence.
"""

from dataclasses import dataclass
from typing import Any, Optional

# Sous ce prix SOL, une conversion lamports -> USD serait un artefact
# numérique (division par un nombre proche de zéro) plutôt qu'une mesure.
MIN_SOL_PRICE_USD = 0.01


@dataclass(frozen=True)
class ExitCost:
    """Coût mesuré d'une sortie, et ce qui a pu être mesuré ou non."""

    price_impact_pct: Optional[float]
    priority_fee_usd: Optional[float]
    total_cost_pct: float
    reason: str

    @property
    def partial(self) -> bool:
        return self.price_impact_pct is None or self.priority_fee_usd is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "price_impact_pct": (
                round(self.price_impact_pct, 3) if self.price_impact_pct is not None else None
            ),
            "priority_fee_usd": (
                round(self.priority_fee_usd, 4) if self.priority_fee_usd is not None else None
            ),
            "total_cost_pct": round(self.total_cost_pct, 3),
            "partial": self.partial,
            "reason": self.reason,
        }


def measure_exit_cost(
    jupiter: Any,
    helius: Any,
    token_address: str,
    size_usd: float,
    sol_price_usd: float,
) -> Optional[ExitCost]:
    """Coût réel de clôturer `size_usd` de `token_address`, maintenant.

    `None` seulement si RIEN n'est mesurable des deux côtés — dans ce cas
    l'appelant garde le P&L au prix nu plutôt que de deviner un coût.
    """
    if size_usd <= 0:
        return None

    price_impact_pct: Optional[float] = None
    if jupiter is not None and getattr(jupiter, "enabled", False):
        try:
            price_impact_pct = jupiter.round_trip_cost_pct(
                token_address, size_usd, sol_price_usd
            )
        except Exception:  # noqa: BLE001
            # Une mesure de coût ne doit jamais faire tomber une clôture de
            # position réelle. Voir l'invariant « la boucle ne meurt jamais ».
            price_impact_pct = None

    priority_fee_usd: Optional[float] = None
    if (
        helius is not None
        and getattr(helius, "enabled", False)
        and sol_price_usd >= MIN_SOL_PRICE_USD
    ):
        try:
            lamports = helius.get_recent_prioritization_fee_lamports()
        except Exception:  # noqa: BLE001
            lamports = None
        if lamports is not None:
            priority_fee_usd = (lamports / 1_000_000_000) * sol_price_usd

    if price_impact_pct is None and priority_fee_usd is None:
        return None

    priority_fee_pct = (
        100.0 * priority_fee_usd / size_usd if priority_fee_usd is not None else 0.0
    )
    total = (price_impact_pct or 0.0) + priority_fee_pct

    manque = []
    if price_impact_pct is None:
        manque.append("impact de prix (devis Jupiter indisponible)")
    if priority_fee_usd is None:
        manque.append("priority fee (RPC Helius indisponible)")
    reason = (
        f"coût mesuré {total:.2f}% (impact {price_impact_pct if price_impact_pct is not None else '?'}%"
        f" + priority ${priority_fee_usd if priority_fee_usd is not None else '?'})"
        + (f" — manque : {', '.join(manque)}" if manque else "")
    )

    return ExitCost(
        price_impact_pct=price_impact_pct,
        priority_fee_usd=priority_fee_usd,
        total_cost_pct=round(total, 3),
        reason=reason,
    )
