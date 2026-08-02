"""Tableau de bord des agents : qui a raison, mesuré sur les faits.

LE TROU QUE ÇA BOUCHE. Ajuster le poids d'un agent « quand un trade perd avec
un consensus élevé » suppose de savoir qui avait dit quoi, et si c'était juste.
Sans mesure, l'ajustement de poids optimise du bruit et donne l'illusion
d'apprendre.

DEUX FAITS, DEUX SOURCES — et il faut les deux, sinon la mesure ment :

  vrais positifs / faux positifs  <- le journal des trades (ce qu'on a pris)
  vrais négatifs / faux négatifs  <- le shadow log (ce qu'on a refusé)

Ne regarder que le journal ne mesure que les trades pris : un agent qui dit
NON à tout aurait une précision parfaite et zéro utilité. Le shadow tracker
suit déjà les rejets pendant 4 h et sait lesquels seraient partis à +100 % —
c'est exactement le contrefactuel qui manque.

CE QUE CE MODULE NE FAIT PAS : ajuster les poids. Il mesure. Le passage à
l'action est conditionné à un échantillon suffisant (voir
`MIN_TRADES_FOR_WEIGHTS`), parce qu'ajuster sur 36 trades revient à
surapprendre le hasard.
"""

import json
import os
from dataclasses import dataclass
from typing import Any, Iterable, Optional

# En dessous, on AFFICHE les scores mais on n'agit pas dessus. Même logique
# que MIN_SEGMENT_SAMPLE dans learning.py, à une échelle plus exigeante :
# un poids d'agent porte sur toutes les décisions, pas sur un segment.
MIN_TRADES_FOR_WEIGHTS = 50
MIN_SIGNALS_PER_AGENT = 20


@dataclass(frozen=True)
class AgentScore:
    """Justesse d'un agent, décomposée. Aucun chiffre agrégé seul."""

    agent: str
    # Il a dit OUI et le trade a gagné.
    true_positive: int = 0
    # Il a dit OUI et le trade a perdu.
    false_positive: int = 0
    # Il a dit NON et le rejet n'aurait rien donné.
    true_negative: int = 0
    # Il a dit NON et le token est parti sans nous. Le coûteux.
    false_negative: int = 0
    abstained: int = 0

    @property
    def sample(self) -> int:
        return (
            self.true_positive + self.false_positive
            + self.true_negative + self.false_negative
        )

    @property
    def precision(self) -> Optional[float]:
        """Quand il dit OUI, à quelle fréquence a-t-il raison ?"""
        said_yes = self.true_positive + self.false_positive
        return round(100 * self.true_positive / said_yes, 1) if said_yes else None

    @property
    def recall(self) -> Optional[float]:
        """Des occasions réelles, combien a-t-il laissé passer ?"""
        real = self.true_positive + self.false_negative
        return round(100 * self.true_positive / real, 1) if real else None

    @property
    def specificity(self) -> Optional[float]:
        """Quand il dit NON, à quelle fréquence a-t-il raison ?"""
        said_no = self.true_negative + self.false_negative
        return round(100 * self.true_negative / said_no, 1) if said_no else None

    @property
    def usefulness(self) -> Optional[float]:
        """Écart entre bien juger le OUI et bien juger le NON.

        Un agent qui refuse tout a une spécificité parfaite et une utilité
        nulle : c'est ce déséquilibre que ce chiffre rend visible. 0 = ne
        discrimine rien, 100 = discrimine parfaitement.
        """
        if self.precision is None or self.specificity is None:
            return None
        return round((self.precision + self.specificity) / 2, 1)

    @property
    def verdict(self) -> str:
        if self.sample < MIN_SIGNALS_PER_AGENT:
            return f"échantillon trop court ({self.sample}/{MIN_SIGNALS_PER_AGENT})"
        if self.usefulness is None:
            return "n'a jamais tranché dans les deux sens"
        if self.usefulness >= 60:
            return "discrimine"
        if self.usefulness >= 45:
            return "à peine mieux que le hasard"
        return "contre-productif — inverser ou couper"

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "sample": self.sample,
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "true_negative": self.true_negative,
            "false_negative": self.false_negative,
            "abstained": self.abstained,
            "precision": self.precision,
            "recall": self.recall,
            "specificity": self.specificity,
            "usefulness": self.usefulness,
            "verdict": self.verdict,
            "actionable": self.sample >= MIN_SIGNALS_PER_AGENT,
        }


class AgentScoreboard:
    """Agrège les signaux journalisés contre l'issue réelle."""

    def __init__(self) -> None:
        self._tally: dict[str, dict[str, int]] = {}

    def _bump(self, agent: str, key: str) -> None:
        self._tally.setdefault(
            agent,
            {"true_positive": 0, "false_positive": 0, "true_negative": 0,
             "false_negative": 0, "abstained": 0},
        )[key] += 1

    def record_trade(self, signals: Iterable[dict[str, Any]], won: bool) -> None:
        """Un trade PRIS et clôturé : mesure les OUI."""
        for signal in signals:
            vote = signal.get("vote")
            agent = signal.get("agent") or "?"
            if vote == "abstain":
                self._bump(agent, "abstained")
            elif vote == "yes":
                self._bump(agent, "true_positive" if won else "false_positive")
            elif vote == "no":
                # Il était contre un trade qu'on a pris : s'il a perdu, il
                # avait raison de s'y opposer.
                self._bump(agent, "true_negative" if not won else "false_negative")

    def record_rejection(
        self, signals: Iterable[dict[str, Any]], would_have_won: bool
    ) -> None:
        """Un candidat REFUSÉ, jugé par le shadow tracker. Mesure les NON."""
        for signal in signals:
            vote = signal.get("vote")
            agent = signal.get("agent") or "?"
            if vote == "abstain":
                self._bump(agent, "abstained")
            elif vote == "no":
                self._bump(agent, "false_negative" if would_have_won else "true_negative")
            elif vote == "yes":
                self._bump(agent, "true_positive" if would_have_won else "false_positive")

    def scores(self) -> list[AgentScore]:
        return sorted(
            (AgentScore(agent=name, **counts) for name, counts in self._tally.items()),
            key=lambda s: (s.usefulness is None, -(s.usefulness or 0)),
        )

    def weights_allowed(self, total_trades: int) -> tuple[bool, str]:
        """Peut-on agir sur ces scores, ou seulement les regarder ?

        Le garde-fou central de tout le chantier d'apprentissage : sans lui,
        des poids d'agents seraient ajustés sur 36 trades — c'est-à-dire sur
        du bruit, avec la confiance d'une mesure.
        """
        if total_trades < MIN_TRADES_FOR_WEIGHTS:
            return False, (
                f"{total_trades}/{MIN_TRADES_FOR_WEIGHTS} trades — les scores "
                f"s'affichent, les poids ne bougent pas"
            )
        prets = [s for s in self.scores() if s.sample >= MIN_SIGNALS_PER_AGENT]
        if not prets:
            return False, (
                f"aucun agent n'a {MIN_SIGNALS_PER_AGENT} signaux jugés"
            )
        return True, f"{len(prets)} agent(s) mesurables sur {total_trades} trades"

    def as_dict(self, total_trades: int = 0) -> dict[str, Any]:
        allowed, why = self.weights_allowed(total_trades)
        return {
            "agents": [s.as_dict() for s in self.scores()],
            "weights_allowed": allowed,
            "reason": why,
        }


def build_from_funnel(
    funnel_rows: Iterable[dict[str, Any]],
    outcomes: dict[str, bool],
) -> AgentScoreboard:
    """Score les BRAS comme des agents, depuis l'entonnoir déjà journalisé.

    Un bras qui accepte ou rejette un token émet exactement le signal que ce
    module sait noter. Pas besoin d'attendre une architecture d'agents : les
    stratégies en sont déjà, et l'entonnoir enregistre leurs verdicts depuis
    le premier cycle.

    `outcomes` : adresse -> le token est-il monté. Vient du shadow tracker
    (`would_have_won`) pour les rejets et du journal pour les trades pris.
    Un token sans issue connue est IGNORÉ, jamais compté comme un échec :
    « pas encore jugé » et « jugé perdant » sont deux états différents.
    """
    board = AgentScoreboard()
    for row in funnel_rows:
        if row.get("gate") != "filtres" or not row.get("token"):
            continue
        issue = outcomes.get(row["token"])
        if issue is None:
            continue
        vote = "yes" if row.get("passed") else "no"
        signal = [{"agent": row.get("arm", "?"), "vote": vote}]
        if vote == "yes":
            board.record_trade(signal, won=issue)
        else:
            board.record_rejection(signal, would_have_won=issue)
    return board


def build_from_logs(
    trades_path: str, shadow_path: str, signals_key: str = "agent_signals"
) -> AgentScoreboard:
    """Reconstruit le tableau depuis les journaux, sans rien recalculer.

    Les lignes antérieures à la journalisation des signaux n'ont pas la clé :
    elles sont ignorées, pas comptées comme des abstentions. Confondre
    « pas de trace » et « pas d'avis » fausserait chaque taux.
    """
    board = AgentScoreboard()

    for row in _read_jsonl(trades_path):
        signals = row.get(signals_key)
        if not signals or not row.get("is_final_exit"):
            continue
        board.record_trade(signals, won=(row.get("pnl_usd", 0) or 0) > 0)

    for row in _read_jsonl(shadow_path):
        signals = row.get(signals_key)
        if not signals:
            continue
        board.record_rejection(signals, would_have_won=bool(row.get("would_have_won")))

    return board


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    if not path or not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows
