"""Agent de quota — ajuste le débit de consommation API sur le RYTHME mesuré.

POURQUOI CE MODULE EXISTE. `RateLimiter` protège du « trop vite », `MonthlyBudget`
protège du « trop au total » (voir `src/core/budget.py`). Aucun des deux
n'ajuste QUAND dépenser à l'intérieur du mois : `scan.social_max_lookups_per_cycle`
est une constante de `params.json`, réglée une fois à la main et jamais revue —
exactement le point faible visé : une réduction statique n'apprend rien de la
consommation réelle.

CE QUE FAIT CET AGENT, ET RIEN DE PLUS. Il compare le RYTHME de consommation
mesuré (`MonthlyBudget.stats["pct_used"]`) au rythme attendu (jour du mois /
jours du mois), et resserre ou desserre `scan.social_max_lookups_per_cycle` en
conséquence — même style que `LearningEngine._relax_from_inactivity` : pas
d'appel LLM, une règle déterministe qui s'auto-calibre sur des données
mesurées, bornée, journalisée dans `parameter_adjustment_history` comme tout
le reste des ajustements du dépôt.

GARDE-FOU : aucun ajustement sans `MIN_CYCLES_SAMPLE` cycles de demande
observée — même invariant que `MIN_SEGMENT_SAMPLE` dans `learning.py`, pour ne
pas réagir au bruit d'un seul cycle.

RÉSERRER QUAND LE RYTHME EST CHAUD (`pct_used` dépasse le rythme attendu de
plus de `PACE_MARGIN_PCT` points) — le budget serait épuisé avant la fin du
mois au rythme actuel. DESSERRER QUAND LE RYTHME EST FROID ET QUE LA DEMANDE
DÉPASSE CE QUI EST SERVI (`wanted > served` en moyenne sur la fenêtre) — du
budget dort alors que des bras attendent une donnée sociale qu'ils n'obtiennent
pas.
"""

from collections import deque
from datetime import date
from typing import Any, Callable, Optional

from src.core.params import ParamsStore

MIN_CYCLES_SAMPLE = 20
DEMAND_WINDOW = 40
PACE_MARGIN_PCT = 15.0
STEP = 1

# Association budget mensuel -> paramètre qu'il gouverne. Un seul couple pour
# l'instant (Twitter, seul budget mensuel réellement plafonné dans le dépôt —
# voir `src/core/budget.py`) ; ajouter une entrée suffit à couvrir un futur
# budget sans toucher au reste de l'agent.
QUOTA_PARAM_BY_BUDGET = {
    "twitter": "scan.social_max_lookups_per_cycle",
}

# BORNES DURES — même rôle que `PARAM_BOUNDS` dans `learning.py` : sans elles,
# un resserrage répété convergerait vers zéro lookup, ce qui reviendrait à
# désactiver l'enrichissement social sans jamais le dire explicitement.
QUOTA_BOUNDS: dict[str, tuple[int, int]] = {
    "scan.social_max_lookups_per_cycle": (1, 6),
}


class QuotaAgent:
    """Recalibre le débit de consommation API sur le rythme mesuré, pas sur une constante figée."""

    def __init__(
        self,
        params: ParamsStore,
        bounds: Optional[dict[str, tuple[int, int]]] = None,
        today: Optional[Callable[[], date]] = None,
    ):
        self.params = params
        self.bounds = {**QUOTA_BOUNDS, **(bounds or {})}
        # Injecté pour les tests : `date.today()` rendrait le rythme attendu
        # non reproductible d'une exécution à l'autre.
        self._today = today or date.today
        self._demand: dict[str, deque] = {}

    def record_demand(self, name: str, wanted: int, served: int) -> None:
        """Un cycle de plus : combien de lookups un bras aurait voulus vs obtenus."""
        self._demand.setdefault(name, deque(maxlen=DEMAND_WINDOW)).append((wanted, served))

    def recalibrate(self, budgets: dict[str, Any]) -> list[str]:
        """Un ajustement au plus par budget connu. Retourne les messages émis."""
        changes = []
        for name, budget in budgets.items():
            param_path = QUOTA_PARAM_BY_BUDGET.get(name)
            if param_path is None:
                continue
            message = self._recalibrate_one(name, budget, param_path)
            if message:
                changes.append(message)
        return changes

    def _recalibrate_one(self, name: str, budget: Any, param_path: str) -> Optional[str]:
        samples = self._demand.get(name)
        if samples is None or len(samples) < MIN_CYCLES_SAMPLE:
            return None

        stats = budget.stats
        if not stats.get("monthly_limit"):
            return None

        today = self._today()
        days_in_month = _days_in_month(today)
        expected_pct = 100.0 * today.day / days_in_month
        pace_gap = stats["pct_used"] - expected_pct

        current = self.params.get(param_path)
        if current is None:
            return None
        low, high = self.bounds.get(param_path, (current, current))

        if pace_gap > PACE_MARGIN_PCT and current > low:
            new_value = max(low, current - STEP)
            if new_value == current:
                return None
            reason = (
                f"rythme {name} chaud : {stats['pct_used']:.1f}% consommé pour "
                f"{expected_pct:.1f}% attendu (jour {today.day}/{days_in_month}) "
                f"— {param_path} resserré"
            )
            self.params.set(param_path, new_value, reason, len(samples))
            return reason

        mean_wanted = sum(w for w, _ in samples) / len(samples)
        mean_served = sum(s for _, s in samples) / len(samples)
        unmet_demand = mean_wanted > mean_served + 1e-9

        if pace_gap < -PACE_MARGIN_PCT and unmet_demand and current < high:
            new_value = min(high, current + STEP)
            if new_value == current:
                return None
            reason = (
                f"rythme {name} froid : {stats['pct_used']:.1f}% consommé pour "
                f"{expected_pct:.1f}% attendu (jour {today.day}/{days_in_month}), "
                f"demande non servie ({mean_wanted:.1f} voulus vs {mean_served:.1f} servis "
                f"en moyenne) — {param_path} desserré"
            )
            self.params.set(param_path, new_value, reason, len(samples))
            return reason

        return None


def _days_in_month(today: date) -> int:
    from calendar import monthrange

    return monthrange(today.year, today.month)[1]
