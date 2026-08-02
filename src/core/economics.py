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
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from src.core.stats import Interval

# Marge exigée au-dessus du seuil de rentabilité. Un TP qui rembourse
# exactement les frais n'est pas un gain, c'est du travail gratuit.
DEFAULT_EDGE_MARGIN = 1.5
# Au-delà, la position est ingérable quelle que soit la stratégie.
MAX_ACCEPTABLE_ROUND_TRIP_PCT = 8.0

# TAUX D'ATTEINTE MESURÉS sur les 26 positions instrumentées : combien de
# trades ont vu leur pic franchir ce niveau. C'est l'a priori à utiliser tant
# qu'un bras n'a pas d'historique à lui.
#
# STOCKÉ EN NOMBRE DE SUCCÈS, PAS EN TAUX. Un taux nu ment sur ce qu'il vaut :
# « 15 % atteignent +100 % » et « 4 trades sur 26 ont atteint +100 % » sont le
# même nombre et deux niveaux de certitude très différents. Le dénominateur est
# commun (26 positions instrumentées), le numérateur est ce qui distingue une
# ligne solide d'une ligne anecdotique.
#
# LE PIÈGE QUE ÇA ÉVITE. La garde économique demandait le win rate DU BRAS.
# Un bras neuf en a zéro, clampé à un plancher arbitraire de 10%, ce qui
# faisait exiger un TP1 de +52% à +63% — donc aucune entrée, donc jamais de
# win rate. Poule et œuf : le bras ne pouvait pas démarrer.
#
# Un a priori mesuré sur l'historique global est un bien meilleur point de
# départ qu'une constante inventée, et il est remplacé par le vécu du bras
# dès qu'il en a un.
OBSERVATIONS_INSTRUMENTEES = 26
SUCCES_ATTEINTE_MESURES = {
    10: 11,   # 42 %
    25: 8,    # 31 %
    50: 6,    # 23 %
    100: 4,   # 15 %  <- quatre observations
    150: 1,   # 4 %   <- une
}
MIN_TRADES_POUR_WIN_RATE_PROPRE = 10


def _succes_interpoles(take_profit_pct: float) -> float:
    niveaux = sorted(SUCCES_ATTEINTE_MESURES)
    if take_profit_pct <= niveaux[0]:
        return float(SUCCES_ATTEINTE_MESURES[niveaux[0]])
    if take_profit_pct >= niveaux[-1]:
        return float(SUCCES_ATTEINTE_MESURES[niveaux[-1]])
    for bas, haut in zip(niveaux, niveaux[1:]):
        if bas <= take_profit_pct <= haut:
            ratio = (take_profit_pct - bas) / (haut - bas)
            return SUCCES_ATTEINTE_MESURES[bas] + ratio * (
                SUCCES_ATTEINTE_MESURES[haut] - SUCCES_ATTEINTE_MESURES[bas]
            )
    return float(SUCCES_ATTEINTE_MESURES[niveaux[-1]])


def expected_hit_rate(take_profit_pct: float) -> float:
    """A priori de réussite pour ce niveau de TP, interpolé sur la mesure."""
    return _succes_interpoles(take_profit_pct) / OBSERVATIONS_INSTRUMENTEES


def hit_rate_interval(take_profit_pct: float) -> "Interval":
    """L'a priori AVEC son incertitude, en pourcentage.

    Wilson plutôt que Wald : à 1 succès sur 26, Wald rend une borne basse
    négative. Voir `src/core/stats.wilson`.
    """
    from src.core.stats import wilson

    return wilson(round(_succes_interpoles(take_profit_pct)), OBSERVATIONS_INSTRUMENTEES)


def win_rate_for(
    take_profit_pct: float, arm_win_rate: float, arm_trades: int
) -> tuple[float, str, float]:
    """Win rate à utiliser, d'où il vient, et sa borne HAUTE à 95 %.

    Le vécu du bras l'emporte dès qu'il est significatif ; sinon l'a priori
    mesuré. Retourner la SOURCE permet de le dire dans le log plutôt que de
    faire passer une estimation pour une mesure.

    La borne haute sert à `evaluate` : refuser une entrée sur une estimation
    ponctuelle tirée de 4 observations, c'est la fausse précision que le reste
    du dépôt combat. Voir `evaluate`.
    """
    from src.core.stats import wilson

    if arm_trades >= MIN_TRADES_POUR_WIN_RATE_PROPRE:
        interval = wilson(round(arm_win_rate * arm_trades), arm_trades)
        return (
            arm_win_rate,
            f"vécu du bras sur {arm_trades} trades",
            interval.high / 100,
        )
    attendu = expected_hit_rate(take_profit_pct)
    interval = hit_rate_interval(take_profit_pct)
    return (
        attendu,
        (
            f"a priori mesuré ({attendu:.0%} atteignent +{take_profit_pct:.0f}%, "
            f"IC95 {interval.low:.0f}–{interval.high:.0f}% sur n={interval.n})"
        ),
        interval.high / 100,
    )


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
    win_rate_high: Optional[float] = None,
) -> TradeEconomics:
    """Ce trade peut-il gagner de l'argent, indépendamment du signal ?

    `round_trip_pct = None` (pas de devis) ne bloque PAS : même invariant que
    le reste du pipeline, une donnée absente ne rejette jamais. Mais le dire,
    pour que « non mesuré » ne se confonde pas avec « gratuit ».

    LE VETO PORTE SUR L'INTERVALLE, PAS SUR LE POINT. `win_rate_high` est la
    borne haute à 95 % du taux de réussite. Le plancher de TP est calculé deux
    fois : au point (c'est le chiffre AFFICHÉ, celui qui décrit la situation
    attendue) et à la borne haute (c'est le chiffre qui REFUSE). Un TP n'est
    rejeté que s'il reste sous le plancher même dans l'hypothèse la plus
    favorable compatible avec les données.

    POURQUOI. Le taux « 15 % atteignent +100 % » vient de 4 succès sur 26
    positions : IC95 ≈ 6–34 %. Bloquer une entrée sur les 15 % nus, c'est
    exactement la fausse précision que `Interval.conclusive`,
    `MIN_SEGMENT_SAMPLE` et `live_mode_allowed` combattent partout ailleurs.
    Même logique que `live_mode_allowed`, en miroir : là-bas on n'AUTORISE que
    si la borne défavorable est bonne, ici on ne REFUSE que si la borne
    favorable est mauvaise. Dans les deux cas la décision doit survivre à
    l'intervalle entier.

    `win_rate_high = None` retombe sur le point — comportement d'origine.
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

    # Le plancher qui REFUSE : calculé au meilleur taux de réussite compatible
    # avec les données. Un win rate plus haut abaisse le plancher, donc ce
    # seuil-ci est toujours ≤ celui affiché.
    haut = win_rate if win_rate_high is None else max(win_rate, win_rate_high)
    plancher_veto = minimum_viable_tp(
        round_trip_pct, haut, partial_fraction, rest_pct, margin
    )

    if take_profit_pct < plancher_veto:
        return TradeEconomics(
            viable=False,
            reason=(
                f"TP +{take_profit_pct:.0f}% sous le plancher +{plancher_veto:.0f}% "
                f"imposé par {round_trip_pct:.1f}% de frais, même à {haut:.0%} de "
                f"réussite (borne haute)"
            ),
            round_trip_pct=round_trip_pct,
            expected_net_pct=net,
            minimum_tp_pct=plancher,
            configured_tp_pct=take_profit_pct,
        )

    if take_profit_pct < plancher:
        return TradeEconomics(
            viable=True,
            reason=(
                f"TP +{take_profit_pct:.0f}% sous le plancher attendu +{plancher:.0f}% "
                f"mais au-dessus de +{plancher_veto:.0f}% à {haut:.0%} de réussite — "
                f"l'échantillon ne tranche pas, entrée non bloquée"
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
