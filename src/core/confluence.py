"""Confluence entre stratégies et arbitrage du quota social.

Fonctions PURES, aucune I/O : elles prennent les évaluations d'un cycle et
rendent des décisions. Testables sans réseau, sans fichier, sans horloge.

Deux questions distinctes y sont traitées :
  - qui, parmi les tokens souhaités par plusieurs bras, mérite la mesure
    Twitter que le budget mensuel nous laisse (`merge_social_wishlists`) ;
  - sur quels tokens les bras tombent d'accord (`build_confluence`).
"""

from dataclasses import dataclass, field
from typing import Any, Optional

from src.core.signals import Signal, Vote, abstain


@dataclass(frozen=True)
class ConfluenceRow:
    """Ce que les bras pensent d'un token, sans lien avec ce qu'ils en feront."""

    token_address: str
    symbol: str = ""
    # Bras dont TOUS les filtres passent.
    accepted_by: tuple[str, ...] = ()
    # Sous-ensemble dont le score dépasse aussi le seuil d'entrée : c'est
    # celui-là qui compte pour le bras consensus. Passer les filtres sans
    # atteindre le seuil n'est pas un vote d'achat.
    above_threshold_by: tuple[str, ...] = ()
    scores_by_arm: dict[str, float] = field(default_factory=dict)

    @property
    def accepted_count(self) -> int:
        return len(self.accepted_by)

    @property
    def above_threshold_count(self) -> int:
        return len(self.above_threshold_by)

    def as_dict(self) -> dict[str, Any]:
        return {
            "token_address": self.token_address,
            "symbol": self.symbol,
            "accepted_by": list(self.accepted_by),
            "above_threshold_by": list(self.above_threshold_by),
            "accepted_count": self.accepted_count,
            "above_threshold_count": self.above_threshold_count,
            "scores_by_arm": dict(self.scores_by_arm),
        }


def merge_social_wishlists(
    wishlists: dict[str, tuple[str, ...]], limit: int
) -> tuple[str, ...]:
    """Arbitre le quota Twitter entre les listes de souhaits des bras.

    Classement : d'abord le nombre de bras demandeurs, ensuite le meilleur
    rang obtenu chez un bras. Un token que deux stratégies veulent
    indépendamment est un meilleur usage d'une requête à quota mensuel que le
    premier choix d'une seule.

    Conséquence assumée : les bras ne sont pas parfaitement isolés sur l'axe
    social — la couverture de l'un dépend de ce que les autres ont demandé.
    C'est le prix d'un budget partagé. Un bras non servi garde
    `social_mentions_1h = None`, ce qui ne rejette pas et redistribue le poids
    social ; il faut juste le SAVOIR au moment de comparer les bras.
    """
    if limit <= 0:
        return ()

    demand: dict[str, int] = {}
    best_rank: dict[str, int] = {}
    for addresses in wishlists.values():
        for rank, address in enumerate(addresses):
            demand[address] = demand.get(address, 0) + 1
            if address not in best_rank or rank < best_rank[address]:
                best_rank[address] = rank

    ordered = sorted(demand, key=lambda a: (-demand[a], best_rank[a], a))
    return tuple(ordered[:limit])


def arm_signals(
    evaluations: dict[str, Any],
    token_address: str,
    thresholds: Optional[dict[str, float]] = None,
    voters: Optional[set[str]] = None,
) -> list[Signal]:
    """Le verdict de chaque bras sur un token, en signaux votables.

    Un bras EST un agent : il examine un candidat et rend un avis motivé.
    Cette fonction traduit ce que l'entonnoir enregistre déjà en objets
    `Signal`, pour que le quorum avec abstention s'applique aux stratégies
    sans qu'il faille inventer une couche d'agents séparée.

    L'ABSTENTION EST LE POINT CLÉ. Un bras dont la fenêtre d'âge ne couvre
    même pas ce token n'a pas d'avis — il s'abstient. Le compter comme un
    NON ferait qu'un token vu par un seul bras n'atteindrait jamais aucun
    quorum, ce qui est exactement ce qui bloque la confluence aujourd'hui :
    2 accords sur 81 évaluations, parce que les fenêtres sont disjointes.
    """
    thresholds = thresholds or {}
    signals: list[Signal] = []

    for name, evaluation in evaluations.items():
        if voters is not None and name not in voters:
            continue
        retenu = next(
            (c for c in evaluation.result.candidates if c.token_address == token_address),
            None,
        )
        if retenu is not None:
            seuil = thresholds.get(name, 0.0)
            au_dessus = retenu.alpha_score_absolute >= seuil
            signals.append(
                Signal(
                    agent=name,
                    vote=Vote.YES if au_dessus else Vote.NO,
                    reason=(
                        f"alpha {retenu.alpha_score_absolute:.0f}"
                        f"{'≥' if au_dessus else '<'}{seuil:g}"
                    ),
                    score=retenu.alpha_score_absolute,
                )
            )
            continue

        rejete = next(
            (c for c in evaluation.result.rejected if c.token_address == token_address),
            None,
        )
        if rejete is None:
            # Hors de sa fenêtre : le bras n'a jamais examiné ce token.
            signals.append(abstain(name, "hors de sa fenêtre"))
            continue

        motif = str(rejete.rejected_reason or "")
        # Un rejet sur l'ÂGE ou la LIQUIDITÉ dit « ce token n'est pas pour
        # moi », pas « ce token est mauvais ». C'est une abstention. Un rejet
        # sur la sécurité ou la manipulation est un vrai avis négatif.
        if motif.startswith(("âge", "liquidité", "market cap", "volume")):
            signals.append(abstain(name, motif))
        else:
            securite = any(
                mot in motif
                for mot in ("risque critique", "honeypot", "wash", "authority", "rugcheck")
            )
            signals.append(
                Signal(agent=name, vote=Vote.NO, reason=motif, veto=securite)
            )

    return signals


def build_confluence(
    evaluations: dict[str, Any],
    voters: Optional[set[str]] = None,
    thresholds: Optional[dict[str, float]] = None,
) -> dict[str, ConfluenceRow]:
    """Agrège les verdicts des bras, token par token.

    `voters` restreint le décompte aux bras votants : inclure le bras
    consensus dans son propre quorum le rendrait auto-référentiel, il se
    validerait tout seul dès qu'il accepte un token.
    """
    thresholds = thresholds or {}
    accepted: dict[str, list[str]] = {}
    above: dict[str, list[str]] = {}
    scores: dict[str, dict[str, float]] = {}
    symbols: dict[str, str] = {}

    for name, evaluation in evaluations.items():
        if voters is not None and name not in voters:
            continue
        threshold = thresholds.get(name, 0.0)
        for candidate in evaluation.result.candidates:
            address = candidate.token_address
            symbols.setdefault(address, candidate.symbol)
            accepted.setdefault(address, []).append(name)
            scores.setdefault(address, {})[name] = candidate.alpha_score_absolute
            if candidate.alpha_score_absolute >= threshold:
                above.setdefault(address, []).append(name)

    return {
        address: ConfluenceRow(
            token_address=address,
            symbol=symbols.get(address, ""),
            accepted_by=tuple(names),
            above_threshold_by=tuple(above.get(address, ())),
            scores_by_arm=scores.get(address, {}),
        )
        for address, names in accepted.items()
    }


def top_confluence(rows: dict[str, ConfluenceRow], limit: int = 15) -> list[dict[str, Any]]:
    """Les tokens les plus consensuels, prêts pour `state.json`."""
    ordered = sorted(
        rows.values(),
        key=lambda row: (row.above_threshold_count, row.accepted_count),
        reverse=True,
    )
    return [row.as_dict() for row in ordered[:limit]]
