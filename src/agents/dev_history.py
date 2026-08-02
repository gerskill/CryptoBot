"""Historique du créateur — détecter le lanceur en série.

LE TROU QUE ÇA BOUCHE. Le dépôt filtre le TOKEN (liquidité, holders,
concentration, autorités, RugCheck) et ne regarde jamais QUI l'a lancé. Un
wallet qui a rugpullé onze fois cette semaine relance un douzième token qui
passe tous les filtres : il est neuf, sa liquidité est fraîche, ses autorités
sont révoquées, RugCheck sort 99 comme sur tout token de moins d'une heure.

CE QU'ON A RÉELLEMENT SOUS LA MAIN, vérifié champ par champ sur `Candidate`.
Aucune API du projet ne rend l'adresse du créateur ni la liste de ses tokens :
RugCheck expose `creatorBalance` (d'où `dev_wallet_pct`), et GMGN n'a pas
d'API publique. Cet agent n'invente donc pas une requête qui n'existe pas — il
agrège les six signaux DÉJÀ COLLECTÉS qui portent de l'information sur le
comportement du créateur, et le dit dans sa raison :

    creator_token_status        le créateur a-t-il déjà vendu son allocation
    twitter_create_token_count  combien de tokens ce compte a déjà lancés
    rug_ratio                   part de rugs dans l'entourage du token (GMGN)
    dev_wallet_pct              ce que le créateur garde encore
    bundler_rate                achats groupés dans un bloc — auto-attribution
    insider_rate                part détenue par des wallets liés

POURQUOI UN SCORE ET PAS UN BOOLÉEN. Aucun de ces six signaux ne prouve la
malveillance isolément : un dev peut détenir 8 % pour de bonnes raisons, un
compte peut avoir lancé trois tokens honnêtes. C'est leur ACCUMULATION qui
discrimine. Le score est la somme des pénalités déclenchées, plafonnée à 100.

INVARIANT RESPECTÉ. Une donnée absente ne pénalise pas et ne rejette pas :
elle est retirée du calcul, et `coverage` dit quelle fraction des signaux a
réellement été mesurée. Un score de 0 sur zéro signal disponible n'est PAS un
créateur propre — c'est une absence de mesure, et `known` le distingue.
"""

import time
from dataclasses import dataclass
from typing import Any, Optional

from src.agents import _journal
from src.core.models import Candidate

# Pénalités par signal. Calibrées pour qu'aucun signal seul n'atteigne le seuil
# de rejet par défaut : il faut au moins deux concordances.
POIDS = {
    "creator_sold": 35.0,       # le créateur a vendu — le signal le plus direct
    "serial_launcher": 30.0,    # compte qui enchaîne les lancements
    "rug_entourage": 25.0,      # rug_ratio élevé autour du token
    "dev_holds_much": 20.0,     # le créateur peut encore tout vendre
    "bundled_launch": 15.0,     # achats groupés au bloc de lancement
    "insider_heavy": 15.0,      # wallets liés surreprésentés
}

# Seuils de déclenchement, tirés des filtres déjà en place dans le manifeste
# (`max_rug_ratio` 0.3, `max_bundler_rate` 0.2, `max_insider_rate` 0.1) pour ne
# pas introduire une deuxième échelle concurrente.
SERIAL_LAUNCH_COUNT = 3
RUG_RATIO_ALERT = 0.3
DEV_HOLD_ALERT_PCT = 10.0
BUNDLER_ALERT = 0.2
INSIDER_ALERT = 0.1

# Au-delà, le créateur est traité comme un lanceur en série.
#
# CALÉ SUR L'ARITHMÉTIQUE DES POIDS, à couverture complète (total 140) :
#
#   creator_sold seul                35/140 = 25,0 %   -> passe
#   serial_launcher seul             30/140 = 21,4 %   -> passe
#   rug_entourage seul               25/140 = 17,9 %   -> passe
#   creator_sold + serial_launcher   65/140 = 46,4 %   -> REJETTE
#   creator_sold + rug_entourage     60/140 = 42,9 %   -> REJETTE
#   les trois faibles cumulés        50/140 = 35,7 %   -> passe
#
# 45 est donc le seuil qui exige DEUX signaux forts concordants et qu'aucun
# signal isolé n'atteint. Les trois faibles (détention du dev, bundler,
# insiders) ne rejettent pas seuls : ils ont déjà leurs propres filtres au
# manifeste (`max_dev_wallet_pct`, `max_bundler_rate`, `max_insider_rate`) et
# les compter deux fois ferait un doublon silencieux.
DEFAULT_REJECT_ABOVE = 45.0
# En dessous de cette couverture, le score ne veut rien dire et ne doit pas
# rejeter — même logique que `sub_scores._weights_used` dans le scoring.
MIN_COVERAGE = 0.5


@dataclass(frozen=True)
class DevVerdict:
    """0 = rien à signaler, 100 = lanceur en série. `None` = non mesuré."""

    score: Optional[float]
    signals: tuple[str, ...]
    coverage: float
    reason: str

    @property
    def known(self) -> bool:
        return self.score is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "dev_score": round(self.score, 1) if self.score is not None else None,
            "signals": list(self.signals),
            "coverage": round(self.coverage, 2),
            "reason": self.reason,
        }


class DevHistoryAgent:
    """Note le créateur d'un token sur les signaux déjà enrichis."""

    def __init__(
        self,
        log_path: Optional[str] = None,
        reject_above: float = DEFAULT_REJECT_ABOVE,
        min_coverage: float = MIN_COVERAGE,
    ):
        self.log_path = log_path
        self.reject_above = reject_above
        self.min_coverage = min_coverage

    def score_candidate(self, candidate: Candidate) -> DevVerdict:
        """Somme des pénalités déclenchées, sur les seuls signaux disponibles."""
        penalites = 0.0
        disponibles = 0.0
        declenches: list[str] = []

        def evalue(present: bool, declenche: bool, cle: str) -> None:
            nonlocal penalites, disponibles
            if not present:
                return
            disponibles += POIDS[cle]
            if declenche:
                penalites += POIDS[cle]
                declenches.append(cle)

        statut = candidate.creator_token_status
        evalue(
            statut is not None,
            bool(statut) and "sell" in str(statut).lower(),
            "creator_sold",
        )

        lancements = candidate.twitter_create_token_count
        evalue(
            lancements is not None,
            (lancements or 0) >= SERIAL_LAUNCH_COUNT,
            "serial_launcher",
        )

        evalue(
            candidate.rug_ratio is not None,
            (candidate.rug_ratio or 0) >= RUG_RATIO_ALERT,
            "rug_entourage",
        )
        evalue(
            candidate.dev_wallet_pct is not None,
            (candidate.dev_wallet_pct or 0) >= DEV_HOLD_ALERT_PCT,
            "dev_holds_much",
        )
        evalue(
            candidate.bundler_rate is not None,
            (candidate.bundler_rate or 0) >= BUNDLER_ALERT,
            "bundled_launch",
        )
        evalue(
            candidate.insider_rate is not None,
            (candidate.insider_rate or 0) >= INSIDER_ALERT,
            "insider_heavy",
        )

        total = sum(POIDS.values())
        coverage = disponibles / total if total else 0.0
        if disponibles <= 0:
            return DevVerdict(
                score=None, signals=(), coverage=0.0,
                reason="aucun signal de créateur disponible — non mesuré",
            )

        # Rapporté aux signaux PRÉSENTS, pas au total théorique : sinon un
        # token dont un seul signal est connu et alarmant sortirait un score
        # faible, et l'absence de données ressemblerait à de la propreté.
        score = 100.0 * penalites / disponibles
        return DevVerdict(
            score=score,
            signals=tuple(declenches),
            coverage=coverage,
            reason=(
                f"{len(declenches)} signal(aux) sur {coverage:.0%} de couverture"
                + (f" : {', '.join(declenches)}" if declenches else "")
            ),
        )

    def rejection_reason(self, candidate: Candidate) -> Optional[str]:
        """Motif de rejet, ou `None`. Signature alignée sur le pipeline.

        DEUX GARDE-FOUS avant de rejeter :
          - un score inconnu ne rejette jamais (invariant du pipeline) ;
          - une couverture sous `MIN_COVERAGE` ne rejette pas non plus, parce
            qu'un score porté par un seul signal sur six n'est pas une mesure.
        """
        verdict = self.score_candidate(candidate)
        if not verdict.known or verdict.coverage < self.min_coverage:
            return None
        if verdict.score is not None and verdict.score > self.reject_above:
            return (
                f"créateur suspect {verdict.score:.0f}/100 > {self.reject_above:.0f} "
                f"({', '.join(verdict.signals)})"
            )
        return None

    def observe(self, candidate: Candidate) -> DevVerdict:
        verdict = self.score_candidate(candidate)
        if self.log_path:
            _journal.append(self.log_path, {
                "ts": time.time(),
                "token_address": candidate.token_address,
                "symbol": candidate.symbol,
                **verdict.as_dict(),
            })
        return verdict
