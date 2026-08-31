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

import os
from dataclasses import dataclass
from typing import Any, Optional, Sequence

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


# ---------------------------------------------------------------------------
# JAMBES PARTIELLES — le trou que l'ADR 009 laissait ouvert (voir ADR 012).
#
# L'ADR 009 ne facture le coût qu'une fois, sur la jambe finale, parce qu'un
# devis Jupiter coûte une requête et que Jupiter plafonne à 1 req/s. La
# conséquence était écrite noir sur blanc dans CONTEXT.md : « le P&L papier
# reste partiellement optimiste » — un trade sorti en TP1 + TP2 + TP3 ne payait
# que la dernière de ses trois ventes.
#
# La correction ne consiste PAS à interroger Jupiter à chaque jambe (ça
# violerait l'ADR 009 et le budget d'appels). Elle consiste à appliquer aux
# jambes partielles la MÉDIANE DES COÛTS DÉJÀ MESURÉS sur les jambes finales
# du même bras, et à marquer la ligne de journal comme estimée pour qu'aucune
# analyse ne confonde jamais les deux.
#
# POURQUOI LA MOITIÉ. `round_trip_cost_pct` mesure un aller-retour : l'impact
# d'achat PLUS l'impact de vente. La jambe finale porte volontairement le
# round-trip complet, puisque l'entrée n'est facturée nulle part ailleurs
# (voir l'en-tête de ce module). Facturer un round-trip complet à chaque jambe
# partielle facturerait donc l'achat deux ou trois fois. Une vente partielle ne
# paie que sa jambe de vente : à défaut d'un devis unidirectionnel — `/swap`
# reste hors de ALLOWED_PATHS — la moitié est le partage le moins faux, et il
# est déclaré comme estimation, pas comme mesure.
#
# POURQUOI 10 ÉCHANTILLONS. Même plancher que `MIN_SEGMENT_SAMPLE` dans
# `learning.py`. Sous ce seuil, la médiane d'un coût de sortie est du bruit :
# on préfère le prix nu, c'est-à-dire l'ancien comportement, à un chiffre
# inventé. « Pas assez de données pour estimer » n'est pas « coût nul ».

MIN_SAMPLE_FOR_PARTIAL_ESTIMATE = 10
SELL_LEG_SHARE = 0.5


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    milieu = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[milieu]
    return (ordered[milieu - 1] + ordered[milieu]) / 2


def estimated_partial_cost_pct(
    measured_round_trips: Sequence[float],
    min_sample: int = MIN_SAMPLE_FOR_PARTIAL_ESTIMATE,
) -> Optional[float]:
    """Coût à déduire d'une VENTE PARTIELLE, estimé sur les coûts déjà mesurés.

    `measured_round_trips` ne doit contenir QUE des coûts réellement mesurés
    (jambes finales avec devis Jupiter abouti). Y réinjecter des estimations
    ferait converger l'estimation sur elle-même — une boucle qui se confirme.

    Retourne `None` tant qu'il n'y a pas `min_sample` mesures : l'appelant
    garde alors le prix nu, comme avant cette correction.
    """
    propres = [c for c in measured_round_trips if c is not None and c > 0]
    if len(propres) < min_sample:
        return None
    return round(_median(propres) * SELL_LEG_SHARE, 3)


class PartialCostEstimator:
    """Estimateur par bras, adossé au journal de ce bras.

    Relit le journal seulement quand son fichier a bougé : une jambe partielle
    ne doit pas coûter une relecture complète du journal à chaque évaluation
    de sortie, qui tourne toutes les 5 secondes.
    """

    def __init__(self, journal: Any, min_sample: int = MIN_SAMPLE_FOR_PARTIAL_ESTIMATE):
        self.journal = journal
        self.min_sample = min_sample
        self._cache: Optional[float] = None
        self._signature: Optional[tuple] = None

    def _file_signature(self) -> Optional[tuple]:
        path = getattr(self.journal, "path", None)
        if not path:
            return None
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def __call__(self) -> Optional[float]:
        signature = self._file_signature()
        if signature is not None and signature == self._signature:
            return self._cache
        try:
            rows = self.journal.read_all()
        except Exception:  # noqa: BLE001
            # Un journal illisible ne doit pas empêcher une clôture : on
            # retombe sur le prix nu, jamais sur un coût deviné.
            return None
        mesures = [
            row.get("exit_cost_pct")
            for row in rows
            if row.get("exit_cost_pct") is not None
            and not row.get("exit_cost_estimated")
        ]
        self._cache = estimated_partial_cost_pct(mesures, self.min_sample)
        self._signature = signature
        return self._cache
