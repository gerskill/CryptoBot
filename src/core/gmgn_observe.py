"""Observation GMGN après sélection, hors de la boucle de trading.

Ce module ne décide rien. Il ne refuse pas une entrée, n'écrit aucun
paramètre, et n'est pas importé par `src.main`. Un processus séparé lit
`data/state.json` et écrit seulement sous `data/gmgn_observe/`.

Le pipeline ne l'attend pas. Un résultat daté n'est pas une garantie :
un champ absent reste inconnu (`None`), jamais un zéro inventé.

Budget propre : 3 appels `gmgn-cli` par heure au total, pas le limiteur
à 60/min du client de la boucle. La boucle consomme GMGN de son côté ;
ce plafond ne se coordonne pas avec elle. Il évite seulement d'en rajouter.

Incident du 2026-11-08 16:27 Zurich : deux 429 `RATE_LIMIT_BANNED`, levée
annoncée 17:27:31 Zurich. Le premier démarrage pose ce cooldown et ne lance
aucun sous-processus tant qu'il tient.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

MEASURE_VERSION = "gmgn-observe-1"
# 3 commandes = un mint complet (security, holders, traders) par heure.
# Holders et traders pèsent plus lourd chez GMGN que `token security`.
# Rester à 3/h évite d'empiler un second limiteur « 60/min » sur la boucle.
MAX_CALLS_PER_HOUR = 3
CALL_WINDOW_SECONDS = 3600.0
CACHE_TTL_SECONDS = 24 * 3600.0
QUEUE_MAX = 4
MAX_STDOUT_BYTES = 200_000
COMMAND_TIMEOUT_SECONDS = 15.0
# Pas de drapeau documenté pour couper le retry interne du CLI.
# Un seul processus est lancé ; à 15 s il est abandonné, sans second essai.
AUTH_PAUSE_SECONDS = 24 * 3600.0
UNKNOWN_BAN_PAUSE_SECONDS = 6 * 3600.0
MAX_OBSERVATIONS = 200
MAX_LOG_BYTES = 1_000_000
HOLDERS_LIMIT = 20
SUSPICIOUS_TAGS = ("bundler", "rat_trader", "sniper", "dev")
# Levée annoncée, heure de Zurich (CET, UTC+1, en novembre).
INCIDENT_COOLDOWN_UNTIL = "2026-11-08T17:27:31+01:00"
SOURCE_RELATIVE = os.path.join("data", "state.json")

_MINT = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_READ_COMMANDS = (
    ("token", "security"),
    ("token", "holders"),
    ("token", "traders"),
)
_FORBIDDEN = ("swap", "order", "cooking", "config", "sign")


def incident_cooldown_epoch() -> float:
    return datetime.fromisoformat(INCIDENT_COOLDOWN_UNTIL).timestamp()


def valid_mint(value: Any) -> Optional[str]:
    """Adresse Solana stricte. Tout le reste est rejeté avant tout argv."""
    if not isinstance(value, str):
        return None
    if not _MINT.fullmatch(value):
        return None
    return value


def build_argv(executable: str, command: str, mint: str) -> list[str]:
    """Argv sans shell. `command` est l'un des trois noms internes."""
    mint = valid_mint(mint)
    if mint is None:
        raise ValueError("mint refusé")
    if command == "security":
        args = ["token", "security", "--chain", "sol", "--address", mint, "--raw"]
    elif command == "holders":
        args = [
            "token", "holders", "--chain", "sol", "--address", mint,
            "--limit", str(HOLDERS_LIMIT), "--raw",
        ]
    elif command == "traders":
        args = [
            "token", "traders", "--chain", "sol", "--address", mint,
            "--limit", str(HOLDERS_LIMIT), "--raw",
        ]
    else:
        raise ValueError("commande refusée")
    if (args[0], args[1]) not in _READ_COMMANDS:
        raise ValueError("commande refusée")
    if any(piece in _FORBIDDEN for piece in args):
        raise ValueError("commande refusée")
    base = os.path.basename(executable)
    if base != "gmgn-cli":
        raise ValueError("exécutable refusé")
    return [executable, *args]


def child_env(base: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Environnement minimal. Les clés GMGN ne sont pas recopiées.

    Le CLI lit `~/.config/gmgn/.env` tout seul. Ce processus ne charge pas
    le `.env` du projet et ne transmet pas les variables secrètes.
    """
    source = base if base is not None else os.environ
    kept = {}
    for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP"):
        value = source.get(key)
        if value:
            kept[key] = value
    return kept


def _num(value: Any) -> tuple[Optional[float], bool]:
    """(nombre, connu). Absent ou illisible = inconnu, pas zéro."""
    if value is None or value == "":
        return None, False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, False
    return number, True


def _text(value: Any) -> tuple[Optional[str], bool]:
    if value is None:
        return None, False
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return None, False
        return stripped[:64], True
    if isinstance(value, bool):
        return ("true" if value else "false"), True
    return None, False


def parse_security(payload: Any) -> dict[str, Any]:
    row = payload if isinstance(payload, dict) else {}
    if isinstance(row.get("data"), dict):
        row = row["data"]
    fields = (
        "rug_ratio", "top_10_holder_rate", "dev_team_hold_rate",
        "creator_balance_rate", "suspected_insider_hold_rate",
        "buy_tax", "sell_tax",
    )
    out: dict[str, Any] = {}
    known = {}
    for name in fields:
        number, ok = _num(row.get(name))
        out[name] = number
        known[name] = ok
    for name in (
        "renounced_mint", "renounced_freeze_account", "is_honeypot",
        "owner_renounced", "creator_token_status",
    ):
        text, ok = _text(row.get(name))
        out[name] = text
        known[name] = ok
    out["fields_known"] = known
    return out


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        inner = payload.get("data", payload)
        if isinstance(inner, dict):
            for key in ("list", "holders", "traders", "items"):
                value = inner.get(key)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]
        if isinstance(inner, list):
            return [row for row in inner if isinstance(row, dict)]
    return []


def _tag_counts(rows: list[dict[str, Any]]) -> dict[str, Optional[int]]:
    counts = {tag: 0 for tag in SUSPICIOUS_TAGS}
    saw_tag_field = False
    for row in rows:
        tags = row.get("tags") or row.get("maker_token_tags") or row.get("wallet_tag_v2")
        if tags is None:
            continue
        saw_tag_field = True
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, list):
            continue
        folded = {str(tag) for tag in tags}
        for tag in SUSPICIOUS_TAGS:
            if tag in folded:
                counts[tag] += 1
    if not saw_tag_field:
        return {tag: None for tag in SUSPICIOUS_TAGS}
    return counts


def parse_holders(payload: Any) -> dict[str, Any]:
    rows = _rows(payload)[:HOLDERS_LIMIT]
    shares = []
    known_share = False
    for row in rows[:10]:
        number, ok = _num(row.get("amount_percentage"))
        if ok and number is not None:
            known_share = True
            shares.append(number)
    return {
        "rows_seen": len(rows),
        "top10_share_sum": round(sum(shares), 6) if known_share else None,
        "top10_share_known": known_share,
        "suspicious_tag_counts": _tag_counts(rows),
    }


def parse_traders(payload: Any) -> dict[str, Any]:
    rows = _rows(payload)[:HOLDERS_LIMIT]
    return {
        "rows_seen": len(rows),
        "suspicious_tag_counts": _tag_counts(rows),
    }


def classify_cli_failure(stdout: str, stderr: str, returncode: int) -> Optional[str]:
    """`rate_limit`, `auth`, ou None si le texte n'est pas un arrêt dur."""
    blob = f"{stdout}\n{stderr}"
    if "RATE_LIMIT_BANNED" in blob or "RATE_LIMIT" in blob or '"code":429' in blob or '"code": 429' in blob:
        return "rate_limit"
    if "AUTH_KEY_INVALID" in blob or "401" in blob or "403" in blob:
        return "auth"
    if returncode != 0:
        return "cli_error"
    return None


def ban_deadline(stdout: str, stderr: str, now: float) -> float:
    blob = f"{stdout}\n{stderr}"
    match = re.search(r'"reset_at"\s*:\s*(\d{10})', blob)
    if match:
        return float(match.group(1))
    return now + UNKNOWN_BAN_PAUSE_SECONDS


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class GmgnObserver:
    """File courte, cache par mint, budget et cooldown persistants."""

    affects_trading = False
    live_capable = False

    def __init__(
        self,
        root: str,
        clock: Optional[Callable[[], float]] = None,
        runner: Optional[Callable[[list[str]], dict[str, Any]]] = None,
        executable: str = "gmgn-cli",
    ) -> None:
        self.root = root
        self.clock = clock or time.time
        self.runner = runner
        self.executable = executable
        self.dir = os.path.join(root, "data", "gmgn_observe")
        self.state_path = os.path.join(self.dir, "state.json")
        self.heartbeat_path = os.path.join(self.dir, "heartbeat.json")
        self.journal_path = os.path.join(self.dir, "observations.jsonl")
        self.ignored: set[str] = set()
        self.cache: dict[str, dict[str, Any]] = {}
        self.call_times: list[float] = []
        self.cooldown_until = incident_cooldown_epoch()
        self.status = "cooldown"
        self.last_error: Optional[str] = None
        self.calls = 0
        self.responses_ok = 0
        self.responses_error = 0
        self.last_latency_ms: Optional[float] = None
        self.bootstrapped = False
        self._load()

    def _load(self) -> None:
        os.makedirs(self.dir, exist_ok=True)
        if not os.path.exists(self.state_path):
            self.cooldown_until = max(self.cooldown_until, incident_cooldown_epoch())
            self.status = "cooldown" if self.clock() < self.cooldown_until else "observation"
            self._save()
            return
        try:
            with open(self.state_path, encoding="utf-8") as handle:
                saved = json.load(handle)
        except (OSError, json.JSONDecodeError):
            self.last_error = "etat_illisible"
            self.status = "erreur"
            return
        self.ignored = set(saved.get("ignored_at_start") or [])
        self.cache = dict(saved.get("cache") or {})
        self.call_times = [float(item) for item in saved.get("call_times") or []]
        self.cooldown_until = float(saved.get("cooldown_until") or incident_cooldown_epoch())
        self.cooldown_until = max(self.cooldown_until, 0.0)
        self.calls = int(saved.get("calls") or 0)
        self.responses_ok = int(saved.get("responses_ok") or 0)
        self.responses_error = int(saved.get("responses_error") or 0)
        self.bootstrapped = bool(saved.get("bootstrapped"))
        self.status = "cooldown" if self.clock() < self.cooldown_until else "observation"

    def _save(self) -> None:
        os.makedirs(self.dir, exist_ok=True)
        payload = {
            "schema": MEASURE_VERSION,
            "ignored_at_start": sorted(self.ignored),
            "cache": self.cache,
            "call_times": self.call_times[-48:],
            "cooldown_until": self.cooldown_until,
            "calls": self.calls,
            "responses_ok": self.responses_ok,
            "responses_error": self.responses_error,
            "bootstrapped": self.bootstrapped,
            "affects_trading": False,
        }
        tmp = os.path.join(self.dir, f".state-{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(tmp, self.state_path)
        self._trim_cache()
        self._rotate_journal()

    def _trim_cache(self) -> None:
        if len(self.cache) <= MAX_OBSERVATIONS:
            return
        ordered = sorted(self.cache.items(), key=lambda item: item[1].get("observed_at") or "")
        self.cache = dict(ordered[-MAX_OBSERVATIONS:])

    def _rotate_journal(self) -> None:
        try:
            if os.path.getsize(self.journal_path) <= MAX_LOG_BYTES:
                return
        except OSError:
            return
        os.replace(self.journal_path, self.journal_path + ".1")

    def _append(self, row: dict[str, Any]) -> None:
        os.makedirs(self.dir, exist_ok=True)
        with open(self.journal_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def budget_open(self) -> int:
        now = self.clock()
        self.call_times = [stamp for stamp in self.call_times if now - stamp < CALL_WINDOW_SECONDS]
        return max(0, MAX_CALLS_PER_HOUR - len(self.call_times))

    def in_cooldown(self) -> bool:
        return self.clock() < self.cooldown_until

    def selected_mints(self, document: Any) -> list[dict[str, str]]:
        """Mints du dernier état publié. Pas un scan trending.

        `candidates` sans motif de rejet = encore dans la sélection du cycle.
        `positions` = déjà ouvertes. Les deux sont dédupliqués par mint.
        """
        if not isinstance(document, dict):
            return []
        found: dict[str, dict[str, str]] = {}
        for row in document.get("candidates") or []:
            if not isinstance(row, dict) or row.get("rejected_reason"):
                continue
            mint = valid_mint(row.get("token_address"))
            if mint is None:
                continue
            found.setdefault(mint, {"mint": mint, "seen_as": "candidate", "arm": ""})
        for row in document.get("positions") or []:
            if not isinstance(row, dict):
                continue
            mint = valid_mint(row.get("token_address"))
            if mint is None:
                continue
            current = found.get(mint)
            if current is None:
                found[mint] = {
                    "mint": mint,
                    "seen_as": "position",
                    "arm": str(row.get("arm") or ""),
                }
            elif row.get("arm"):
                current["arm"] = str(row.get("arm"))
        return list(found.values())

    def poll(self, document: Any, source_age_sec: Optional[float] = None) -> dict[str, Any]:
        try:
            return self._poll(document, source_age_sec)
        except Exception as exc:
            self.last_error = type(exc).__name__
            self.status = "erreur"
            self._write_heartbeat(source_age_sec)
            self._save()
            return self.heartbeat(source_age_sec)

    def _poll(self, document: Any, source_age_sec: Optional[float]) -> dict[str, Any]:
        if isinstance(document, dict) and str(document.get("mode") or "PAPER").upper() == "LIVE":
            self.status = "erreur"
            self.last_error = "mode_live_refuse"
            self._write_heartbeat(source_age_sec)
            self._save()
            return self.heartbeat(source_age_sec)

        selected = self.selected_mints(document)
        if not self.bootstrapped:
            for row in selected:
                self.ignored.add(row["mint"])
            self.bootstrapped = True
            self._save()

        if self.in_cooldown():
            self.status = "cooldown"
            self._write_heartbeat(source_age_sec)
            self._save()
            return self.heartbeat(source_age_sec)

        fresh = [
            row for row in selected
            if row["mint"] not in self.ignored and not self._cached(row["mint"])
        ][:QUEUE_MAX]
        self.status = "observation"
        for row in fresh:
            if self.budget_open() < 1 or self.in_cooldown():
                break
            self._observe_one(row, source_age_sec)
            if self.in_cooldown():
                break
        self._write_heartbeat(source_age_sec)
        self._save()
        return self.heartbeat(source_age_sec)

    def _cached(self, mint: str) -> bool:
        row = self.cache.get(mint)
        if not row:
            return False
        observed = row.get("observed_epoch")
        if observed is None:
            return False
        return self.clock() - float(observed) < CACHE_TTL_SECONDS

    def _observe_one(self, row: dict[str, str], source_age_sec: Optional[float]) -> None:
        mint = row["mint"]
        observation: dict[str, Any] = {
            "schema": MEASURE_VERSION,
            "mint": mint,
            "seen_as": row["seen_as"],
            "arm": row.get("arm") or "",
            "pretrade_guaranteed": False,
            "timing_note": (
                "candidat encore listé dans state.json au moment du sondage ; "
                "le fichier est écrasé à chaque cycle, donc ce n'est pas une preuve d'analyse avant achat"
                if row["seen_as"] == "candidate"
                else "vu seulement comme position ouverte : l'entrée a déjà eu lieu"
            ),
            "observed_at": _iso(self.clock()),
            "observed_epoch": self.clock(),
            "source_age_sec": source_age_sec,
            "security": None,
            "holders": None,
            "traders": None,
            "status": "unknown",
            "errors": [],
            "affects_trading": False,
            "decision": None,
            "quality": "observation_not_a_guarantee",
        }
        for command in ("security", "holders", "traders"):
            if self.budget_open() < 1 or self.in_cooldown():
                observation["errors"].append(f"{command}:non_tente")
                break
            outcome = self._call(command, mint)
            if outcome["stop"]:
                observation["errors"].append(outcome["error"] or "arret")
                observation["status"] = "unknown"
                self.cache[mint] = observation
                self._append({"kind": "observation", **_public(observation)})
                return
            if outcome["error"]:
                observation["errors"].append(f"{command}:{outcome['error']}")
                continue
            if command == "security":
                observation["security"] = parse_security(outcome["payload"])
            elif command == "holders":
                observation["holders"] = parse_holders(outcome["payload"])
            else:
                observation["traders"] = parse_traders(outcome["payload"])
        filled = [observation["security"], observation["holders"], observation["traders"]]
        if all(item is not None for item in filled):
            observation["status"] = "complete"
        elif any(item is not None for item in filled):
            observation["status"] = "partial"
        else:
            observation["status"] = "unknown"
        self.cache[mint] = observation
        self._append({"kind": "observation", **_public(observation)})

    def _call(self, command: str, mint: str) -> dict[str, Any]:
        if self.runner is None:
            return {"stop": True, "error": "sans_transport", "payload": None}
        try:
            argv = build_argv(self.executable, command, mint)
        except ValueError as exc:
            return {"stop": True, "error": str(exc), "payload": None}
        started = self.clock()
        self.call_times.append(started)
        self.calls += 1
        try:
            result = self.runner(argv)
        except TimeoutError:
            self.responses_error += 1
            self.last_latency_ms = round((self.clock() - started) * 1000, 1)
            self.last_error = "timeout"
            return {"stop": True, "error": "timeout", "payload": None}
        except Exception:
            self.responses_error += 1
            self.last_error = "transport"
            return {"stop": True, "error": "transport", "payload": None}
        self.last_latency_ms = round((self.clock() - started) * 1000, 1)
        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        if len(stdout) > MAX_STDOUT_BYTES:
            self.responses_error += 1
            self.last_error = "reponse_trop_grande"
            return {"stop": True, "error": "reponse_trop_grande", "payload": None}
        code = int(result.get("returncode") or 0)
        kind = classify_cli_failure(stdout, stderr, code)
        if kind == "rate_limit":
            self.responses_error += 1
            self.cooldown_until = max(self.cooldown_until, ban_deadline(stdout, stderr, self.clock()))
            self.status = "cooldown"
            self.last_error = "rate_limit"
            return {"stop": True, "error": "rate_limit", "payload": None}
        if kind == "auth":
            self.responses_error += 1
            self.cooldown_until = max(self.cooldown_until, self.clock() + AUTH_PAUSE_SECONDS)
            self.status = "erreur"
            self.last_error = "auth"
            return {"stop": True, "error": "auth", "payload": None}
        if kind == "cli_error":
            self.responses_error += 1
            self.last_error = "cli_error"
            return {"stop": True, "error": "cli_error", "payload": None}
        try:
            payload = json.loads(stdout) if stdout.strip() else None
        except json.JSONDecodeError:
            self.responses_error += 1
            self.last_error = "json_invalide"
            return {"stop": True, "error": "json_invalide", "payload": None}
        self.responses_ok += 1
        return {"stop": False, "error": None, "payload": payload}

    def heartbeat(self, source_age_sec: Optional[float] = None) -> dict[str, Any]:
        return {
            "schema": "gmgn-observe-heartbeat-1",
            "ts": _iso(self.clock()),
            "status": self.status,
            "mode": "PAPER",
            "affects_trading": False,
            "live_capable": False,
            "cooldown_until": _iso(self.cooldown_until),
            "incident_cooldown_until": INCIDENT_COOLDOWN_UNTIL,
            "calls": self.calls,
            "responses_ok": self.responses_ok,
            "responses_error": self.responses_error,
            "budget_per_hour": MAX_CALLS_PER_HOUR,
            "budget_open": self.budget_open(),
            "last_latency_ms": self.last_latency_ms,
            "last_error": self.last_error,
            "source": "data/state.json candidates sans rejet, et positions ouvertes",
            "source_age_sec": source_age_sec,
            "pretrade_guaranteed": False,
            "cached_mints": len(self.cache),
            "ignored_at_start": len(self.ignored),
        }

    def _write_heartbeat(self, source_age_sec: Optional[float]) -> None:
        os.makedirs(self.dir, exist_ok=True)
        tmp = os.path.join(self.dir, f".heart-{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.heartbeat(source_age_sec), handle, ensure_ascii=False, indent=1)
        os.replace(tmp, self.heartbeat_path)


def _public(observation: dict[str, Any]) -> dict[str, Any]:
    skip = {"observed_epoch"}
    return {key: value for key, value in observation.items() if key not in skip}


def read_state(path: str) -> tuple[Any, Optional[float]]:
    """Lecture seule. Retourne (document ou None, âge en secondes)."""
    try:
        age = max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return None, None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle), age
    except (OSError, json.JSONDecodeError):
        return None, age
