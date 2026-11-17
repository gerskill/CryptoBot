"""Plafond de crédits Helius pour les scripts de recherche.

Pourquoi : le 5 novembre, cinq scripts de recherche ont consommé 490 700 crédits
Enhanced (48,9 % du quota) en une soirée. Un appel Enhanced Transactions coûte
100 crédits. Ce module compte chaque appel AVANT qu'il parte et s'arrête au
plafond, au lieu de découvrir la facture après coup.

Plafond par exécution : variable HELIUS_SCRIPT_CREDIT_CAP (en crédits), défaut
50 000, soit 500 appels Enhanced. Le bot n'utilise pas Enhanced : ce plafond ne
le touche pas.
"""
from __future__ import annotations

import os

ENHANCED_CREDITS_PER_CALL = 100
DEFAULT_CAP_CREDITS = 50_000


class CreditBudget:
    """Compteur d'une exécution de script. `charge()` dit si l'appel peut partir."""

    def __init__(self, name: str = "script", cap: int | None = None) -> None:
        raw = (os.getenv("HELIUS_SCRIPT_CREDIT_CAP") or "").strip()
        if raw.isdigit():
            self.cap = int(raw)
        else:
            self.cap = cap if cap is not None else DEFAULT_CAP_CREDITS
        self.spent = 0
        self.name = os.path.basename(name)
        self._stopped = False

    def charge(self, credits: int = ENHANCED_CREDITS_PER_CALL) -> bool:
        if self.spent + credits > self.cap:
            if not self._stopped:
                self._stopped = True
                print(
                    f"[{self.name}] plafond Helius atteint : {self.spent}/{self.cap} "
                    "crédits — arrêt des appels Enhanced"
                )
            return False
        self.spent += credits
        return True
