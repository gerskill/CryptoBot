"""Intervalles de confiance. Sans eux, chaque chiffre du bot ment par omission.

LE PROBLÈME. « Win rate 11,1 % » sur 36 trades se lit comme une mesure. C'en
est une, mais son intervalle à 95 % va de **3,7 % à 25,0 %** : la vraie phrase
est « entre presque nul et plutôt bon, on ne sait pas ». Afficher 11,1 % sans
cet intervalle donne à un échantillon minuscule l'autorité d'une statistique.

Toutes les décisions du projet reposent sur des échantillons de 4 à 36
observations. Aucune ne devrait être prise sans voir sa largeur d'incertitude.

MÉTHODE. Wilson pour les proportions, pas Wald. Wald (`p ± 1.96·√(p(1-p)/n)`)
est le calcul enseigné partout et il est FAUX ici : sur un petit n ou une
proportion proche de 0, il produit des bornes négatives et sous-estime
l'incertitude. Avec 1 gagnant sur 36, Wald donne [-2,5 % ; 8,1 %] — une borne
impossible. Wilson reste dans [0,1] par construction.

Bootstrap pour le P&L moyen, qui n'est pas une proportion et dont la
distribution est très asymétrique (quelques gros gains, beaucoup de petites
pertes) : aucune formule fermée ne convient.
"""

import math
import random
from dataclasses import dataclass
from typing import Any, Optional, Sequence

Z95 = 1.959963985


@dataclass(frozen=True)
class Interval:
    """Une estimation et ce qu'on ignore d'elle."""

    value: float
    low: float
    high: float
    n: int

    @property
    def width(self) -> float:
        return self.high - self.low

    @property
    def conclusive(self) -> bool:
        """Un intervalle qui contient tout ne conclut rien.

        Seuil ramené de 30 à 12 points après vérification sur les données
        réelles : le win rate mesuré est 11,1 % [4,4 – 25,3], soit 20,9 points
        de large. À 30 il était déclaré « concluant » — or cet intervalle
        couvre aussi bien une stratégie catastrophique qu'une stratégie
        correcte. Un seuil qui valide ça ne sert à rien.

        12 points sur une proportion, c'est le seuil au-delà duquel deux
        stratégies séparées par un écart réaliste ne peuvent plus être
        distinguées. Atteindre cette précision demande environ 100 trades.
        """
        return self.width < 12.0

    def format(self, unit: str = "%") -> str:
        return f"{self.value:.1f}{unit} [{self.low:.1f}–{self.high:.1f}] n={self.n}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": round(self.value, 2),
            "low": round(self.low, 2),
            "high": round(self.high, 2),
            "n": self.n,
            "conclusive": self.conclusive,
        }


def wilson(successes: int, total: int, z: float = Z95) -> Interval:
    """Intervalle de Wilson pour une proportion, en pourcentage.

    Choisi contre Wald : sur 1 succès sur 36, Wald rend une borne basse
    NÉGATIVE. Wilson reste borné et garde une couverture correcte même à
    n petit ou p extrême — exactement le régime de ce projet.
    """
    if total <= 0:
        return Interval(0.0, 0.0, 100.0, 0)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    marge = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return Interval(
        value=100 * p,
        low=100 * max(0.0, centre - marge),
        high=100 * min(1.0, centre + marge),
        n=total,
    )


def bootstrap_mean(
    values: Sequence[float],
    iterations: int = 2000,
    z_unused: float = Z95,
    seed: int = 12345,
) -> Optional[Interval]:
    """Intervalle percentile sur la moyenne, par rééchantillonnage.

    Le P&L par trade n'est pas une proportion : sa distribution est très
    asymétrique — quelques gains à +30 $, beaucoup de pertes à -5 $. Une
    formule normale supposerait une symétrie qui n'existe pas. Le bootstrap
    ne suppose rien sur la forme.

    `seed` fixe : deux exécutions sur les mêmes données doivent rendre le même
    intervalle, sinon on ne peut pas comparer deux rapports.
    """
    data = [float(v) for v in values]
    if len(data) < 3:
        return None

    rng = random.Random(seed)
    n = len(data)
    moyennes = []
    for _ in range(iterations):
        echantillon = [data[rng.randrange(n)] for _ in range(n)]
        moyennes.append(sum(echantillon) / n)
    moyennes.sort()

    return Interval(
        value=sum(data) / n,
        low=moyennes[int(0.025 * iterations)],
        high=moyennes[int(0.975 * iterations) - 1],
        n=n,
    )


def win_rate_interval(positions: Sequence[dict[str, Any]]) -> Interval:
    wins = sum(1 for p in positions if (p.get("pnl_usd") or 0) > 0)
    return wilson(wins, len(positions))


def pnl_per_trade_interval(positions: Sequence[dict[str, Any]]) -> Optional[Interval]:
    return bootstrap_mean([p.get("pnl_usd") or 0.0 for p in positions])


def walk_forward(
    positions: Sequence[dict[str, Any]],
    evaluate: Any,
    folds: int = 3,
    min_train: int = 10,
) -> list[dict[str, Any]]:
    """Validation par fenêtres glissantes : régler sur le passé, juger sur la suite.

    LE BIAIS QUE ÇA CORRIGE. `simulate_exits` et `exit_grid` cherchent le
    meilleur couple (stop, take profit) SUR LES MÊMES DONNÉES qu'ils
    évaluent. Trouver le meilleur réglage a posteriori est trivial et ne
    prédit rien : c'est du surapprentissage pur. Le seul test honnête est
    d'apprendre sur les N premiers trades et de mesurer sur les suivants,
    jamais vus.

    `evaluate(train, test) -> dict` reçoit deux tranches CHRONOLOGIQUES. À
    lui de régler sur `train` et de rendre un résultat mesuré sur `test`.

    LIMITE À DIRE : sur 26 positions instrumentées, trois plis donnent des
    tranches de test d'environ 5 trades. C'est trop peu pour conclure — mais
    l'écart entre performance en apprentissage et en test reste informatif :
    un écart énorme signale un surapprentissage même sur petit échantillon.
    """
    n = len(positions)
    if n < min_train + folds:
        return []

    resultats = []
    pas = (n - min_train) // folds or 1
    for fold in range(folds):
        fin_train = min_train + fold * pas
        fin_test = min(n, fin_train + pas)
        if fin_test <= fin_train:
            break
        train = list(positions[:fin_train])
        test = list(positions[fin_train:fin_test])
        try:
            resultat = evaluate(train, test)
        except Exception as exc:
            resultat = {"error": f"{type(exc).__name__}: {exc}"}
        resultats.append({
            "fold": fold + 1,
            "train_n": len(train),
            "test_n": len(test),
            **(resultat or {}),
        })
    return resultats


def overfit_gap(folds: Sequence[dict[str, Any]]) -> Optional[float]:
    """Écart moyen entre apprentissage et test. Grand écart = surapprentissage.

    Chaque pli doit porter `train_score` et `test_score`. Un écart positif
    signifie que le réglage marche mieux sur ce qu'il a vu que sur la suite —
    définition opérationnelle du surapprentissage.
    """
    ecarts = [
        f["train_score"] - f["test_score"]
        for f in folds
        if f.get("train_score") is not None and f.get("test_score") is not None
    ]
    return round(sum(ecarts) / len(ecarts), 4) if ecarts else None


def compare(a: Interval, b: Interval) -> str:
    """Peut-on dire qu'une stratégie bat l'autre ?

    Test volontairement CONSERVATEUR : on ne conclut que si les intervalles
    ne se recouvrent pas du tout. Des intervalles disjoints impliquent une
    différence significative ; l'inverse n'est pas vrai — un chevauchement
    n'exclut pas une différence réelle. On préfère ici manquer une vraie
    différence plutôt qu'en inventer une, parce que c'est de l'argent qui
    serait alloué sur cette conclusion.
    """
    if a.low > b.high:
        return "supérieur"
    if b.low > a.high:
        return "inférieur"
    return "indistinguable"
