"""Budget mensuel d'appels API, persistant entre les redémarrages.

POURQUOI CE MODULE EXISTE
-------------------------
Le limiteur de débit (`RateLimiter`) protège du « trop vite ». Il ne protège
pas du « trop au total ». Twitter/X plafonne au MOIS : 100 posts en Free,
15 000 en Basic. Or interroger 3 tokens par cycle de 90 s consomme 172 800
requêtes par mois — 1 700 fois le plafond Free.

Résultat observé : quota épuisé en quelques heures, puis `402 Payment
Required` sur tous les appels pendant le reste du mois.

STRATÉGIE
---------
1. Compteur persistant par mois calendaire, remis à zéro au changement de mois.
2. Garde-fou JOURNALIER : on ne peut pas brûler le mois en une journée. La
   part quotidienne est le budget restant divisé par les jours restants.
3. Réserve de fin de mois : une fraction est gardée pour les jours creux.

Le budget se consulte AVANT de dépenser, jamais après.
"""

import json
import os
import tempfile
import threading
from calendar import monthrange
from datetime import date
from typing import Any, Optional

# Part du budget mensuel gardée en réserve, non consommable par le lissage
# quotidien. Évite qu'un pic d'activité en début de mois assèche la fin.
RESERVE_FRACTION = 0.10


class MonthlyBudget:
    """Compteur d'appels borné au mois, avec lissage quotidien."""

    def __init__(self, path: str, name: str, monthly_limit: int):
        self.path = path
        self.name = name
        self.monthly_limit = max(0, int(monthly_limit))
        self._lock = threading.Lock()
        self._month = self._current_month()
        self._used = 0
        self._used_today = 0
        self._day = date.today().isoformat()
        self._load()

    @staticmethod
    def _current_month() -> str:
        today = date.today()
        return f"{today.year}-{today.month:02d}"

    # ------------------------------------------------------------- décision

    def can_spend(self, count: int = 1) -> bool:
        """True si `count` appels tiennent dans le budget mensuel ET du jour."""
        with self._lock:
            self._roll_over()
            if self._used + count > self.monthly_limit:
                return False
            return self._used_today + count <= self._daily_allowance()

    def spend(self, count: int = 1) -> bool:
        """Consomme le budget. False si refusé — l'appel ne doit pas partir."""
        with self._lock:
            self._roll_over()
            if self._used + count > self.monthly_limit:
                return False
            if self._used_today + count > self._daily_allowance():
                return False
            self._used += count
            self._used_today += count
            self._save()
            return True

    def _daily_allowance(self) -> int:
        """Part du jour, calculée sur le budget disponible à l'OUVERTURE du jour.

        Stable pendant toute la journée : la baser sur `_used` courant la
        ferait varier à chaque dépense, et la part augmenterait au fur et à
        mesure qu'on la consomme — l'inverse d'un plafond.

        Un mois qui commence donne une part large ; un mois presque consommé
        la resserre automatiquement. Aucun réglage manuel.
        """
        today = date.today()
        days_in_month = monthrange(today.year, today.month)[1]
        days_left = max(1, days_in_month - today.day + 1)
        reserve = int(self.monthly_limit * RESERVE_FRACTION)
        used_before_today = self._used - self._used_today
        spendable = max(0, self.monthly_limit - reserve - used_before_today)
        # Toujours au moins 1 : un budget serré ne doit pas tout bloquer.
        return max(1, spendable // days_left)

    # ------------------------------------------------------------- rapport

    @property
    def remaining(self) -> int:
        with self._lock:
            self._roll_over()
            return max(0, self.monthly_limit - self._used)

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            self._roll_over()
            return {
                "name": self.name,
                "monthly_limit": self.monthly_limit,
                "used": self._used,
                "remaining": max(0, self.monthly_limit - self._used),
                "used_today": self._used_today,
                "daily_allowance": self._daily_allowance(),
                "month": self._month,
                "pct_used": round(100 * self._used / self.monthly_limit, 1)
                if self.monthly_limit
                else 100.0,
            }

    # --------------------------------------------------------- persistance

    def _roll_over(self) -> None:
        """Remet les compteurs à zéro au changement de mois ou de jour."""
        month = self._current_month()
        if month != self._month:
            self._month, self._used = month, 0
            self._used_today, self._day = 0, date.today().isoformat()
            self._save()
            return
        today = date.today().isoformat()
        if today != self._day:
            self._day, self._used_today = today, 0
            self._save()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh).get(self.name, {})
        except (json.JSONDecodeError, OSError):
            return
        if data.get("month") == self._month:
            self._used = int(data.get("used", 0))
            if data.get("day") == self._day:
                self._used_today = int(data.get("used_today", 0))

    def _save(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        try:
            with open(self.path, encoding="utf-8") as fh:
                everything = json.load(fh)
        except (json.JSONDecodeError, OSError, FileNotFoundError):
            everything = {}

        everything[self.name] = {
            "month": self._month,
            "used": self._used,
            "day": self._day,
            "used_today": self._used_today,
            "monthly_limit": self.monthly_limit,
        }
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(everything, fh, ensure_ascii=False)
            os.replace(tmp_path, self.path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


def load_budgets(path: str, limits: dict[str, int]) -> dict[str, MonthlyBudget]:
    """Construit les budgets déclarés dans params.json."""
    return {
        name: MonthlyBudget(path, name, limit)
        for name, limit in limits.items()
        if limit and limit > 0
    }
