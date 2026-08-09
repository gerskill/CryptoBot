"""Tableau de bord des wallets — étape 3 du workflow wallets, celle qui manquait.

LE TROU QUE ÇA BOUCHE. `wallets.py` mesure l'AVANCE (`lead_minutes`) de
chaque wallet sur chaque token, mais ne dit jamais si cette avance valait
quelque chose : un wallet en avance sur un token qui n'a rien fait n'est pas
un témoin fiable, c'est du bruit. Son propre docstring le dit : "Aucun
signal ne sort d'ici tant que `AgentScoreboard` n'a pas jugé ces wallets sur
assez de cas — même garde que pour les agents (50 trades, 20 signaux)." Ce
jugement n'existait pas. Ce module le fait.

CE QUI DIFFÈRE DU SCORE GMGN "SMART_MONEY" DÉJÀ EN PRODUCTION
(`src/apis/gmgn.py`) : ce score-là vient d'un tag tiers-partie
("smart_degen") assigné par GMGN sur des critères qu'on ne contrôle pas.
Celui-ci vient de l'HISTORIQUE PROPRE de ce bot — quels wallets ont été
observés en avance, sur quels tokens précis, et ce que ces tokens sont
devenus. Un edge construit, pas emprunté.

DEUX SOURCES POUR "CE QUE LE TOKEN EST DEVENU", même logique que
`AgentScoreboard` (`src/core/scoreboard.py`) :

  tokens PRIS     <- le journal des trades (`peak_pct` mesuré en vrai)
  tokens REJETÉS  <- le shadow tracker (`peak_gain_pct`, suivi fictif 4h)

Un token qui n'apparaît dans AUCUNE des deux n'est pas encore jugé : il est
ignoré, pas compté comme un échec. Compter l'absence de jugement comme un
échec biaiserait vers "ce wallet est mauvais" sur des cas où on n'a
simplement pas encore l'information.

CE QUE CE MODULE NE FAIT PAS : décider d'une entrée. Il calcule un score par
wallet ; `src/core/scoring.py` en fait un composant du score alpha, avec la
même garde "absent ne dégrade jamais" que tous les autres composants
optionnels (rugcheck, smart_money, social).
"""

import statistics
from dataclasses import dataclass
from typing import Any, Optional

from src.core.journal import TradeJournal
from src.core.shadow import ShadowTracker
from src.core.wallets import EXCLUDED_TAGS, WalletRegistry

# Même garde citée dans le docstring de wallets.py : sous ce seuil, un wallet
# "rentable" est indistinguable du hasard dans une population de milliers.
MIN_TOKENS_FOR_WALLET_SCORE = 50
# Même seuil que ShadowVerdict.would_have_won (src/core/shadow.py) : rester
# cohérent avec la définition existante de "ce rejet valait le coup".
PUMP_THRESHOLD_PCT = 100.0
# En dessous, l'avance est dans le bruit de mesure/latence, pas un vrai signal.
MIN_LEAD_MINUTES = 1.0


@dataclass(frozen=True)
class WalletScore:
    """Fiabilité d'un wallet, jugée sur ce qui a suivi ses avances passées."""

    wallet: str
    tokens_judged: int
    tokens_pumped: int
    median_lead_minutes: Optional[float]

    @property
    def hit_rate(self) -> Optional[float]:
        """% des tokens où ce wallet était en avance qui ont ensuite pump."""
        if self.tokens_judged == 0:
            return None
        return round(100 * self.tokens_pumped / self.tokens_judged, 1)

    @property
    def actionable(self) -> bool:
        return self.tokens_judged >= MIN_TOKENS_FOR_WALLET_SCORE

    @property
    def verdict(self) -> str:
        if not self.actionable:
            return f"échantillon trop court ({self.tokens_judged}/{MIN_TOKENS_FOR_WALLET_SCORE})"
        rate = self.hit_rate or 0.0
        if rate >= 40:
            return "fiable"
        if rate >= 20:
            return "moyen"
        return "bruit — ne pas pondérer"

    def as_dict(self) -> dict[str, Any]:
        return {
            "wallet": self.wallet,
            "tokens_judged": self.tokens_judged,
            "tokens_pumped": self.tokens_pumped,
            "hit_rate": self.hit_rate,
            "median_lead_minutes": self.median_lead_minutes,
            "actionable": self.actionable,
            "verdict": self.verdict,
        }


def _outcome_map(journal: TradeJournal, shadow: ShadowTracker) -> dict[str, float]:
    """token_address -> plus haut gain observé (%), toutes sources confondues.

    Le journal (trades PRIS, mesure réelle) écrase le shadow (trades REJETÉS,
    suivi fictif) quand un même token existe dans les deux.
    """
    outcomes: dict[str, float] = {}
    for row in shadow.read_all():
        address = row.get("token_address")
        peak = row.get("peak_gain_pct")
        if address and peak is not None:
            outcomes[address] = peak
    for row in journal.read_positions():
        address = row.get("token_address")
        peak = row.get("peak_pct")
        if address and peak is not None:
            outcomes[address] = peak
    return outcomes


def score_wallets(
    wallets: WalletRegistry, journal: TradeJournal, shadow: ShadowTracker
) -> list[WalletScore]:
    """Score chaque wallet observé, jugé par ce que ses avances sont devenues.

    Trié : wallets actionnables (échantillon suffisant) d'abord, par taux de
    réussite décroissant ; le reste ensuite, pour rester visible sans peser.
    """
    outcomes = _outcome_map(journal, shadow)
    if not outcomes:
        return []

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in wallets.read_all():
        if set(row.get("tags") or ()) & EXCLUDED_TAGS:
            continue
        lead = row.get("lead_minutes")
        if lead is None or lead < MIN_LEAD_MINUTES:
            continue  # pas en avance = pas un témoin, cf. docstring wallets.py
        address = row.get("token_address")
        if address not in outcomes:
            continue  # token pas encore jugé
        grouped.setdefault(row.get("wallet", "?"), []).append(row)

    scores = []
    for wallet, rows in grouped.items():
        leads = [r["lead_minutes"] for r in rows]
        pumped = sum(
            1 for r in rows if outcomes[r["token_address"]] >= PUMP_THRESHOLD_PCT
        )
        scores.append(
            WalletScore(
                wallet=wallet,
                tokens_judged=len(rows),
                tokens_pumped=pumped,
                median_lead_minutes=(
                    round(statistics.median(leads), 2) if leads else None
                ),
            )
        )

    return sorted(
        scores,
        key=lambda s: (not s.actionable, -(s.hit_rate or -1.0)),
    )
