"""Modifications demandées depuis le site, appliquées par la simulation.

INVARIANT : l'API n'écrit jamais dans les fichiers dont un autre processus
est propriétaire. Elle dépose donc des DEMANDES dans une boîte qu'elle est
seule à écrire (`requests.jsonl`, ajout en fin de fichier). La simulation les
lit au début de chaque cycle, les valide, et tient à jour SA surcouche
(`arms_local.json`) par écriture atomique. `config/prediction_arms.json`
(versionné) n'est jamais réécrit.

Deux actions seulement, voir ADR 017 :
  - `toggle` : activer ou désactiver un bras ;
  - `fork`   : créer un bras qui ne diffère de son parent que par UNE variable
               bornée. Un bras existant n'est jamais modifié : ses paris déjà
               pris ne seraient plus comparables.
"""

import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from src.prediction.paper import _write_atomic, append_jsonl, read_json, read_jsonl

NAME_PATTERN = re.compile(r"^pm_[a-z0-9_]{3,30}$")
MAX_FORKS = 20

# Variables qu'un fork a le droit de changer, par rôle du parent, avec bornes.
BOUNDS: dict[str, tuple[float, float]] = {
    "edge_min": (0.01, 0.30),
    "max_jours": (0.05, 60),
    "min_minutes": (1, 1440),
}
VARIABLES: dict[str, tuple[str, ...]] = {
    "temoin": ("fourchette", "max_jours", "min_minutes"),
    "regle": ("fourchette", "cote", "max_jours", "min_minutes"),
    "parieur": ("edge_min", "estimateur", "max_jours", "min_minutes"),
}
ESTIMATEURS = ("empirique", "lognormal")


def requests_path(data_dir: str) -> str:
    return os.path.join(data_dir, "requests.jsonl")


def overlay_path(data_dir: str) -> str:
    return os.path.join(data_dir, "arms_local.json")


def log_path(data_dir: str) -> str:
    return os.path.join(data_dir, "requests_log.jsonl")


def read_overlay(data_dir: str) -> dict[str, Any]:
    data = read_json(overlay_path(data_dir), None)
    if not isinstance(data, dict):
        data = {}
    return {"enabled": dict(data.get("enabled") or {}), "forks": list(data.get("forks") or []),
            "applied": list(data.get("applied") or [])}


def effective_config(config: dict[str, Any], data_dir: str) -> dict[str, Any]:
    """Configuration versionnée + surcouche locale. Ne modifie pas `config`."""
    overlay = read_overlay(data_dir)
    arms = [dict(arm) for arm in config.get("arms", [])] + [dict(f) for f in overlay["forks"]]
    for arm in arms:
        if arm.get("name") in overlay["enabled"]:
            arm["enabled"] = bool(overlay["enabled"][arm["name"]])
        arm["cree_depuis_le_site"] = any(f.get("name") == arm.get("name") for f in overlay["forks"])
    return dict(config, arms=arms)


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def build_fork(arms: list[dict[str, Any]], request: dict[str, Any],
               moment: datetime) -> tuple[Optional[dict[str, Any]], str]:
    """Le bras fork, ou None et la raison du refus."""
    parent = next((a for a in arms if a.get("name") == request.get("parent")), None)
    if parent is None:
        return None, "parent_inconnu"
    allowed = VARIABLES.get(parent.get("role"), ())
    variable, value = request.get("variable"), request.get("value")
    name = str(request.get("name") or "")
    if not NAME_PATTERN.match(name):
        return None, "nom_invalide"
    if any(a.get("name") == name for a in arms):
        return None, "nom_deja_pris"
    if variable not in allowed:
        return None, "variable_non_autorisee_pour_ce_role"

    change: dict[str, Any] = {}
    if variable == "fourchette":
        low = _number(value.get("prix_min")) if isinstance(value, dict) else None
        high = _number(value.get("prix_max")) if isinstance(value, dict) else None
        if low is None or high is None or not 0.02 <= low < high <= 0.98:
            return None, "fourchette_hors_bornes"
        change, shown = {"prix_min": low, "prix_max": high}, f"{low:g} à {high:g}"
        variable_label = "prix_min/prix_max"
    elif variable == "cote":
        if value not in ("favori", "outsider"):
            return None, "cote_inconnue"
        change, shown, variable_label = {"cote": value}, value, "cote"
    elif variable == "estimateur":
        if value not in ESTIMATEURS or parent.get("estimateur") not in ESTIMATEURS:
            return None, "estimateur_non_autorise"
        change, shown, variable_label = {"estimateur": value}, value, "estimateur"
    else:
        number = _number(value)
        low, high = BOUNDS[variable]
        if number is None or not low <= number <= high:
            return None, f"{variable}_hors_bornes"
        change, shown, variable_label = {variable: number}, f"{number:g}", variable
    if all(parent.get(k) == v for k, v in change.items()):
        return None, "identique_au_parent"

    fork = {k: v for k, v in parent.items() if k != "cree_depuis_le_site"}
    fork.update(change)
    fork.update({
        "name": name, "enabled": True, "parent": parent["name"], "variable": variable_label,
        "description": (f"Fork de {parent['name']} créé depuis le site le "
                        f"{moment.strftime('%Y-%m-%d')}, une seule variable changée : "
                        f"{variable_label} = {shown}."),
    })
    return fork, ""


def apply_requests(config: dict[str, Any], data_dir: str, moment: datetime) -> int:
    """Applique les demandes en attente. Renvoie le nombre de demandes traitées."""
    pending = read_jsonl(requests_path(data_dir))
    if not pending:
        return 0
    overlay = read_overlay(data_dir)
    done = set(overlay["applied"])
    treated = 0
    for request in pending:
        request_id = request.get("id")
        if not request_id or request_id in done:
            continue
        # Relu à chaque demande : la surcouche vient peut-être de changer.
        arms = effective_config(config, data_dir)["arms"]
        ok, reason = False, "action_inconnue"
        if request.get("action") == "toggle":
            if any(a.get("name") == request.get("arm") for a in arms):
                overlay["enabled"][request["arm"]] = bool(request.get("enabled"))
                ok, reason = True, ""
            else:
                reason = "bras_inconnu"
        elif request.get("action") == "fork":
            if len(overlay["forks"]) >= MAX_FORKS:
                reason = "trop_de_forks"
            else:
                fork, reason = build_fork(arms, request, moment)
                if fork is not None:
                    overlay["forks"].append(fork)
                    ok = True
        overlay["applied"].append(request_id)
        done.add(request_id)
        treated += 1
        # Surcouche écrite avant le journal : une coupure laisse une demande
        # appliquée sans ligne de journal, jamais une demande rejouée.
        _write_atomic(overlay_path(data_dir), overlay)
        append_jsonl(log_path(data_dir), {
            "id": request_id, "t": moment.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "action": request.get("action"), "ok": ok, "raison": reason,
            "demande": {k: v for k, v in request.items() if k not in ("id",)}})
    return treated


def request_status(data_dir: str, limit: int = 50) -> list[dict[str, Any]]:
    """Demandes déposées, avec leur sort : en attente, appliquée ou refusée."""
    outcome = {row.get("id"): row for row in read_jsonl(log_path(data_dir))}
    rows = []
    for request in read_jsonl(requests_path(data_dir))[-limit:]:
        result = outcome.get(request.get("id"))
        rows.append(dict(request, statut=("en attente" if result is None
                                           else "appliquée" if result.get("ok") else "refusée"),
                         raison=(result or {}).get("raison") or None))
    return rows
