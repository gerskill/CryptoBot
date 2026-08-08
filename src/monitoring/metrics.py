"""Métriques du bot au format texte Prometheus — lecture seule.

MÊME RÈGLE QUE `api/server.py` : ce module ne fait AUCUN appel réseau et ne
consomme aucun quota (DexScreener/Birdeye/Helius/Twitter/...). Il lit
uniquement les fichiers que le bot écrit déjà — `state.json` et les journaux
par bras via `settings.arm_paths` — exactement les mêmes sources que le reste
de l'API. Aucun accès direct au bot, aucune logique métier dupliquée : les
quotas API viennent de `state["budgets"]`, déjà calculés par
`MonthlyBudget.stats` (voir `src/core/budget.py`) et publiés par le bot à
chaque cycle — pas d'un nouveau parsing de `data/api_budgets.json`.

FORMAT : texte Prometheus (`# HELP` / `# TYPE` / `nom{labels} valeur`) écrit à
la main. Le format est trivial à générer ; `prometheus_client` n'apporte rien
ici et n'a donc pas été ajouté à `requirements.txt`.
"""

import json
import os
import time
from typing import Any, Optional

from src import settings
from src.core.arm import load_manifest
from src.core.journal import TradeJournal

# Même seuil que `api/server.py::_state()` — un state.json plus vieux que ça
# signifie que le bot ne tourne plus, pas qu'il n'a rien trouvé.
STALE_AFTER_SECONDS = 180


def _read_json(path: str) -> Optional[dict[str, Any]]:
    """Copie volontaire de `api/server.py::_read_json`.

    `api/server.py` importe ce module ; l'inverse créerait un cycle. La
    fonction fait huit lignes et n'a pas de logique propre au dashboard —
    la dupliquer coûte moins qu'un import circulaire ou un module utilitaire
    à trois fonctions.
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def _escape_label(value: str) -> str:
    """Échappe une valeur de label Prometheus : antislash, guillemets, retour ligne."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class MetricFamily:
    """Une métrique nommée : aide, type, et échantillons (labels, valeur).

    `add(None, ...)` est un no-op délibéré : une valeur absente (bot jamais
    démarré, budget non configuré) ne doit PAS apparaître comme un zéro
    trompeur dans la sortie Prometheus — elle doit simplement être absente.
    """

    def __init__(self, name: str, help_text: str, metric_type: str = "gauge"):
        self.name = name
        self.help_text = help_text
        self.metric_type = metric_type
        self.samples: list[tuple[dict[str, str], float]] = []

    def add(self, value: Optional[float], labels: Optional[dict[str, str]] = None) -> None:
        if value is None:
            return
        self.samples.append((labels or {}, float(value)))

    def render(self) -> str:
        if not self.samples:
            return ""
        lines = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} {self.metric_type}",
        ]
        for labels, value in self.samples:
            if labels:
                label_str = ",".join(
                    f'{key}="{_escape_label(str(val))}"' for key, val in labels.items()
                )
                lines.append(f"{self.name}{{{label_str}}} {value}")
            else:
                lines.append(f"{self.name} {value}")
        return "\n".join(lines)


def _arm_trade_families(manifest: list[dict[str, Any]]) -> list[MetricFamily]:
    """Trades, P&L et win rate par bras — depuis le journal de chaque bras.

    Même lecture que `GET /api/trades?arm=all` dans `api/server.py` : les
    bras désactivés sont exclus, `TradeJournal.read_positions()` agrège les
    jambes d'une même position (voir `src/core/journal.py`), pas de recalcul
    parallèle des règles d'agrégation.
    """
    trades = MetricFamily(
        "cryptobbot_arm_trades_total",
        "Nombre de positions closes (jambes agrégées) par bras.",
        "gauge",
    )
    pnl = MetricFamily(
        "cryptobbot_arm_pnl_usd",
        "P&L cumulé en dollars par bras, sur les positions closes.",
        "gauge",
    )
    win_rate = MetricFamily(
        "cryptobbot_arm_win_rate_pct",
        "Taux de trades gagnants par bras, en pourcentage.",
        "gauge",
    )

    for entry in manifest:
        name = entry.get("name")
        if not name:
            continue
        labels = {"arm": name}
        path = settings.arm_paths(name)["trades"]
        positions = TradeJournal(path).read_positions() if os.path.exists(path) else []

        trades.add(len(positions), labels)
        if not positions:
            continue

        total_pnl = sum(row.get("pnl_usd") or 0.0 for row in positions)
        winners = sum(1 for row in positions if (row.get("pnl_usd") or 0.0) > 0)
        pnl.add(round(total_pnl, 4), labels)
        win_rate.add(round(100 * winners / len(positions), 2), labels)

    return [trades, pnl, win_rate]


def _cycle_families(state: Optional[dict[str, Any]]) -> list[MetricFamily]:
    """Fraîcheur, cycles et scans — tout vient de `state.json`.

    `bot_online` et `state_age_seconds` reproduisent le calcul de
    `api/server.py::_state()` (même seuil `STALE_AFTER_SECONDS`) sans en
    dépendre : ce module doit rester importable sans FastAPI.
    """
    bot_online = MetricFamily(
        "cryptobbot_bot_online",
        "1 si state.json a été mis à jour dans les dernières "
        f"{STALE_AFTER_SECONDS} s, 0 sinon.",
        "gauge",
    )
    state_age = MetricFamily(
        "cryptobbot_state_age_seconds", "Âge du dernier state.json, en secondes.", "gauge"
    )
    cycle_total = MetricFamily(
        "cryptobbot_cycle_total", "Numéro du cycle de scan courant.", "counter"
    )
    cycle_duration = MetricFamily(
        "cryptobbot_cycle_duration_seconds",
        "Durée du dernier cycle de scan complet, en secondes.",
        "gauge",
    )
    scanned = MetricFamily(
        "cryptobbot_scan_tokens_scanned",
        "Nombre de tokens scannés au dernier cycle.",
        "gauge",
    )
    cached_skipped = MetricFamily(
        "cryptobbot_scan_tokens_cached_skipped",
        "Tokens ignorés (déjà en cache) au dernier cycle.",
        "gauge",
    )
    scan_duration = MetricFamily(
        "cryptobbot_scan_duration_seconds",
        "Durée du dernier scan de tokens, en secondes.",
        "gauge",
    )

    families = [
        bot_online, state_age, cycle_total, cycle_duration,
        scanned, cached_skipped, scan_duration,
    ]

    if state is None:
        bot_online.add(0.0)
        return families

    age = time.time() - float(state.get("updated_at", 0))
    bot_online.add(1.0 if age < STALE_AFTER_SECONDS else 0.0)
    state_age.add(round(age, 1))
    cycle_total.add(state.get("cycle"))
    cycle_duration.add(state.get("cycle_duration_sec"))

    scan = state.get("scan") or {}
    scanned.add(scan.get("scanned"))
    cached_skipped.add(scan.get("cached_skipped"))
    scan_duration.add(scan.get("duration_sec"))

    return families


def _quota_families(state: Optional[dict[str, Any]]) -> list[MetricFamily]:
    """Quotas API restants par service, depuis `state["budgets"]`.

    `state["budgets"]` est déjà `{nom: MonthlyBudget.stats}` (voir
    `src/main.py::_publish_state` et `src/core/budget.py::MonthlyBudget.stats`)
    — publié par le bot à chaque cycle, sans appel réseau pour le relire ici.
    """
    used_pct = MetricFamily(
        "cryptobbot_api_quota_used_pct",
        "Pourcentage du quota mensuel consommé, par service.",
        "gauge",
    )
    remaining = MetricFamily(
        "cryptobbot_api_quota_remaining",
        "Appels restants ce mois-ci avant plafond, par service.",
        "gauge",
    )
    monthly_limit = MetricFamily(
        "cryptobbot_api_quota_monthly_limit",
        "Plafond mensuel d'appels configuré, par service.",
        "gauge",
    )

    budgets = (state or {}).get("budgets") or {}
    for service, stats in budgets.items():
        if not isinstance(stats, dict):
            continue
        labels = {"service": service}
        used_pct.add(stats.get("pct_used"), labels)
        remaining.add(stats.get("remaining"), labels)
        monthly_limit.add(stats.get("monthly_limit"), labels)

    return [used_pct, remaining, monthly_limit]


def _availability_families(state: Optional[dict[str, Any]]) -> list[MetricFamily]:
    """Disponibilité des intégrations et des capacités, depuis `state.json`.

    Réutilise `state["api_status"]` et `state["capabilities"]`, déjà calculés
    par `src/core/capabilities.py::CapabilityRegistry` et publiés par le bot —
    aucun système de health check parallèle n'est réinventé ici.
    """
    service_up = MetricFamily(
        "cryptobbot_api_service_up",
        "1 si le service API est actif, 0 si désactivé ou dégradé, par service.",
        "gauge",
    )
    capability_blind = MetricFamily(
        "cryptobbot_capability_blind",
        "1 si une capacité (bougies, holders, prix, ...) n'a plus aucun "
        "fournisseur disponible, 0 sinon.",
        "gauge",
    )

    api_status = (state or {}).get("api_status") or {}
    for service, up in api_status.items():
        service_up.add(1.0 if up else 0.0, {"service": service})

    for capability in (state or {}).get("capabilities") or []:
        name = capability.get("capability")
        if not name:
            continue
        capability_blind.add(1.0 if capability.get("blind") else 0.0, {"capability": name})

    return [service_up, capability_blind]


def generate_metrics_text() -> str:
    """Texte au format d'exposition Prometheus, prêt pour `GET /api/metrics`."""
    state = _read_json(settings.STATE_PATH)
    manifest = [entry for entry in load_manifest() if entry.get("enabled", True)]

    families: list[MetricFamily] = []
    families.extend(_arm_trade_families(manifest))
    families.extend(_cycle_families(state))
    families.extend(_quota_families(state))
    families.extend(_availability_families(state))

    blocks = [block for block in (family.render() for family in families) if block]
    return ("\n".join(blocks) + "\n") if blocks else ""
