"""Économie du trade : le coût d'entrée décide du take profit, pas l'inverse.

LE CONSTAT QUI JUSTIFIE CE MODULE. Mesuré le 2026-08-01 via des devis Jupiter
réels, pour une position de 20 $ :

    TOM         liquidité  57 000 $   aller-retour   2,32 %
    PWEACEJIMO  liquidité  22 074 $   aller-retour  23,62 %
    Willie      liquidité  13 716 $   aller-retour   2,11 %
    Buddy       liquidité  26 261 $   aller-retour   1,85 %

Le coût ne suit PAS la liquidité — il dépend de la profondeur réelle du
routage, qu'aucune heuristique ne devine. Il faut un devis.

CE QUE ÇA CHANGE POUR LE TAKE PROFIT. Un TP n'est pas une préférence, c'est
une conséquence. Sur 26 positions instrumentées, le pic médian est +4,9 % :
la stratégie « plein de petits gains » est la seule que la distribution
autorise… sauf qu'un aller-retour à 2,3 % mange la moitié d'un gain de 5 %,
et qu'à 23,6 % aucun petit gain n'existe. Le plancher du TP est donc FIXÉ par
le coût, pas choisi.

D'où les deux règles de ce module :
  - `minimum_viable_tp` : sous ce TP, la stratégie perd de l'argent même en
    gagnant ses trades.
  - `is_worth_trading` : si l'espérance nette est négative avant même
    d'entrer, aucun signal, aussi bon soit-il, ne rattrape la géométrie.

Ces deux règles répondent à « un TP fixe ou plein de petits gains ? » : les
deux sont valides, mais le coût mesuré du token décide lequel est ACCESSIBLE.
"""

from dataclasses import dataclass
from typing import Any, Optional

# Marge exigée au-dessus du seuil de rentabilité. Un TP qui rembourse
# exactement les frais n'est pas un gain, c'est du travail gratuit.
DEFAULT_EDGE_MARGIN = 1.5
# Au-delà, la position est ingérable quelle que soit la stratégie.
MAX_ACCEPTABLE_ROUND_TRIP_PCT = 8.0

# TAUX D'ATTEINTE MESURÉS sur les 26 positions instrumentées : proportion des
# trades dont le pic a franchi ce niveau. C'est l'a priori à utiliser tant
# qu'un bras n'a pas d'historique à lui.
#
# LE PIÈGE QUE ÇA ÉVITE. La garde économique demandait le win rate DU BRAS.
# Un bras neuf en a zéro, clampé à un plancher arbitraire de 10%, ce qui
# faisait exiger un TP1 de +52% à +63% — donc aucune entrée, donc jamais de
# win rate. Poule et œuf : le bras ne pouvait pas démarrer.
#
# Un a priori mesuré sur l'historique global est un bien meilleur point de
# départ qu'une constante inventée, et il est remplacé par le vécu du bras
# dès qu'il en a un.
TAUX_ATTEINTE_MESURES = {
    10: 0.42,
    25: 0.31,
    50: 0.23,
    100: 0.15,
    150: 0.04,
}
MIN_TRADES_POUR_WIN_RATE_PROPRE = 10


def expected_hit_rate(take_profit_pct: float) -> float:
    """A priori de réussite pour ce niveau de TP, interpolé sur la mesure."""
    niveaux = sorted(TAUX_ATTEINTE_MESURES)
    if take_profit_pct <= niveaux[0]:
        return TAUX_ATTEINTE_MESURES[niveaux[0]]
    if take_profit_pct >= niveaux[-1]:
        return TAUX_ATTEINTE_MESURES[niveaux[-1]]
    for bas, haut in zip(niveaux, niveaux[1:]):
        if bas <= take_profit_pct <= haut:
            ratio = (take_profit_pct - bas) / (haut - bas)
            return TAUX_ATTEINTE_MESURES[bas] + ratio * (
                TAUX_ATTEINTE_MESURES[haut] - TAUX_ATTEINTE_MESURES[bas]
            )
    return TAUX_ATTEINTE_MESURES[niveaux[-1]]


def win_rate_for(
    take_profit_pct: float, arm_win_rate: float, arm_trades: int
) -> tuple[float, str]:
    """Win rate à utiliser, et d'où il vient.

    Le vécu du bras l'emporte dès qu'il est significatif ; sinon l'a priori
    mesuré. Retourner la SOURCE permet de le dire dans le log plutôt que de
    faire passer une estimation pour une mesure.
    """
    if arm_trades >= MIN_TRADES_POUR_WIN_RATE_PROPRE:
        return arm_win_rate, f"vécu du bras sur {arm_trades} trades"
    attendu = expected_hit_rate(take_profit_pct)
    return attendu, f"a priori mesuré ({attendu:.0%} atteignent +{take_profit_pct:.0f}%)"


@dataclass(frozen=True)
class TradeEconomics:
    """Verdict économique d'une entrée, avant tout signal."""

    viable: bool
    reason: str
    round_trip_pct: Optional[float] = None
    expected_net_pct: Optional[float] = None
    minimum_tp_pct: Optional[float] = None
    configured_tp_pct: Optional[float] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "viable": self.viable,
            "reason": self.reason,
            "round_trip_pct": self.round_trip_pct,
            "expected_net_pct": self.expected_net_pct,
            "minimum_tp_pct": self.minimum_tp_pct,
            "configured_tp_pct": self.configured_tp_pct,
        }


def minimum_viable_tp(
    round_trip_pct: float,
    win_rate: float,
    partial_fraction: float = 0.5,
    rest_pct: float = -2.9,
    margin: float = DEFAULT_EDGE_MARGIN,
) -> float:
    """Le plus petit TP1 qui laisse une espérance positive après coûts.

    Le modèle suit le comportement réel du bot, pas une formule de manuel :
    au TP1 on vend `partial_fraction`, le reste sort autour de `rest_pct`
    (mesuré : -2,9 % via le stop breakeven). L'aller-retour se paie sur la
    position ENTIÈRE, gagnante ou perdante.

    Résoudre pour tp :
        win_rate * (f*tp + (1-f)*rest) - round_trip >= 0
    puis appliquer la marge.
    """
    win_rate = max(0.01, min(1.0, win_rate))
    f = max(0.01, min(1.0, partial_fraction))
    besoin = round_trip_pct / win_rate - (1 - f) * rest_pct
    return round(max(0.0, besoin / f) * margin, 1)


def expected_net_pct(
    take_profit_pct: float,
    win_rate: float,
    round_trip_pct: float,
    partial_fraction: float = 0.5,
    rest_pct: float = -2.9,
    loss_pct: Optional[float] = None,
) -> float:
    """Espérance par trade, coûts déduits, en points de pourcentage."""
    f = max(0.01, min(1.0, partial_fraction))
    win_rate = max(0.0, min(1.0, win_rate))
    gain = f * take_profit_pct + (1 - f) * rest_pct
    perte = loss_pct if loss_pct is not None else 0.0
    return round(win_rate * gain + (1 - win_rate) * perte - round_trip_pct, 2)


def evaluate(
    round_trip_pct: Optional[float],
    take_profit_pct: float,
    win_rate: float,
    partial_fraction: float = 0.5,
    rest_pct: float = -2.9,
    loss_pct: Optional[float] = None,
    max_round_trip_pct: float = MAX_ACCEPTABLE_ROUND_TRIP_PCT,
    margin: float = DEFAULT_EDGE_MARGIN,
) -> TradeEconomics:
    """Ce trade peut-il gagner de l'argent, indépendamment du signal ?

    `round_trip_pct = None` (pas de devis) ne bloque PAS : même invariant que
    le reste du pipeline, une donnée absente ne rejette jamais. Mais le dire,
    pour que « non mesuré » ne se confonde pas avec « gratuit ».
    """
    if round_trip_pct is None:
        return TradeEconomics(
            viable=True,
            reason="coût non mesuré — aucun devis disponible, entrée non bloquée",
            configured_tp_pct=take_profit_pct,
        )

    if round_trip_pct > max_round_trip_pct:
        return TradeEconomics(
            viable=False,
            reason=(
                f"aller-retour {round_trip_pct:.1f}% > {max_round_trip_pct:.0f}% — "
                f"le carnet est trop mince pour cette taille"
            ),
            round_trip_pct=round_trip_pct,
            configured_tp_pct=take_profit_pct,
        )

    plancher = minimum_viable_tp(
        round_trip_pct, win_rate, partial_fraction, rest_pct, margin
    )
    net = expected_net_pct(
        take_profit_pct, win_rate, round_trip_pct, partial_fraction, rest_pct, loss_pct
    )

    if take_profit_pct < plancher:
        return TradeEconomics(
            viable=False,
            reason=(
                f"TP +{take_profit_pct:.0f}% sous le plancher +{plancher:.0f}% "
                f"imposé par {round_trip_pct:.1f}% de frais à {win_rate:.0%} de réussite"
            ),
            round_trip_pct=round_trip_pct,
            expected_net_pct=net,
            minimum_tp_pct=plancher,
            configured_tp_pct=take_profit_pct,
        )

    return TradeEconomics(
        viable=True,
        reason=f"espérance nette {net:+.2f} pts, frais {round_trip_pct:.1f}%",
        round_trip_pct=round_trip_pct,
        expected_net_pct=net,
        minimum_tp_pct=plancher,
        configured_tp_pct=take_profit_pct,
    )


def size_for_cost(
    jupiter: Any,
    token_mint: str,
    desired_usd: float,
    max_round_trip_pct: float,
    sol_price_usd: float = 0.0,
    steps: tuple[float, ...] = (1.0, 0.5, 0.25),
) -> tuple[float, Optional[float], str]:
    """Réduit la taille jusqu'à ce que le coût passe sous le plafond.

    Plus juste que « rejeter si slippage > 2 % » : sur un carnet mince, une
    position deux fois plus petite coûte souvent bien moins de la moitié. On
    essaie donc de tenir, en plus petit, avant de renoncer.

    Retourne (taille retenue, coût mesuré, raison). Taille 0 = renoncer.
    """
    if jupiter is None or not getattr(jupiter, "enabled", False):
        return desired_usd, None, "Jupiter indisponible — taille inchangée"

    dernier: Optional[float] = None
    for step in steps:
        taille = round(desired_usd * step, 4)
        if taille <= 0:
            continue
        cost = jupiter.round_trip_cost_pct(token_mint, taille, sol_price_usd)
        if cost is None:
            return desired_usd, None, "devis indisponible — taille inchangée"
        dernier = cost
        if cost <= max_round_trip_pct:
            if step < 1.0:
                return taille, cost, (
                    f"taille réduite à {step:.0%} — {cost:.1f}% de frais "
                    f"contre {max_round_trip_pct:.0f}% autorisés"
                )
            return taille, cost, f"frais {cost:.1f}% dans le budget"

    return 0.0, dernier, (
        f"aller-retour {dernier:.1f}% même au quart de la taille — abandon"
        if dernier is not None else "coût non mesurable — abandon"
    )
