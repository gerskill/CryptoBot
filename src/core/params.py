"""Chargement / sauvegarde de params.json (source de vérité des règles)."""

import copy
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from typing import Any


# Paramètres présents dans la config mais lus par AUCUN code : les afficher
# comme des règles en vigueur ferait croire à une protection inexistante.
# Clé = chemin pointé, valeur = raison. `tests/test_parametres_appliques.py`
# vérifie que chaque clé listée est bien absente du code (sinon la liste ment).
PARAMETRES_NON_APPLIQUES: dict[str, str] = {
    "entry_rules.min_liquidity_lock_days": "RugCheck expose lpLockedPct, pas une durée de verrou : aucune source.",
    "filters.max_tax_buy": "Aucun code ne lit cette clé : la taxe d'achat n'est contrôlée nulle part (la blacklist honeypot est un autre mécanisme).",
    "filters.max_tax_sell": "Aucun code ne lit cette clé : la taxe de vente n'est contrôlée nulle part.",
    "entry_rules.slippage_max_pct": "Aucun code ne lit cette clé : le slippage maximal à l'entrée n'est pas plafonné ; le coût de sortie vient de exit_fees.",
    "entry_rules.entry_timing": "Aucun code ne lit cette clé : l'entrée n'attend aucun déclencheur « breakout_5m », c'est une étiquette.",
    "entry_rules.position_size_formula": "Clé non lue : la taille vient toujours de PaperPortfolio.position_size (capital × risk_per_trade, majorée façon Kelly au-delà de 40 % de réussite sur 20 trades, plafonnée à 5 % du capital), quelle que soit sa valeur.",
}

# Paramètres lus sous un nom construit (f-string, boucle) : un balayage de
# littéraux ne les voit pas. Valeur = endroit du code qui les lit.
PARAMETRES_LUS_DYNAMIQUEMENT: dict[str, str] = {}


class ParamsStore:
    """Accès thread-safe à params.json avec historique des ajustements."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> dict[str, Any]:
        with self._lock:
            with open(self.path, encoding="utf-8") as fh:
                self._data = json.load(fh)
            return copy.deepcopy(self._data)

    @property
    def data(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def get(self, dotted_path: str, default: Any = None) -> Any:
        """Lecture par chemin : get('filters.min_liquidity_usd')."""
        with self._lock:
            node: Any = self._data
            for part in dotted_path.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def set(
        self,
        dotted_path: str,
        value: Any,
        reason: str = "",
        sample_size: int = 0,
        log: bool = True,
    ) -> None:
        """Écrit un paramètre et journalise l'ajustement (étape 6.5 du spec).

        `log=False` pour les écritures de pure comptabilité (rafraîchissement
        des stats, marqueurs de cadence) : sans ça l'historique se remplirait
        d'entrées qui ne sont pas des décisions et deviendrait illisible.
        """
        parts = dotted_path.split(".")
        with self._lock:
            node = self._data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            old_value = node.get(parts[-1])
            node[parts[-1]] = value

            if log:
                history = self._data.setdefault("learning", {}).setdefault(
                    "parameter_adjustment_history", []
                )
                history.append(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "param_name": dotted_path,
                        "old_value": old_value,
                        "new_value": value,
                        "reason": reason,
                        "trades_sample_size": sample_size,
                    }
                )
            self._write()
        if log:
            print(f"🧠 LEARNING | {dotted_path} : {old_value} → {value} | Raison : {reason}")

    def save(self) -> None:
        with self._lock:
            self._write()

    def _write(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, ensure_ascii=False)
            os.replace(tmp_path, self.path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
