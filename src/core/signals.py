"""Signaux d'agents et agrégation par quorum. Fonctions pures, zéro I/O.

TROIS DÉCISIONS DE CONCEPTION, chacune corrige un piège précis :

1. UN AGENT NE VOTE PAS « ENTRER ». Il produit un signal et sa raison ; c'est
   la stratégie qui décide. Si les agents décidaient, toutes les stratégies
   recevraient la même décision et la diversité entre bras — la seule chose
   qui permet de comparer — disparaîtrait. Les bras restent l'unité de
   compétition, les agents l'unité de mesure.

2. L'ABSTENTION EST UN VOTE DE PREMIÈRE CLASSE. Un agent privé de données
   (API tombée, champ absent) doit dire ABSTAIN, jamais NO. Sinon une panne
   Birdeye devient un veto silencieux, et avec un quorum fixe « 3 sur 5 »
   deux agents muets suffisent à empêcher tout trade pour toujours. Le quorum
   se calcule donc sur les VOTANTS PRÉSENTS, pas sur l'effectif théorique.
   C'est le même invariant que le pipeline : une donnée absente ne rejette
   jamais.

3. UN VETO EXISTE, MAIS SEULEMENT POUR LA SÉCURITÉ. Un honeypot n'est pas
   une opinion à pondérer. Les signaux marqués `veto=True` court-circuitent
   le quorum. Tout le reste se négocie.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Optional


class Vote(str, Enum):
    YES = "yes"
    NO = "no"
    ABSTAIN = "abstain"


@dataclass(frozen=True)
class Signal:
    """Ce qu'un agent a mesuré, et pourquoi."""

    agent: str
    vote: Vote
    reason: str = ""
    # 0..1 — sert au tableau de bord, jamais à décider seul.
    score: Optional[float] = None
    # Sécurité uniquement : honeypot, autorité de mint active, rug avéré.
    veto: bool = False

    @property
    def counts(self) -> bool:
        return self.vote is not Vote.ABSTAIN

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "vote": self.vote.value,
            "reason": self.reason,
            "score": self.score,
            "veto": self.veto,
        }


def abstain(agent: str, reason: str) -> Signal:
    """Raccourci : dire « je ne sais pas » sans peser sur la décision."""
    return Signal(agent=agent, vote=Vote.ABSTAIN, reason=reason)


@dataclass(frozen=True)
class Verdict:
    """Décision agrégée pour un candidat, entièrement traçable."""

    passed: bool
    reason: str
    signals: tuple[Signal, ...] = ()
    voters: int = 0
    yes: int = 0
    no: int = 0
    abstained: int = 0
    required: int = 0

    @property
    def ratio(self) -> float:
        return self.yes / self.voters if self.voters else 0.0

    @property
    def dissent(self) -> tuple[Signal, ...]:
        """Ceux qui n'étaient pas d'accord avec l'issue. Le tableau de bord
        des agents ne mesure rien sans ça."""
        wanted = Vote.NO if self.passed else Vote.YES
        return tuple(s for s in self.signals if s.vote is wanted)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "voters": self.voters,
            "yes": self.yes,
            "no": self.no,
            "abstained": self.abstained,
            "required": self.required,
            "ratio": round(self.ratio, 3),
            "signals": [s.as_dict() for s in self.signals],
        }


def aggregate(
    signals: Iterable[Signal],
    min_ratio: float = 0.6,
    min_voters: int = 1,
) -> Verdict:
    """Quorum sur les VOTANTS PRÉSENTS.

    `min_ratio` est une PROPORTION, pas un compte. « 3 sur 5 » se casse dès
    qu'un agent s'abstient ; « 60% des présents » survit à une panne d'API et
    garde le même niveau d'exigence.

    `min_voters` est le garde-fou inverse : si presque tout le monde s'abstient,
    le peu qui reste ne doit pas décider seul. En dessous, on refuse — mais en
    le DISANT, pour que ça se distingue d'un rejet motivé.
    """
    signals = tuple(signals)
    vetoes = [s for s in signals if s.veto and s.vote is Vote.NO]
    if vetoes:
        return Verdict(
            passed=False,
            reason=f"veto sécurité — {vetoes[0].reason or vetoes[0].agent}",
            signals=signals,
            voters=sum(1 for s in signals if s.counts),
            yes=sum(1 for s in signals if s.vote is Vote.YES),
            no=sum(1 for s in signals if s.vote is Vote.NO),
            abstained=sum(1 for s in signals if not s.counts),
        )

    voters = [s for s in signals if s.counts]
    yes = sum(1 for s in voters if s.vote is Vote.YES)
    no = len(voters) - yes
    abstained = len(signals) - len(voters)
    required = _required(len(voters), min_ratio)

    if len(voters) < min_voters:
        return Verdict(
            passed=False,
            reason=(
                f"quorum introuvable — {abstained} agent(s) sans données, "
                f"{len(voters)}/{min_voters} votants"
            ),
            signals=signals, voters=len(voters), yes=yes, no=no,
            abstained=abstained, required=required,
        )

    passed = yes >= required
    if passed:
        reason = f"{yes}/{len(voters)} d'accord (seuil {required})"
    else:
        contre = ", ".join(s.reason or s.agent for s in voters if s.vote is Vote.NO)
        reason = f"{yes}/{len(voters)} d'accord, il en faut {required} — {contre}"

    return Verdict(
        passed=passed, reason=reason, signals=signals, voters=len(voters),
        yes=yes, no=no, abstained=abstained, required=required,
    )


def _required(voters: int, min_ratio: float) -> int:
    """Nombre de OUI nécessaires. Au moins un dès qu'il y a un votant."""
    if voters <= 0:
        return 0
    import math

    return max(1, math.ceil(voters * min_ratio))


@dataclass
class SignalLog:
    """Accumule les signaux d'un cycle pour les journaliser à l'entrée.

    Sans trace au moment de la DÉCISION, le tableau de bord des agents ne peut
    rien mesurer plus tard : on ne saurait pas qui avait dit quoi.
    """

    by_token: dict[str, list[Signal]] = field(default_factory=dict)

    def record(self, token_address: str, signal: Signal) -> None:
        self.by_token.setdefault(token_address, []).append(signal)

    def verdict(
        self, token_address: str, min_ratio: float = 0.6, min_voters: int = 1
    ) -> Verdict:
        return aggregate(self.by_token.get(token_address, ()), min_ratio, min_voters)

    def payload(self, token_address: str) -> list[dict[str, Any]]:
        return [s.as_dict() for s in self.by_token.get(token_address, ())]

    def clear(self) -> None:
        self.by_token.clear()
