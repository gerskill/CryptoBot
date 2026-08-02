"""RSI sur les bougies reconstruites — et ce qu'il vaut vraiment ici.

CE QUE CET AGENT MESURE. L'indice de force relative de Wilder : la part des
variations haussières dans le mouvement total sur une fenêtre glissante.
Au-dessus de 70, le marché a monté sans respirer ; en dessous de 30, l'inverse.

CE QU'IL NE FAUT PAS EN ATTENDRE ICI, à lire avant de câbler quoi que ce soit
sur sa sortie :

1. LA FENÊTRE N'EST PAS CELLE DES MANUELS. Le RSI 14 classique se lit sur 14
   bougies journalières. `BUCKET_SECONDS` vaut 180 s : quatorze périodes font
   42 minutes, sur des tokens dont la détention médiane se compte en dizaines
   de minutes. C'est un indicateur d'impulsion intra-séance, pas un signal de
   retournement de tendance.
2. LES BOUGIES SONT RECONSTRUITES. Birdeye (`/defi/ohlcv`) est coupé depuis le
   2026-08-01 pour quota mort ; les bougies viennent des snapshots de cycle.
   Un cycle dure 90 s, donc une bougie de 180 s repose sur DEUX points. Les
   mèches n'existent pas et le RSI est calculé sur des clôtures échantillonnées.
3. UN SURACHAT N'EST PAS UN SIGNAL DE VENTE SUR CE MARCHÉ. Mesuré sur les 26
   positions instrumentées, le pic médian est +4,9 % mais 15 % dépassent
   +100 % : sortir sur RSI > 70 couperait précisément les trajectoires qui
   portent tout le P&L. Cet agent MESURE, il ne conclut pas.

D'où le choix de conception : `RSIAgent` rend une valeur et une zone, jamais
une décision d'entrée ou de sortie. Le câblage éventuel appartient aux bras,
avec leurs bornes et leur échantillon.
"""

import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from src.agents import _journal
from src.analysis.technical import Candle

# Wilder. Gardé à 14 pour rester comparable à la littérature, malgré le point 1
# du docstring : changer la constante ET l'unité de temps rendrait toute
# comparaison externe impossible.
DEFAULT_PERIOD = 14
OVERBOUGHT = 70.0
OVERSOLD = 30.0
# En dessous, le RSI est arithmétiquement calculable mais dominé par une ou
# deux variations. Wilder lui-même demande `period + 1` clôtures ; on exige une
# période complète de plus pour que le lissage ait un sens.
MIN_CLOSES = DEFAULT_PERIOD + 1


@dataclass(frozen=True)
class RSIReading:
    """Une lecture de RSI, et ce qu'on ignore d'elle."""

    value: Optional[float]
    zone: str  # "surachat" | "survente" | "neutre" | "inconnu"
    period: int
    samples: int
    reason: str

    @property
    def known(self) -> bool:
        return self.value is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "rsi": round(self.value, 2) if self.value is not None else None,
            "zone": self.zone,
            "period": self.period,
            "samples": self.samples,
            "reason": self.reason,
        }


def _zone(value: float) -> str:
    if value >= OVERBOUGHT:
        return "surachat"
    if value <= OVERSOLD:
        return "survente"
    return "neutre"


def compute_rsi(closes: Sequence[float], period: int = DEFAULT_PERIOD) -> Optional[float]:
    """RSI de Wilder sur une suite de clôtures. `None` si l'historique manque.

    Lissage exponentiel de Wilder (et non moyenne simple) : c'est la définition
    d'origine, et la seule qui converge vers la même valeur quelle que soit la
    longueur de l'historique fourni au-delà de la période.

    Les deux cas dégénérés sont explicites plutôt que laissés à une division
    par zéro : que du gain rend 100, que de la perte rend 0.
    """
    if len(closes) < period + 1:
        return None

    variations = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
    gains = [max(v, 0.0) for v in variations]
    pertes = [max(-v, 0.0) for v in variations]

    gain_moyen = sum(gains[:period]) / period
    perte_moyenne = sum(pertes[:period]) / period
    for index in range(period, len(variations)):
        gain_moyen = (gain_moyen * (period - 1) + gains[index]) / period
        perte_moyenne = (perte_moyenne * (period - 1) + pertes[index]) / period

    if perte_moyenne == 0:
        return 100.0 if gain_moyen > 0 else 50.0
    force = gain_moyen / perte_moyenne
    return 100.0 - (100.0 / (1.0 + force))


class RSIAgent:
    """Calcule le RSI d'un token depuis ses bougies. Ne décide rien."""

    def __init__(self, log_path: Optional[str] = None, period: int = DEFAULT_PERIOD):
        self.log_path = log_path
        self.period = period

    def read(self, candles: Sequence[Candle]) -> RSIReading:
        """Lecture depuis les bougies déjà construites par `PriceHistory`.

        Prend les CLÔTURES et pas les moyennes haut/bas : sur des bougies
        reconstruites depuis deux snapshots, haut et bas sont souvent l'un des
        deux points de clôture, et les moyenner ajouterait un lissage qui
        n'existe pas dans les données.
        """
        closes = [c.close for c in candles if c.close > 0]
        if len(closes) < self.period + 1:
            return RSIReading(
                value=None, zone="inconnu", period=self.period, samples=len(closes),
                reason=(
                    f"{len(closes)} clôtures pour une période de {self.period} — "
                    f"il en faut {self.period + 1}"
                ),
            )

        value = compute_rsi(closes, self.period)
        if value is None:
            return RSIReading(
                value=None, zone="inconnu", period=self.period, samples=len(closes),
                reason="historique insuffisant",
            )
        return RSIReading(
            value=value, zone=_zone(value), period=self.period, samples=len(closes),
            reason=f"RSI({self.period}) sur {len(closes)} clôtures reconstruites",
        )

    def observe(
        self,
        token_address: str,
        symbol: str,
        candles: Sequence[Candle],
        extra: Optional[dict[str, Any]] = None,
    ) -> RSIReading:
        """Lit et journalise. Une lecture inconnue est journalisée AUSSI.

        Ne garder que les lectures connues biaiserait toute analyse ultérieure :
        on ne saurait plus distinguer « le RSI n'était jamais en surachat » de
        « on n'a jamais eu assez de bougies pour le savoir ».

        `extra` porte le contexte du trade (`position_id`, bras, P&L). Sans lui
        la ligne ne se joint à rien, et un indicateur qu'on ne peut pas relier
        à un résultat ne s'évalue jamais.
        """
        reading = self.read(candles)
        if self.log_path:
            _journal.append(self.log_path, {
                "ts": time.time(),
                "token_address": token_address,
                "symbol": symbol,
                **reading.as_dict(),
                **(extra or {}),
            })
        return reading
