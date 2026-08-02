"""Agents de mesure — ils CALCULENT, ils n'apprennent pas.

LA DISTINCTION QUI ORGANISE CE PAQUET. Un agent qui apprend un paramètre a
besoin d'un échantillon : `MIN_SEGMENT_SAMPLE`, `EXIT_BACKTEST_MIN_COVERAGE`
et `Interval.conclusive` existent pour empêcher d'ajuster sur du bruit. Au
2026-08-02 le dépôt compte 93 trades clôturés, tous bras confondus — greffer
une douzaine d'agents apprenants là-dessus produirait douze surapprentissages
parallèles.

Les agents de ce paquet ne décident rien et n'écrivent aucun paramètre. Ils
transforment de la donnée déjà collectée en mesure exploitable, et JOURNALISENT.
Ils produisent l'échantillon dont la couche d'apprentissage aura besoin ;
l'inverse — écrire les apprenants d'abord — donnerait des lecteurs de fichiers
vides.

INVARIANT COMMUN, hérité du pipeline : **une donnée absente ne rejette jamais**
et ne s'invente pas. Chaque agent rend `None` quand il ne sait pas, et le dit.
« Non mesuré » et « mesuré à zéro » sont deux états différents.
"""

from src.agents.counterfactual_timing import CounterfactualTimingAgent
from src.agents.dev_history import DevHistoryAgent
from src.agents.microstructure_agent import MicrostructureAgent
from src.agents.rsi_agent import RSIAgent
from src.agents.telegram_reporter import TelegramReporterAgent
from src.agents.volatility_agent import VolatilityAgent

__all__ = [
    "CounterfactualTimingAgent",
    "DevHistoryAgent",
    "MicrostructureAgent",
    "RSIAgent",
    "TelegramReporterAgent",
    "VolatilityAgent",
]
