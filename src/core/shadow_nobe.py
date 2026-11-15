"""Suivi prospectif apparié, sans le breakeven indépendant.

Mesure seulement. Aucun ordre, aucun capital de bras, aucun accès LIVE.

Le journal réel étiquette `BREAKEVEN_STOP` dès que `breakeven_moved` est vrai.
Trois gestes distincts posent ce drapeau :

- le déclencheur `breakeven_trigger` dans `update_water_marks` ;
- le TP1, dans `apply_exit` ;
- l'ajout de taille de `scaled_be`, dans `apply_scale_in` (stop exact au
  prix moyen).

Ce module coupe uniquement le premier. Les deux autres restent, avec leur
niveau : 0 % exact, ou plancher 0 + tampon (+10, +15, …).

Le processus est un sidecar. Il lit `open_positions.json`, `state.json` et
le journal du bras. Il n'écrit que sous `data/shadow_nobe/`. Un échec ici
ne touche ni la boucle ni les bras.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import time
from dataclasses import asdict, fields
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from src.core.exit_fees import (
    MIN_SAMPLE_FOR_PARTIAL_ESTIMATE,
    estimated_partial_cost_pct,
)
from src.core.funnel import rotate
from src.core.journal import correct_legacy_cost
from src.core.portfolio import normalise_ladder
from src.core.positions import (
    ExitAction,
    Position,
    apply_exit,
    apply_scale_in,
    cost_vs_entry,
    evaluate_exits,
    update_water_marks,
)

MEASURE_VERSION = "nobe-shadow-1"
REFERENCE_EQUITY_USD = 7000.0
DEFAULT_ARMS = ("tp1_30", "scaled_be")
HORIZONS_MIN = (5, 15, 30, 60)
# Le monitoring réel est à 5 s. Au-delà, la cote n'est plus « le tick ».
STALE_SECONDS = 45.0
# Un trou plus long peut cacher un stop ou un TP touché puis reperdu.
AMBIGUOUS_GAP_SECONDS = 90.0
HORIZON_TOLERANCE_SECONDS = 90.0
# Après le time stop, sans cote, on censure. On n'invente pas un prix.
CENSOR_GRACE_SECONDS = 30.0 * 60.0
MAX_LOG_BYTES = 8 * 1024 * 1024
MAX_CLOSED = 400
MAX_CONTINUATION_PER_MIN = 4
CONTINUATION_MIN_INTERVAL = 15.0
MARK_BUCKET_SECONDS = 60
# Plafond du coût exprimé en % du montant revendu. Au-delà, le devis est
# absurde (pool vidé) : on ne peut pas perdre plus que la mise. Le même
# plafond vit dans positions.cost_vs_entry depuis le correctif du 2026-11-05.
# Il est répété ici pour que l'ombre ne dépende pas de ce correctif.
SOLD_COST_CAP_PCT = 100.0

EXTERNAL_PREFIXES = (
    "RUG_PULL",
    "DEV_DUMP",
    "COPY_WALLET_SOLD",
    "MANUAL_STOP",
    "MANUAL",
    "PANIC",
)

ASSUMPTIONS = (
    "Seuls breakeven_trigger est coupé. Le TP1 et l'ajout scaled_be arment toujours leur stop.",
    "Tant que la position source est ouverte, le prix vient de state.json, la publication du monitoring.",
    "Après la sortie source, la continuation interroge DexScreener public sur la paire d'entrée, au plus 4 fois par minute. Pas de Helius, pas de Jupiter, pas de Birdeye.",
    "Le coût final du shadow est la médiane des coûts mesurés du bras, figée à l'entrée, pas un devis. Sous 10 mesures : prix nu.",
    "Les ventes partielles prennent la moitié de cette médiane, figée aussi, et une mesure arrivée plus tard ne réécrit rien.",
    "Un trou de plus de 90 s interdit de compter la sortie comme un effet exact. Si la règle sortirait, le chemin est censuré, sans prix inventé.",
    "Quand TP1 et le déclencheur basculent sur la même observation sans sortie, le stop est attribué au TP1 : apply_exit l'aurait armé de toute façon.",
    "Le livre de 7000 USD sert au drawdown de mesure. Ce n'est pas la trésorerie du bras et pas un portefeuille alternatif.",
    "Une position déjà en breakeven, déjà ajoutée ou déjà partielle au premier regard n'est pas rejouée.",
    "Les règles viennent de la position figée à l'entrée. Un fichier réécrit par l'apprentissage est signalé, il ne remplace pas ces règles.",
)


def _now_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def parse_ts(value: Any) -> Optional[float]:
    """Epoch, ou ISO 8601. None si la valeur ne dit pas un instant."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            return None
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(payload: Any) -> str:
    return json.dumps(_jsonable(payload), sort_keys=True, ensure_ascii=False, default=str)


def classify_stop_kind(breakeven_moved: bool, breakeven_stop_exact: bool) -> str:
    """`initial`, `exact_zero` ou `profit_floor`.

    Le plancher vaut 0 + tampon, même si le tampon est nul : le mécanisme
    n'est pas le drapeau `breakeven_stop_exact`. Le niveau chiffré est à
    côté, pour distinguer +10 de +15 de 0.
    """
    if not breakeven_moved:
        return "initial"
    if breakeven_stop_exact:
        return "exact_zero"
    return "profit_floor"


def stop_view(position: Position) -> dict[str, Any]:
    return {
        "kind": classify_stop_kind(position.breakeven_moved, position.breakeven_stop_exact),
        "level_pct": position.effective_stop_loss_pct,
        "breakeven_moved": position.breakeven_moved,
        "breakeven_stop_exact": position.breakeven_stop_exact,
        "buffer_pct": position.stop_loss_slippage_buffer_pct,
        "initial_stop_pct": position.stop_loss_pct,
        "trigger_pct": position.breakeven_trigger,
    }


def flags_of(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "breakeven_moved": bool(row.get("breakeven_moved")),
        "tp1_hit": bool(row.get("tp1_hit")),
        "tp2_hit": bool(row.get("tp2_hit")),
        "scaled_in": bool(row.get("scaled_in")),
        "ladder_filled": int(row.get("ladder_filled") or 0),
        "remaining_fraction": row.get("remaining_fraction", 1.0),
    }


def classify_be_transition(
    before: dict[str, Any], after: dict[str, Any]
) -> tuple[Optional[str], Optional[str]]:
    """Origine du passage `breakeven_moved` de faux à vrai.

    Retourne (origine, note). None si le drapeau ne vient pas de bouger.
    """
    if not after.get("breakeven_moved") or before.get("breakeven_moved"):
        return None, None
    scale = bool(after.get("scaled_in")) and not before.get("scaled_in")
    tp1 = bool(after.get("tp1_hit")) and not before.get("tp1_hit")
    if scale and tp1:
        return "ambiguous_multi", "scale_et_tp1_meme_observation"
    if scale:
        return "scale_in", "ajout_observe"
    if tp1:
        return "tp1", "tp1_suffit_a_armer_le_stop"
    return "independent_trigger", "seuil_breakeven_trigger"


def fee_policy_from_costs(costs: list[float]) -> dict[str, Any]:
    """Médiane figée. Les estimations déjà journalisées ne sont pas ici.

    L'appelant ne passe que des `exit_cost_pct` mesurés. Sous dix valeurs
    strictement positives, prix nu : pas un coût inventé.
    """
    propres = []
    for value in costs:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            propres.append(number)
    partial = estimated_partial_cost_pct(propres)
    if len(propres) >= MIN_SAMPLE_FOR_PARTIAL_ESTIMATE:
        final = round(statistics.median(propres), 3)
        name = "median_frozen_at_entry"
    else:
        final = None
        name = "naked_insufficient_sample"
    return {
        "name": name,
        "sample_n": len(propres),
        "partial_cost_pct": partial,
        "final_cost_assumption_pct": final,
        "min_sample": MIN_SAMPLE_FOR_PARTIAL_ESTIMATE,
        "note": (
            "Figé à l'entrée du shadow. Le devis de la sortie réelle, "
            "connu plus tard, est conservé à part et ne réécrit pas ce coût."
        ),
    }


def position_from_mapping(row: dict[str, Any]) -> Position:
    known = {item.name for item in fields(Position)}
    clean = {key: value for key, value in row.items() if key in known}
    if isinstance(clean.get("exit_reasons"), list):
        clean["exit_reasons"] = tuple(clean["exit_reasons"])
    clean["ladder"] = normalise_ladder(clean.get("ladder"))
    if not isinstance(clean.get("entry_context"), dict):
        clean["entry_context"] = {}
    return Position(**clean)


def dump_position(position: Position) -> dict[str, Any]:
    return _jsonable(asdict(position))


_RULE_FIELDS = (
    "stop_loss_pct",
    "take_profit_1",
    "take_profit_2",
    "take_profit_3",
    "partial_sell_tp1_pct",
    "partial_sell_tp2_pct",
    "trailing_stop_activation",
    "trailing_stop_distance_pct",
    "max_hold_time_minutes",
    "stop_loss_slippage_buffer_pct",
    "breakeven_trigger",
    "breakeven_stop_exact",
    "ladder",
    "scale_in_trigger_pct",
    "scale_in_max_pct",
)


def rules_from_position(position: Position) -> dict[str, Any]:
    return {name: _jsonable(getattr(position, name)) for name in _RULE_FIELDS}


def _near(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= 1e-6
    return _jsonable(left) == _jsonable(right)


def file_divergence(rules: dict[str, Any], config: dict[str, Any]) -> list[str]:
    """Clés présentes dans le fichier et différentes de la position figée.

    Une clé absente du fichier n'est pas une divergence : l'apprentissage
    n'écrit pas toujours les optionnels. Le fichier ne devient jamais la
    règle du shadow.
    """
    exit_rules = (config or {}).get("exit_rules") or {}
    entry_rules = (config or {}).get("entry_rules") or {}
    found = []
    for key in _RULE_FIELDS:
        if key in ("scale_in_trigger_pct", "scale_in_max_pct"):
            source = entry_rules
        else:
            source = exit_rules
        if key not in source or source.get(key) in ("", None):
            continue
        if not _near(rules.get(key), source.get(key)):
            found.append(key)
    return found


def _clean_entry(row: dict[str, Any]) -> tuple[bool, str]:
    if str(row.get("mode") or "PAPER").upper() != "PAPER":
        return False, "refused_not_paper"
    try:
        entry = float(row.get("entry_price") or 0)
        size = float(row.get("size_usd") or 0)
    except (TypeError, ValueError):
        return False, "ambiguous_incomplete"
    if entry <= 0 or size <= 0:
        return False, "ambiguous_incomplete"
    if row.get("breakeven_moved") or row.get("tp1_hit") or row.get("tp2_hit"):
        return False, "skipped_late_or_dirty"
    if row.get("scaled_in") or int(row.get("ladder_filled") or 0) > 0:
        return False, "skipped_late_or_dirty"
    if row.get("exit_reasons"):
        return False, "skipped_late_or_dirty"
    try:
        remaining = float(row.get("remaining_fraction", 1.0))
    except (TypeError, ValueError):
        return False, "ambiguous_incomplete"
    if remaining < 1.0 - 1e-6:
        return False, "skipped_late_or_dirty"
    trigger = float(row.get("breakeven_trigger") or 0)
    high = float(row.get("high_water_pct") or 0)
    if trigger > 0 and high + 1e-9 >= trigger and not row.get("breakeven_moved"):
        return False, "skipped_inconsistent_be"
    return True, ""


def _is_external(reason: str) -> bool:
    text = reason or ""
    return text.startswith(EXTERNAL_PREFIXES)


def _gross_usd(entry_stake: float, entry: float, exit_price: float) -> Optional[float]:
    if entry_stake is None or not entry or not exit_price or entry <= 0 or exit_price <= 0:
        return None
    return float(entry_stake) * (100.0 * (float(exit_price) / float(entry) - 1.0)) / 100.0


def select_pair_quote(
    pairs: list[dict[str, Any]],
    pair_address: Optional[str],
    chain: Optional[str],
    now: float,
) -> dict[str, Any]:
    """Choisit la paire d'entrée. Une autre paire n'est pas un prix de règle."""
    chosen = [row for row in pairs or [] if not chain or row.get("chainId") in (None, chain)]
    if pair_address:
        hit = [row for row in chosen if row.get("pairAddress") == pair_address]
        if not hit:
            return {
                "matched": False,
                "reason": "pair_mismatch_or_missing",
                "price": None,
                "ts": now,
            }
        return _quote_from_pair(hit[0], True, "anchored", now)
    if len(chosen) == 1:
        return _quote_from_pair(chosen[0], True, "pair_unanchored_single", now)
    return {"matched": False, "reason": "multi_pair_no_anchor", "price": None, "ts": now}


def _quote_from_pair(pair: dict[str, Any], matched: bool, reason: str, now: float) -> dict[str, Any]:
    try:
        price = float(pair.get("priceUsd"))
    except (TypeError, ValueError):
        price = 0.0
    liquidity = None
    try:
        liquidity = float((pair.get("liquidity") or {}).get("usd"))
    except (TypeError, ValueError):
        liquidity = None
    if not math.isfinite(price) or price <= 0:
        return {"matched": False, "reason": "no_price", "price": None, "ts": now, "liquidity": liquidity}
    return {
        "matched": matched,
        "reason": reason,
        "price": price,
        "ts": now,
        "pair": pair.get("pairAddress"),
        "liquidity": liquidity,
    }


class RateLimitedQuotes:
    """Plafond du flux de continuation. Un échec du fetch rend un dict vide."""

    def __init__(
        self,
        fetch: Callable[[list[str]], dict[str, Any]],
        min_interval: float = CONTINUATION_MIN_INTERVAL,
        max_per_min: int = MAX_CONTINUATION_PER_MIN,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.fetch = fetch
        self.min_interval = min_interval
        self.max_per_min = max_per_min
        self.clock = clock or time.time
        self.calls: list[float] = []
        self.last = 0.0
        self.served = 0
        self.skipped = 0

    def get(self, tokens: list[str]) -> dict[str, Any]:
        if not tokens:
            return {}
        now = self.clock()
        self.calls = [stamp for stamp in self.calls if now - stamp < 60.0]
        if now - self.last < self.min_interval or len(self.calls) >= self.max_per_min:
            self.skipped += 1
            return {}
        self.last = now
        self.calls.append(now)
        self.served += 1
        try:
            return self.fetch(list(tokens)) or {}
        except Exception:
            return {}


def _read_tail(path: str, offset: int) -> tuple[list[dict[str, Any]], int]:
    if not path or not os.path.exists(path):
        return [], 0
    size = os.path.getsize(path)
    if offset < 0 or offset > size:
        offset = 0
    with open(path, "rb") as handle:
        handle.seek(offset)
        data = handle.read()
    if not data:
        return [], offset
    if not data.endswith(b"\n"):
        cut = data.rfind(b"\n")
        if cut < 0:
            return [], offset
        data = data[: cut + 1]
    rows = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(correct_legacy_cost(json.loads(line)))
        except json.JSONDecodeError:
            continue
    return rows, offset + len(data)


def _scan_measured_costs(path: str) -> list[float]:
    if not path or not os.path.exists(path):
        return []
    costs = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("exit_cost_estimated"):
                continue
            value = row.get("exit_cost_pct")
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                costs.append(float(value))
    return costs


def _file_sha(path: str) -> tuple[str, dict[str, Any]]:
    if not path or not os.path.exists(path):
        return "", {"sha256": "", "readable": False, "exit_rules": {}, "entry_rules": {}, "mode": None}
    raw = b""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return "", {"sha256": "", "readable": False, "exit_rules": {}, "entry_rules": {}, "mode": None}
    sha = _sha256_bytes(raw)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return sha, {
            "sha256": sha,
            "readable": False,
            "exit_rules": {},
            "entry_rules": {},
            "mode": None,
        }
    if not isinstance(doc, dict):
        doc = {}
    return sha, {
        "sha256": sha,
        "readable": True,
        "exit_rules": doc.get("exit_rules") if isinstance(doc.get("exit_rules"), dict) else {},
        "entry_rules": doc.get("entry_rules") if isinstance(doc.get("entry_rules"), dict) else {},
        "mode": doc.get("mode"),
        "version": doc.get("version"),
    }


def collect_bundle(
    root: str,
    arms: tuple[str, ...] = DEFAULT_ARMS,
    offsets: Optional[dict[str, dict[str, int]]] = None,
    fee_cache: Optional[dict[str, Any]] = None,
) -> tuple[dict[str, Any], dict[str, dict[str, int]], dict[str, Any]]:
    """Lecture seule des observations. N'écrit rien.

    `offsets` reprend après le dernier octet consommé. Un fichier raccourci
    est relu depuis le début ; les identifiants de lignes dédoublonnent.
    """
    offsets = {
        "journal": dict((offsets or {}).get("journal") or {}),
        "scale": dict((offsets or {}).get("scale") or {}),
    }
    fee_cache = dict(fee_cache or {})
    state_path = os.path.join(root, "data", "state.json")
    state_updated = None
    state_positions: list[dict[str, Any]] = []
    if os.path.exists(state_path):
        try:
            with open(state_path, encoding="utf-8") as handle:
                state = json.load(handle)
            state_updated = state.get("updated_at")
            for row in state.get("positions") or []:
                if isinstance(row, dict):
                    state_positions.append(row)
        except (OSError, json.JSONDecodeError):
            state_updated = None
            state_positions = []

    arm_payload: dict[str, Any] = {}
    for arm in arms:
        if arm == "baseline":
            # Le témoin n'est pas sous data/arms : mêmes chemins que src/settings.arm_paths.
            journal_path = os.path.join(root, "data", "trades_log.jsonl")
            positions_path = os.path.join(root, "data", "open_positions.json")
            config_path = os.path.join(root, "config", "params.json")
        else:
            base = os.path.join(root, "data", "arms", arm)
            journal_path = os.path.join(base, "trades_log.jsonl")
            positions_path = os.path.join(base, "open_positions.json")
            config_path = os.path.join(root, "config", "arms", f"{arm}.json")
        opens: list[dict[str, Any]] = []
        if os.path.exists(positions_path):
            try:
                with open(positions_path, encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, list):
                    opens = [row for row in loaded if isinstance(row, dict)]
            except (OSError, json.JSONDecodeError):
                opens = []
        start = int(offsets["journal"].get(arm) or 0)
        rows, new_offset = _read_tail(journal_path, start)
        offsets["journal"][arm] = new_offset
        signature = None
        try:
            stat = os.stat(journal_path)
            signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = None
        cached = fee_cache.get(arm)
        if not cached or cached.get("signature") != signature:
            fee_cache[arm] = {"signature": signature, "costs": _scan_measured_costs(journal_path)}
        _sha, config = _file_sha(config_path)
        arm_payload[arm] = {
            "open_positions": opens,
            "journal_rows": rows,
            "fee_costs": list(fee_cache[arm]["costs"]),
            "config": config,
        }
    bundle = {
        "state_updated_at": state_updated,
        "state_positions": state_positions,
        "arms": arm_payload,
        "continuation_quotes": {},
    }
    return bundle, offsets, fee_cache


def tokens_needing_quotes(tracker: "ShadowTracker", bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Tokens dont le shadow vit encore sans prix frais dans state.json.

    On ne double pas le flux tant que la position source est ouverte et que
    state.json porte déjà son prix.
    """
    fresh = set()
    updated = parse_ts(bundle.get("state_updated_at"))
    now = tracker.clock()
    if updated is not None and now - updated <= STALE_SECONDS:
        for row in bundle.get("state_positions") or []:
            price = row.get("current_price")
            try:
                price_ok = float(price) > 0
            except (TypeError, ValueError):
                price_ok = False
            if price_ok:
                fresh.add((row.get("arm"), row.get("id")))
    open_ids = {
        (arm, row.get("id"))
        for arm, payload in (bundle.get("arms") or {}).items()
        for row in payload.get("open_positions") or []
    }
    needed = []
    seen = set()
    for pair in list(tracker.pairs.values()) + list(tracker.closed):
        if pair.get("token_address") in seen:
            continue
        wants_price = pair.get("shadow_open")
        wants_horizon = _horizon_pending(pair, now)
        if not wants_price and not wants_horizon:
            continue
        key = (pair.get("arm"), pair.get("source_position_id"))
        if key in open_ids and key in fresh and not wants_horizon:
            continue
        if key in open_ids and key in fresh and pair.get("shadow_open"):
            # Le prix d'état suffit au shadow ouvert. L'horizon n'est dû
            # qu'après la sortie BE, donc la source n'est plus ouverte.
            continue
        seen.add(pair["token_address"])
        needed.append(
            {
                "token_address": pair["token_address"],
                "pair_address": (pair.get("position") or {}).get("pair_address"),
                "chain": (pair.get("position") or {}).get("chain") or "solana",
            }
        )
    return needed


def _horizon_pending(pair: dict[str, Any], now: float) -> bool:
    start = pair.get("baseline_be_exit_ts")
    if start is None:
        return False
    if now > start + (max(HORIZONS_MIN) * 60) + HORIZON_TOLERANCE_SECONDS:
        return False
    horizons = pair.get("horizons") or {}
    for minutes in HORIZONS_MIN:
        slot = horizons.get(str(minutes)) or {}
        if slot.get("status") not in ("filled", "missed"):
            return True
    return False


class ShadowTracker:
    """État persistant du suivi. Rejouable : un second passage ne double pas."""

    live_capable = False

    def __init__(
        self,
        root: str,
        clock: Optional[Callable[[], float]] = None,
        max_log_bytes: int = MAX_LOG_BYTES,
        max_closed: int = MAX_CLOSED,
        arms: tuple[str, ...] = DEFAULT_ARMS,
    ) -> None:
        self.root = root
        self.clock = clock or time.time
        self.max_log_bytes = max_log_bytes
        self.max_closed = max_closed
        self.arms = arms
        self.dir = os.path.join(root, "data", "shadow_nobe")
        self.journal_path = os.path.join(self.dir, "events.jsonl")
        self.state_path = os.path.join(self.dir, "state.json")
        self.heartbeat_path = os.path.join(self.dir, "heartbeat.json")
        self.stats_path = os.path.join(self.dir, "paired_stats.json")
        self.pairs: dict[str, dict[str, Any]] = {}
        self.closed: list[dict[str, Any]] = []
        self.skipped: dict[str, str] = {}
        self.offsets: dict[str, dict[str, int]] = {"journal": {}, "scale": {}}
        self.peaks: dict[str, float] = {}
        self.last_error: Optional[str] = None
        self.pair_errors: list[dict[str, str]] = []
        self._seen: set[str] = set()
        self._load()

    # ------------------------------------------------------------- persistance

    def _load(self) -> None:
        os.makedirs(self.dir, exist_ok=True)
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, encoding="utf-8") as handle:
                    state = json.load(handle)
            except (OSError, json.JSONDecodeError):
                state = {}
            self.pairs = state.get("pairs") or {}
            self.closed = list(state.get("closed") or [])
            self.skipped = dict(state.get("skipped") or {})
            self.offsets = state.get("offsets") or {"journal": {}, "scale": {}}
            self.peaks = {key: float(value) for key, value in (state.get("peaks") or {}).items()}
        self._replay_journal_snapshots()

    def _replay_journal_snapshots(self) -> None:
        """Le journal gagne si son seq dépasse le checkpoint.

        Un crash entre l'append et l'écriture de state.json laisse la
        transition dans le journal. On la reprend, sans réécrire la ligne.
        """
        latest: dict[str, dict[str, Any]] = {}
        for path in (self._rotated_journal(), self.journal_path):
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event_id = event.get("event_id")
                    if event_id:
                        self._seen.add(event_id)
                    snap = event.get("pair_snapshot")
                    if isinstance(snap, dict) and snap.get("pair_id"):
                        current = latest.get(snap["pair_id"])
                        if current is None or int(snap.get("seq") or 0) >= int(current.get("seq") or 0):
                            latest[snap["pair_id"]] = snap
        for pair_id, snap in latest.items():
            checkpoint = self.pairs.get(pair_id)
            closed = next((row for row in self.closed if row.get("pair_id") == pair_id), None)
            checkpoint_seq = int((checkpoint or closed or {}).get("seq") or 0)
            if int(snap.get("seq") or 0) <= checkpoint_seq:
                continue
            self._install(snap)

    def _install(self, snap: dict[str, Any]) -> None:
        pair_id = snap["pair_id"]
        if snap.get("shadow_open") and not snap.get("terminal"):
            self.pairs[pair_id] = snap
            self.closed = [row for row in self.closed if row.get("pair_id") != pair_id]
        else:
            self.pairs.pop(pair_id, None)
            self.closed = [row for row in self.closed if row.get("pair_id") != pair_id]
            self.closed.append(self._summary(snap))

    def _rotated_journal(self) -> str:
        base, ext = os.path.splitext(self.journal_path)
        return f"{base}.1{ext}"

    def save(self) -> None:
        os.makedirs(self.dir, exist_ok=True)
        payload = {
            "schema": MEASURE_VERSION,
            "saved_at": _now_iso(self.clock()),
            "pairs": _jsonable(self.pairs),
            "closed": _jsonable(self.closed[-self.max_closed :]),
            "skipped": self.skipped,
            "offsets": self.offsets,
            "peaks": self.peaks,
            "live_capable": False,
        }
        directory = self.dir
        tmp = os.path.join(directory, f".state-{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(tmp, self.state_path)
        self.closed = self.closed[-self.max_closed :]

    def _emit(self, event_id: str, row: dict[str, Any], snapshot: Optional[dict[str, Any]] = None) -> bool:
        if not event_id or event_id in self._seen:
            return False
        body = dict(row)
        body["event_id"] = event_id
        body["measure_version"] = MEASURE_VERSION
        body["ts"] = body.get("ts") or _now_iso(self.clock())
        if snapshot is not None:
            body["pair_snapshot"] = _jsonable(snapshot)
        os.makedirs(self.dir, exist_ok=True)
        with open(self.journal_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(_jsonable(body), ensure_ascii=False) + "\n")
        rotate(self.journal_path, max_bytes=self.max_log_bytes)
        self._seen.add(event_id)
        return True

    def journal_events(self) -> list[dict[str, Any]]:
        rows = []
        for path in (self._rotated_journal(), self.journal_path):
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return rows

    # ----------------------------------------------------------------- cycle

    def apply(self, bundle: dict[str, Any], offsets: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        self.pair_errors = []
        self.last_error = None
        try:
            self._apply_inner(bundle)
            if offsets is not None:
                self.offsets = offsets
        except Exception as exc:  # le shadow meurt seul
            self.last_error = f"{type(exc).__name__}: {exc}"
        try:
            self._write_outputs()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
        return self.heartbeat_body()

    def _apply_inner(self, bundle: dict[str, Any]) -> None:
        now = self.clock()
        state_ts = parse_ts(bundle.get("state_updated_at"))
        state_price = {}
        for row in bundle.get("state_positions") or []:
            state_price[(row.get("arm"), row.get("id"))] = row.get("current_price")
        priced: set[str] = set()
        seen_open: set[tuple[str, str]] = set()

        for arm, payload in (bundle.get("arms") or {}).items():
            config = payload.get("config") or {}
            if str(config.get("mode") or "PAPER").upper() not in ("PAPER", "NONE", ""):
                if str(config.get("mode")).upper() == "LIVE":
                    self._skip_arm_live(arm)
                    continue
            for row in payload.get("open_positions") or []:
                try:
                    self._handle_open(arm, row, payload, state_price, state_ts, now, priced)
                except Exception as exc:
                    self.pair_errors.append(
                        {"arm": arm, "id": str(row.get("id")), "error": f"{type(exc).__name__}: {exc}"}
                    )
                if row.get("id"):
                    seen_open.add((arm, row.get("id")))
            for row in payload.get("journal_rows") or []:
                try:
                    self._ingest_journal(arm, row, now, priced)
                except Exception as exc:
                    self.pair_errors.append(
                        {
                            "arm": arm,
                            "id": str(row.get("position_id")),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

        for pair_id, pair in list(self.pairs.items()):
            key = (pair.get("arm"), pair.get("source_position_id"))
            if key not in seen_open and pair.get("baseline_open"):
                pair["baseline_open"] = False
                if not pair.get("baseline_final_seen"):
                    pair["baseline_note"] = "position source absente sans jambe finale"
                    self._taint(pair, "ambiguous_gap")
            self._apply_continuation(pair, bundle.get("continuation_quotes") or {}, now, priced)
            self._update_horizons(pair, now)
            self._maybe_censor(pair, now)
            self._mark(pair, now)
            self._refresh_effect(pair)
            self._park_if_terminal(pair_id)

        for row in self.closed:
            self._refresh_effect(row)

    def _skip_arm_live(self, arm: str) -> None:
        reason = "refused_not_paper"
        if self.skipped.get(arm) == reason:
            return
        self.skipped[arm] = reason
        self._emit(f"{arm}:refused_live", {"kind": "refused_live", "arm": arm, "note": reason})

    def _known(self, pair_id: str) -> bool:
        if pair_id in self.pairs or pair_id in self.skipped:
            return True
        return any(row.get("pair_id") == pair_id for row in self.closed)

    def _handle_open(
        self,
        arm: str,
        row: dict[str, Any],
        payload: dict[str, Any],
        state_price: dict[tuple[Any, Any], Any],
        state_ts: Optional[float],
        now: float,
        priced: set[str],
    ) -> None:
        source_id = row.get("id")
        if not source_id:
            return
        pair_id = f"{arm}:{source_id}"
        if pair_id in self.skipped or any(item.get("pair_id") == pair_id for item in self.closed):
            return
        pair = self.pairs.get(pair_id)
        if pair is None:
            pair = self._adopt(arm, row, payload)
            if pair is None:
                return
        elif not pair.get("shadow_open"):
            return
        self._note_config(pair, payload.get("config") or {})
        self._sync_flags(pair, row, now)
        self._maybe_scale(pair, row, now)
        price = state_price.get((arm, source_id))
        if pair.get("baseline_open", True):
            self._consider_price(pair, price, state_ts, now, "state")
            priced.add(pair_id)

    def _adopt(self, arm: str, row: dict[str, Any], payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        source_id = row.get("id")
        pair_id = f"{arm}:{source_id}"
        if self._known(pair_id):
            return self.pairs.get(pair_id)
        ok, reason = _clean_entry(row)
        config = payload.get("config") or {}
        if str(config.get("mode") or "PAPER").upper() == "LIVE" or str(row.get("mode") or "PAPER").upper() != "PAPER":
            ok, reason = False, "refused_not_paper"
        if not ok:
            self.skipped[pair_id] = reason
            self._emit(
                f"{pair_id}:skip:{reason}",
                {"kind": "skipped", "pair_id": pair_id, "arm": arm, "reason": reason},
            )
            return None
        source = position_from_mapping(row)
        rules = rules_from_position(source)
        shadow = position_from_mapping(dump_position(source))
        shadow = _replace_shadow(shadow, source_id)
        diverged = file_divergence(rules, config) if config.get("readable") else []
        policy = "comparable_frozen_file_diverged" if diverged else "comparable_frozen"
        note = ""
        if not config.get("readable"):
            note = "fichier de config illisible ou absent ; règles prises sur la position"
        elif diverged:
            note = "fichier différent de la position figée : " + ",".join(diverged)
        fee = fee_policy_from_costs(list(payload.get("fee_costs") or []))
        pair = {
            "schema": MEASURE_VERSION,
            "pair_id": pair_id,
            "arm": arm,
            "source_position_id": source_id,
            "token_address": shadow.token_address,
            "symbol": shadow.symbol,
            "seq": 1,
            "applied_event_ids": [f"{pair_id}:open"],
            "shadow_open": True,
            "baseline_open": True,
            "baseline_final_seen": False,
            "terminal": None,
            "path_quality": "clean",
            "policy_status": policy,
            "policy_note": note,
            "config_changed_after_entry": False,
            "config_sha_at_entry": config.get("sha256") or "",
            "config_sha_now": config.get("sha256") or "",
            "file_divergence": diverged,
            "rules": rules,
            "rules_sha256": _sha256_bytes(_canonical(rules).encode("utf-8")),
            "params_version": shadow.params_version,
            "fee": fee,
            "position": dump_position(shadow),
            "source_flags": flags_of(row),
            "baseline_stop_events": [],
            "shadow_stop_events": [],
            "baseline_stop_origin": None,
            "shadow_stop_origin": None,
            "baseline_legs": [],
            "baseline_row_ids": [],
            "shadow_legs": [],
            "shadow_leg_ids": [],
            "baseline_realized_net_usd": 0.0,
            "baseline_realized_gross_usd": 0.0,
            "shadow_realized_gross_usd": 0.0,
            "baseline_exit_ts": None,
            "baseline_exit_price": None,
            "baseline_exit_reason": None,
            "baseline_be_exit_ts": None,
            "baseline_be_exit_price": None,
            "horizon_basis": None,
            "horizons": {},
            "last_price": None,
            "last_price_ts": None,
            "last_actionable_ts": None,
            "missing_quotes": 0,
            "stale_quotes": 0,
            "late_quotes_ignored": 0,
            "mtm_net_usd": None,
            "mtm_gross_usd": None,
            "mtm_status": "none",
            "entry_time": shadow.entry_time,
            "effect_status": "incomplete",
            "delta_net_usd": None,
            "delta_gross_usd": None,
            "live_delta_net_usd": None,
            "baseline_note": "",
            "pair_note": "",
        }
        self.pairs[pair_id] = pair
        self._bump(pair)
        self._emit(
            f"{pair_id}:open",
            {
                "kind": "open",
                "pair_id": pair_id,
                "arm": arm,
                "source_position_id": source_id,
                "rules_sha256": pair["rules_sha256"],
                "params_version": pair["params_version"],
                "policy_status": policy,
                "policy_note": note,
                "disabled_trigger_pct": rules.get("breakeven_trigger"),
                "fee": fee,
                "mode": "PAPER",
            },
            pair,
        )
        return pair

    def _note_config(self, pair: dict[str, Any], config: dict[str, Any]) -> None:
        sha = config.get("sha256") or ""
        if not sha or sha == pair.get("config_sha_now"):
            return
        pair["config_sha_now"] = sha
        if pair.get("config_sha_at_entry") and sha != pair.get("config_sha_at_entry"):
            pair["config_changed_after_entry"] = True
            self._bump(pair)
            self._emit(
                f"{pair['pair_id']}:config:{sha}",
                {
                    "kind": "config_changed",
                    "pair_id": pair["pair_id"],
                    "sha256": sha,
                    "note": "le fichier a bougé après l'entrée ; le shadow garde les règles figées",
                },
                pair,
            )

    def _sync_flags(self, pair: dict[str, Any], row: dict[str, Any], now: float) -> None:
        after = flags_of(row)
        before = pair.get("source_flags") or flags_of({})
        origin, note = classify_be_transition(before, after)
        pair["source_flags"] = after
        pair["baseline_open"] = True
        if origin is None:
            return
        view = _view_from_row(row)
        event = {
            "origin": origin,
            "note": note,
            "ts": _now_iso(now),
            "level_pct": view["level_pct"],
            "kind": view["kind"],
            "side": "baseline",
        }
        pair["baseline_stop_events"].append(event)
        pair["baseline_stop_origin"] = origin
        self._bump(pair)
        self._emit(
            f"{pair['pair_id']}:origin:baseline:{origin}:{len(pair['baseline_stop_events'])}",
            {"kind": "stop_origin", **event, "pair_id": pair["pair_id"]},
            pair,
        )

    def _maybe_scale(self, pair: dict[str, Any], row: dict[str, Any], now: float) -> None:
        if pair.get("shadow_scaled"):
            return
        if not row.get("scaled_in"):
            return
        position = position_from_mapping(pair["position"])
        if position.scaled_in or not position.is_open:
            pair["shadow_scaled"] = True
            return
        try:
            add = float(row.get("size_usd") or 0) - float(position.size_usd)
            price = float(row.get("scale_in_price") or 0)
        except (TypeError, ValueError):
            add, price = 0.0, 0.0
        if add <= 1e-9 or price <= 0:
            self._taint(pair, "ambiguous_gap")
            pair["baseline_note"] = "scale-in source sans prix ou sans taille exploitable"
            pair["shadow_scaled"] = True
            return
        before = flags_of(dump_position(position))
        position = apply_scale_in(
            position,
            price,
            add,
            now=now,
            observed_price=row.get("scale_in_observed_price") or price,
        )
        # Le déclencheur indépendant reste coupé après l'ajout.
        position = _force_trigger_off(position)
        pair["position"] = dump_position(position)
        pair["shadow_scaled"] = True
        after = flags_of(pair["position"])
        origin, note = classify_be_transition(before, after)
        if origin:
            view = stop_view(position)
            event = {
                "origin": origin,
                "note": note,
                "ts": _now_iso(now),
                "level_pct": view["level_pct"],
                "kind": view["kind"],
                "side": "shadow",
                "add_usd": round(add, 4),
                "scale_price": price,
            }
            pair["shadow_stop_events"].append(event)
            pair["shadow_stop_origin"] = origin
        self._bump(pair)
        self._emit(
            f"{pair['pair_id']}:scale_in",
            {
                "kind": "scale_in",
                "pair_id": pair["pair_id"],
                "add_usd": round(add, 4),
                "scale_price": price,
                "avg_entry": position.entry_price,
                "stop": stop_view(position),
            },
            pair,
        )

    def _consider_price(
        self,
        pair: dict[str, Any],
        price: Any,
        price_ts: Optional[float],
        now: float,
        source: str,
    ) -> None:
        if str((pair.get("position") or {}).get("mode") or "PAPER").upper() != "PAPER":
            self._censor(pair, "refused_not_paper", "le shadow n'évalue pas un mode autre que PAPER")
            return
        if not pair.get("shadow_open"):
            self._observe_horizon_price(pair, price, price_ts, now)
            return
        try:
            number = float(price)
        except (TypeError, ValueError):
            number = 0.0
        if price is None or not math.isfinite(number) or number <= 0 or price_ts is None:
            pair["missing_quotes"] = int(pair.get("missing_quotes") or 0) + 1
            pair["mtm_status"] = "missing"
            return
        last_ts = pair.get("last_price_ts")
        if last_ts is not None and price_ts + 1e-6 < float(last_ts):
            pair["late_quotes_ignored"] = int(pair.get("late_quotes_ignored") or 0) + 1
            return
        if now - price_ts > STALE_SECONDS:
            pair["stale_quotes"] = int(pair.get("stale_quotes") or 0) + 1
            pair["mtm_status"] = "stale"
            return
        ambiguous = last_ts is not None and price_ts - float(last_ts) > AMBIGUOUS_GAP_SECONDS
        position = position_from_mapping(pair["position"])
        preview = _preview_exit(position, number)
        if ambiguous and preview:
            self._censor(pair, "censored_ambiguous_gap", "trou de prix avant une sortie de règle")
            return
        self._step(pair, position, number, price_ts, source)
        if ambiguous:
            self._taint(pair, "ambiguous_gap")
        pair["last_price"] = number
        pair["last_price_ts"] = price_ts
        pair["last_actionable_ts"] = price_ts
        pair["missing_quotes"] = 0
        self._set_mtm(pair, number, "fresh")
        self._observe_horizon_price(pair, number, price_ts, now)

    def _step(
        self,
        pair: dict[str, Any],
        position: Position,
        price: float,
        price_ts: float,
        source: str,
    ) -> None:
        before = flags_of(dump_position(position))
        position = update_water_marks(position, price)
        position = _force_trigger_off(position)
        actions = evaluate_exits(position, price, None)
        for action in actions:
            fraction = min(action.fraction, position.remaining_fraction)
            if fraction <= 1e-9:
                continue
            leg_id = f"{pair['pair_id']}:shadow_leg:{len(pair['shadow_leg_ids'])}"
            if leg_id in pair["shadow_leg_ids"]:
                break
            raw_cost, estimated, fee_source = _cost_for(action, pair)
            cost_entry = _cost_on_stake(raw_cost, price, position.entry_price)
            gross_pct = position.pnl_pct(price)
            net_pct = gross_pct - cost_entry
            gross_usd = position.size_usd * fraction * gross_pct / 100.0
            net_usd = position.size_usd * fraction * net_pct / 100.0
            position = apply_exit(position, action, price, cost_pct=cost_entry)
            position = _force_trigger_off(position)
            pair["position"] = dump_position(position)
            if not position.is_open:
                pair["shadow_open"] = False
                pair["terminal"] = _reason_code(position.exit_reasons[-1] if position.exit_reasons else "FLAT")
                pair["mtm_net_usd"] = 0.0
                pair["mtm_gross_usd"] = 0.0
                pair["mtm_status"] = "flat"
            leg = {
                "leg_id": leg_id,
                "ts": _now_iso(price_ts),
                "price": price,
                "source": source,
                "reason": action.reason,
                "fraction": fraction,
                "is_final": not position.is_open,
                "gross_pct": round(gross_pct, 4),
                "net_pct": round(net_pct, 4),
                "gross_usd": round(gross_usd, 4),
                "net_usd": round(net_usd, 4),
                "cost_pct_of_sold": raw_cost if estimated or raw_cost else 0.0,
                "cost_on_stake_pct": round(cost_entry, 4),
                "exit_cost_estimated": estimated,
                "fee_source": fee_source,
                "quality": pair.get("path_quality") or "clean",
            }
            pair["shadow_leg_ids"].append(leg_id)
            pair["shadow_legs"].append(leg)
            pair["shadow_realized_gross_usd"] = round(
                float(pair.get("shadow_realized_gross_usd") or 0) + gross_usd, 4
            )
            self._bump(pair)
            self._emit(
                leg_id,
                {"kind": "shadow_exit", "pair_id": pair["pair_id"], "leg": leg},
                pair,
            )
            if not position.is_open:
                break
        after = flags_of(dump_position(position))
        origin, note = classify_be_transition(before, after)
        if origin and origin != pair.get("shadow_stop_origin"):
            view = stop_view(position)
            event = {
                "origin": origin,
                "note": note,
                "ts": _now_iso(price_ts),
                "level_pct": view["level_pct"],
                "kind": view["kind"],
                "side": "shadow",
            }
            pair["shadow_stop_events"].append(event)
            pair["shadow_stop_origin"] = origin
            self._bump(pair)
            self._emit(
                f"{pair['pair_id']}:origin:shadow:{origin}:{len(pair['shadow_stop_events'])}",
                {"kind": "stop_origin", **event, "pair_id": pair["pair_id"]},
                pair,
            )
        pair["position"] = dump_position(position)

    def _ingest_journal(self, arm: str, row: dict[str, Any], now: float, priced: set[str]) -> None:
        source_id = row.get("position_id")
        row_id = row.get("id")
        if not source_id or not row_id:
            return
        pair = self.pairs.get(f"{arm}:{source_id}")
        if pair is None:
            return
        if row_id in pair.get("baseline_row_ids", []):
            return
        entry = row.get("entry_price") or (pair.get("position") or {}).get("entry_price")
        gross = _gross_usd(row.get("position_size"), entry, row.get("exit_price"))
        try:
            net = float(row.get("pnl_usd") or 0)
        except (TypeError, ValueError):
            net = 0.0
        pair["baseline_row_ids"].append(row_id)
        pair["baseline_legs"].append(
            {
                "id": row_id,
                "reason": row.get("exit_reason"),
                "is_final": bool(row.get("is_final_exit")),
                "net_usd": net,
                "gross_usd": None if gross is None else round(gross, 4),
                "exit_price": row.get("exit_price"),
                "exit_cost_pct": row.get("exit_cost_pct"),
                "pnl_floor_applied": bool(row.get("pnl_floor_applied")),
                "ts": row.get("timestamp_exit"),
            }
        )
        pair["baseline_realized_net_usd"] = round(float(pair.get("baseline_realized_net_usd") or 0) + net, 4)
        if gross is not None:
            pair["baseline_realized_gross_usd"] = round(
                float(pair.get("baseline_realized_gross_usd") or 0) + gross, 4
            )
        self._fill_origin_from_journal(pair, row)
        if row.get("is_final_exit"):
            pair["baseline_final_seen"] = True
            pair["baseline_open"] = False
            pair["baseline_exit_reason"] = row.get("exit_reason")
            pair["baseline_exit_ts"] = parse_ts(row.get("timestamp_exit"))
            try:
                pair["baseline_exit_price"] = float(row.get("exit_price"))
            except (TypeError, ValueError):
                pair["baseline_exit_price"] = None
            if (
                str(row.get("exit_reason") or "").startswith("BREAKEVEN_STOP")
                and pair.get("baseline_stop_origin") == "independent_trigger"
            ):
                pair["baseline_be_exit_ts"] = pair["baseline_exit_ts"]
                pair["baseline_be_exit_price"] = pair["baseline_exit_price"]
                pair["horizon_basis"] = "independent_trigger"
        self._bump(pair)
        self._emit(
            f"{pair['pair_id']}:baseline:{row_id}",
            {
                "kind": "baseline_exit",
                "pair_id": pair["pair_id"],
                "row_id": row_id,
                "reason": row.get("exit_reason"),
                "is_final": bool(row.get("is_final_exit")),
                "origin": pair.get("baseline_stop_origin"),
                "net_usd": net,
            },
            pair,
        )
        exit_ts = parse_ts(row.get("timestamp_exit")) or now
        # Un rug ou un panic est un fait observé, pas un prix à réinterpréter.
        # Le bot les évalue avant le stop : on les copie tels quels.
        if row.get("is_final_exit") and _is_external(str(row.get("exit_reason") or "")):
            self._copy_external(pair, row, exit_ts)
            pair["last_price"] = pair.get("baseline_exit_price")
            pair["last_price_ts"] = exit_ts
        else:
            self._consider_price(pair, row.get("exit_price"), exit_ts, now, "baseline_exit")
        priced.add(pair["pair_id"])

    def _fill_origin_from_journal(self, pair: dict[str, Any], row: dict[str, Any]) -> None:
        if pair.get("baseline_stop_origin"):
            return
        reasons = [str(leg.get("reason") or "") for leg in pair.get("baseline_legs") or []]
        if any(reason.startswith("TAKE_PROFIT_1") for reason in reasons):
            origin = "tp1"
        elif row.get("scaled_in") or (pair.get("source_flags") or {}).get("scaled_in"):
            origin = "scale_in"
        elif str(row.get("exit_reason") or "").startswith("BREAKEVEN_STOP"):
            origin = "independent_trigger"
        else:
            return
        exact = bool((pair.get("rules") or {}).get("breakeven_stop_exact"))
        # L'ajout force le stop exact, même si le bras n'avait pas le drapeau.
        if origin == "scale_in":
            kind = "exact_zero"
        else:
            kind = "exact_zero" if exact else "profit_floor"
        pair["baseline_stop_origin"] = origin
        pair["baseline_stop_events"].append(
            {
                "origin": origin,
                "note": "inferee_du_journal_sans_snapshot_intermediaire",
                "ts": row.get("timestamp_exit"),
                "level_pct": row.get("stop_loss_trigger_pct"),
                "kind": kind,
                "side": "baseline",
            }
        )

    def _copy_external(self, pair: dict[str, Any], row: dict[str, Any], exit_ts: float) -> None:
        if not pair.get("shadow_open"):
            return
        position = position_from_mapping(pair["position"])
        try:
            price = float(row.get("exit_price"))
        except (TypeError, ValueError):
            self._censor(pair, "censored_no_quote", "sortie externe sans prix")
            return
        reason = f"COPIED_{row.get('exit_reason')}"
        action = ExitAction(position.remaining_fraction, reason, is_final=True)
        self._step_forced(pair, position, action, price, exit_ts, row)

    def _step_forced(
        self,
        pair: dict[str, Any],
        position: Position,
        action: ExitAction,
        price: float,
        price_ts: float,
        row: dict[str, Any],
    ) -> None:
        fraction = min(action.fraction, position.remaining_fraction)
        leg_id = f"{pair['pair_id']}:shadow_leg:{len(pair['shadow_leg_ids'])}"
        if leg_id in pair["shadow_leg_ids"] or fraction <= 1e-9:
            return
        measured = row.get("exit_cost_pct")
        if isinstance(measured, (int, float)) and not row.get("exit_cost_estimated"):
            raw_cost = min(float(measured), SOLD_COST_CAP_PCT)
            estimated = False
            fee_source = "baseline_measured_same_exit"
        else:
            raw_cost, estimated, fee_source = _cost_for(action, pair)
        cost_entry = _cost_on_stake(raw_cost, price, position.entry_price)
        gross_pct = position.pnl_pct(price)
        net_pct = gross_pct - cost_entry
        gross_usd = position.size_usd * fraction * gross_pct / 100.0
        net_usd = position.size_usd * fraction * net_pct / 100.0
        position = apply_exit(position, action, price, cost_pct=cost_entry)
        position = _force_trigger_off(position)
        leg = {
            "leg_id": leg_id,
            "ts": _now_iso(price_ts),
            "price": price,
            "source": "copied_external",
            "reason": action.reason,
            "fraction": fraction,
            "is_final": True,
            "gross_pct": round(gross_pct, 4),
            "net_pct": round(net_pct, 4),
            "gross_usd": round(gross_usd, 4),
            "net_usd": round(net_usd, 4),
            "cost_pct_of_sold": raw_cost,
            "cost_on_stake_pct": round(cost_entry, 4),
            "exit_cost_estimated": estimated,
            "fee_source": fee_source,
            "quality": "external_observed",
        }
        pair["shadow_leg_ids"].append(leg_id)
        pair["shadow_legs"].append(leg)
        pair["shadow_realized_gross_usd"] = round(float(pair.get("shadow_realized_gross_usd") or 0) + gross_usd, 4)
        pair["position"] = dump_position(position)
        pair["shadow_open"] = False
        pair["terminal"] = _reason_code(action.reason)
        pair["mtm_net_usd"] = 0.0
        pair["mtm_gross_usd"] = 0.0
        pair["mtm_status"] = "flat"
        self._bump(pair)
        self._emit(leg_id, {"kind": "shadow_exit", "pair_id": pair["pair_id"], "leg": leg}, pair)

    def _apply_continuation(
        self,
        pair: dict[str, Any],
        quotes: dict[str, Any],
        now: float,
        priced: set[str],
    ) -> None:
        if pair["pair_id"] in priced:
            return
        if not pair.get("shadow_open") and not _horizon_pending(pair, now):
            return
        quote = quotes.get(pair.get("token_address"))
        if not quote:
            if pair.get("shadow_open"):
                pair["missing_quotes"] = int(pair.get("missing_quotes") or 0) + 1
                pair["mtm_status"] = "missing"
            return
        if not quote.get("matched"):
            pair["pair_note"] = quote.get("reason") or "unmatched"
            pair["missing_quotes"] = int(pair.get("missing_quotes") or 0) + 1
            return
        if quote.get("reason") == "pair_unanchored_single":
            pair["pair_note"] = "pair_unanchored_single"
        self._consider_price(pair, quote.get("price"), parse_ts(quote.get("ts")), now, "continuation")

    def _update_horizons(self, pair: dict[str, Any], now: float) -> None:
        start = pair.get("baseline_be_exit_ts")
        if start is None:
            return
        price = pair.get("last_price")
        price_ts = pair.get("last_price_ts")
        be_price = pair.get("baseline_be_exit_price")
        for minutes in HORIZONS_MIN:
            key = str(minutes)
            slot = (pair.get("horizons") or {}).get(key) or {}
            if slot.get("status") in ("filled", "missed"):
                continue
            target = float(start) + minutes * 60.0
            in_window = (
                price_ts is not None
                and price is not None
                and abs(float(price_ts) - target) <= HORIZON_TOLERANCE_SECONDS
            )
            if in_window:
                pnl = None
                if be_price:
                    pnl = round(100.0 * (float(price) / float(be_price) - 1.0), 4)
                self._set_horizon(
                    pair,
                    key,
                    {
                        "status": "filled",
                        "target_ts": target,
                        "ts": price_ts,
                        "price": price,
                        "pnl_vs_be_exit_pct": pnl,
                    },
                )
                continue
            # Pas d'interpolation : une cote hors fenêtre ne remplit pas l'horizon.
            if now > target + HORIZON_TOLERANCE_SECONDS:
                self._set_horizon(
                    pair,
                    key,
                    {"status": "missed", "target_ts": target, "late_ts": price_ts},
                )

    def _observe_horizon_price(self, pair: dict[str, Any], price: Any, price_ts: Optional[float], now: float) -> None:
        if price_ts is None:
            return
        try:
            number = float(price)
        except (TypeError, ValueError):
            return
        if number <= 0:
            return
        # Dernière cote connue pour les horizons, même shadow déjà fermé.
        previous = pair.get("last_price_ts")
        if previous is not None and price_ts + 1e-6 < float(previous):
            return
        pair["last_price"] = number
        pair["last_price_ts"] = price_ts
        self._update_horizons(pair, now)

    def _set_horizon(self, pair: dict[str, Any], key: str, slot: dict[str, Any]) -> None:
        pair.setdefault("horizons", {})[key] = slot
        self._bump(pair)
        self._emit(
            f"{pair['pair_id']}:horizon:{key}:{slot['status']}",
            {"kind": "horizon", "pair_id": pair["pair_id"], "minutes": int(key), **slot},
            pair,
        )

    def _maybe_censor(self, pair: dict[str, Any], now: float) -> None:
        if not pair.get("shadow_open"):
            return
        hold = float((pair.get("rules") or {}).get("max_hold_time_minutes") or 0) * 60.0
        if hold <= 0:
            return
        deadline = float(pair.get("entry_time") or now) + hold + CENSOR_GRACE_SECONDS
        if now < deadline:
            return
        last = pair.get("last_actionable_ts")
        hold_end = float(pair.get("entry_time") or now) + hold
        if last is None or hold_end - float(last) > AMBIGUOUS_GAP_SECONDS:
            self._censor(pair, "censored_no_quote", "aucune cote actionnable jusqu'après le time stop")

    def _censor(self, pair: dict[str, Any], terminal: str, note: str) -> None:
        if not pair.get("shadow_open"):
            return
        pair["shadow_open"] = False
        pair["terminal"] = terminal
        pair["path_quality"] = "censored"
        pair["baseline_note"] = note
        pair["mtm_status"] = "censored"
        pair["mtm_net_usd"] = None
        pair["mtm_gross_usd"] = None
        self._bump(pair)
        self._emit(
            f"{pair['pair_id']}:censor:{terminal}",
            {"kind": "censor", "pair_id": pair["pair_id"], "terminal": terminal, "note": note},
            pair,
        )

    def _taint(self, pair: dict[str, Any], quality: str) -> None:
        if pair.get("path_quality") == "censored":
            return
        if pair.get("path_quality") != quality:
            pair["path_quality"] = quality

    def _mark(self, pair: dict[str, Any], now: float) -> None:
        if pair.get("last_price_ts") is None:
            return
        bucket = int(float(pair["last_price_ts"]) // MARK_BUCKET_SECONDS)
        event_id = f"{pair['pair_id']}:mark:{bucket}"
        if event_id in self._seen or event_id in pair.get("applied_event_ids", []):
            return
        self._bump(pair)
        self._emit(
            event_id,
            {
                "kind": "mark",
                "pair_id": pair["pair_id"],
                "price": pair.get("last_price"),
                "price_ts": pair.get("last_price_ts"),
                "mtm_net_usd": pair.get("mtm_net_usd"),
                "shadow_stop": _view_from_position_dict(pair.get("position") or {}),
                "baseline_origin": pair.get("baseline_stop_origin"),
                "path_quality": pair.get("path_quality"),
                "wall_ts": _now_iso(now),
            },
        )

    def _set_mtm(self, pair: dict[str, Any], price: float, status: str) -> None:
        position = position_from_mapping(pair["position"])
        if not position.is_open:
            pair["mtm_net_usd"] = 0.0
            pair["mtm_gross_usd"] = 0.0
            pair["mtm_status"] = "flat"
            return
        gross = position.pnl_pct(price)
        assumption = (pair.get("fee") or {}).get("final_cost_assumption_pct")
        raw = float(assumption) if assumption else 0.0
        cost_entry = _cost_on_stake(raw, price, position.entry_price)
        net = gross - cost_entry
        frac = position.remaining_fraction
        pair["mtm_gross_usd"] = round(position.size_usd * frac * gross / 100.0, 4)
        pair["mtm_net_usd"] = round(position.size_usd * frac * net / 100.0, 4)
        pair["mtm_status"] = status

    def _refresh_effect(self, pair: dict[str, Any]) -> None:
        pair["live_delta_net_usd"] = None
        pair["delta_net_usd"] = None
        pair["delta_gross_usd"] = None
        policy = pair.get("policy_status") or ""
        if policy.startswith("ambiguous"):
            pair["effect_status"] = "not_comparable"
            return
        if pair.get("path_quality") == "censored" or "censor" in str(pair.get("terminal") or ""):
            pair["effect_status"] = "censored"
            return
        if pair.get("path_quality") == "ambiguous_gap" or pair.get("baseline_note", "").startswith("position source absente"):
            pair["effect_status"] = "ambiguous"
            return
        shadow_net = _shadow_net(pair)
        baseline_net = float(pair.get("baseline_realized_net_usd") or 0)
        if pair.get("shadow_open") or pair.get("baseline_open") or not pair.get("baseline_final_seen"):
            pair["effect_status"] = "incomplete"
            if (
                not pair.get("baseline_open")
                and pair.get("baseline_final_seen")
                and pair.get("shadow_open")
                and pair.get("mtm_net_usd") is not None
            ):
                pair["live_delta_net_usd"] = round(shadow_net + float(pair["mtm_net_usd"]) - baseline_net, 4)
            return
        pair["effect_status"] = "exact"
        pair["delta_net_usd"] = round(shadow_net - baseline_net, 4)
        pair["delta_gross_usd"] = round(
            float(pair.get("shadow_realized_gross_usd") or 0) - float(pair.get("baseline_realized_gross_usd") or 0),
            4,
        )

    def _park_if_terminal(self, pair_id: str) -> None:
        pair = self.pairs.get(pair_id)
        if pair is None:
            return
        if pair.get("shadow_open"):
            return
        # Garder le plein objet tant que des horizons peuvent encore se remplir.
        if _horizon_pending(pair, self.clock()):
            return
        self.pairs.pop(pair_id, None)
        self.closed = [row for row in self.closed if row.get("pair_id") != pair_id]
        self.closed.append(self._summary(pair))
        overflow = len(self.closed) - self.max_closed
        if overflow > 0:
            self.closed = self.closed[overflow:]

    def _summary(self, pair: dict[str, Any]) -> dict[str, Any]:
        kept = {
            key: pair.get(key)
            for key in (
                "schema",
                "pair_id",
                "arm",
                "source_position_id",
                "token_address",
                "symbol",
                "seq",
                "shadow_open",
                "baseline_open",
                "baseline_final_seen",
                "terminal",
                "path_quality",
                "policy_status",
                "policy_note",
                "config_changed_after_entry",
                "config_sha_at_entry",
                "config_sha_now",
                "rules_sha256",
                "params_version",
                "fee",
                "baseline_stop_origin",
                "shadow_stop_origin",
                "baseline_stop_events",
                "shadow_stop_events",
                "baseline_realized_net_usd",
                "baseline_realized_gross_usd",
                "shadow_realized_gross_usd",
                "baseline_exit_reason",
                "baseline_be_exit_ts",
                "horizon_basis",
                "horizons",
                "mtm_net_usd",
                "mtm_gross_usd",
                "mtm_status",
                "entry_time",
                "effect_status",
                "delta_net_usd",
                "delta_gross_usd",
                "live_delta_net_usd",
                "baseline_note",
                "position",
                "shadow_legs",
                "baseline_legs",
                "applied_event_ids",
            )
        }
        if pair.get("position"):
            view = _view_from_position_dict(pair["position"])
            kept["shadow_stop_level_pct"] = view.get("level_pct")
            kept["shadow_stop_kind"] = view.get("kind")
            kept["shadow_realized_net_usd"] = pair["position"].get("realized_pnl_usd")
        return kept

    def _bump(self, pair: dict[str, Any]) -> None:
        pair["seq"] = int(pair.get("seq") or 0) + 1
        pair.setdefault("applied_event_ids", [])

    def _write_outputs(self) -> None:
        self._refresh_all_effects()
        stats = self.paired_stats()
        os.makedirs(self.dir, exist_ok=True)
        self.save()
        tmp = os.path.join(self.dir, f".stats-{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(_jsonable(stats), handle, ensure_ascii=False, indent=1)
        os.replace(tmp, self.stats_path)
        heart = self.heartbeat_body()
        tmp = os.path.join(self.dir, f".heart-{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(_jsonable(heart), handle, ensure_ascii=False, indent=1)
        os.replace(tmp, self.heartbeat_path)

    def _refresh_all_effects(self) -> None:
        for pair in self.pairs.values():
            self._refresh_effect(pair)
        for row in self.closed:
            self._refresh_effect(row)

    def paired_stats(self) -> dict[str, Any]:
        arms: dict[str, Any] = {}
        groups: dict[str, list[dict[str, Any]]] = {}
        for pair in list(self.pairs.values()) + list(self.closed):
            groups.setdefault(pair.get("arm") or "?", []).append(pair)
        for arm, rows in groups.items():
            exact = [row for row in rows if row.get("effect_status") == "exact" and row.get("delta_net_usd") is not None]
            deltas = [float(row["delta_net_usd"]) for row in exact]
            equity, peak, drawdown, complete = self._equity(arm, rows)
            arms[arm] = {
                "open": sum(1 for row in rows if row.get("shadow_open")),
                "exact_closed": len(exact),
                "incomplete": sum(1 for row in rows if row.get("effect_status") == "incomplete"),
                "ambiguous": sum(1 for row in rows if row.get("effect_status") == "ambiguous"),
                "censored": sum(1 for row in rows if row.get("effect_status") == "censored"),
                "not_comparable": sum(1 for row in rows if row.get("effect_status") == "not_comparable"),
                "median_delta_net_usd": statistics.median(deltas) if deltas else None,
                "sum_delta_net_usd": round(sum(deltas), 4) if deltas else None,
                "reference_equity_usd": REFERENCE_EQUITY_USD,
                "measurement_equity_usd": equity,
                "measurement_peak_usd": peak,
                "measurement_drawdown_pct": drawdown,
                "drawdown_complete": complete,
                "pairs": [_public_pair(row) for row in rows],
            }
        return {
            "schema": "nobe-shadow-paired-1",
            "generated_at": _now_iso(self.clock()),
            "measure_version": MEASURE_VERSION,
            "reference_equity_usd": REFERENCE_EQUITY_USD,
            "equity_is_measurement_book": True,
            "not_alternate_portfolio": True,
            "not_arm_cash": True,
            "live_capable": False,
            "assumptions": list(ASSUMPTIONS),
            "arms": arms,
            "skipped": self.skipped,
        }

    def _equity(self, arm: str, rows: list[dict[str, Any]]) -> tuple[float, float, float, bool]:
        pnl = 0.0
        complete = True
        for row in rows:
            if row.get("effect_status") == "censored" or "censor" in str(row.get("terminal") or ""):
                complete = False
                pnl += _shadow_net(row)
                continue
            pnl += _shadow_net(row)
            if row.get("shadow_open"):
                if row.get("mtm_net_usd") is None or row.get("mtm_status") in ("missing", "stale", "censored"):
                    complete = False
                else:
                    pnl += float(row["mtm_net_usd"])
        equity = round(REFERENCE_EQUITY_USD + pnl, 4)
        peak = max(float(self.peaks.get(arm) or REFERENCE_EQUITY_USD), equity)
        self.peaks[arm] = peak
        drawdown = round(100.0 * (peak - equity) / peak, 4) if peak else 0.0
        return equity, peak, drawdown, complete

    def heartbeat_body(self) -> dict[str, Any]:
        disk = 0
        if os.path.isdir(self.dir):
            for name in os.listdir(self.dir):
                path = os.path.join(self.dir, name)
                if os.path.isfile(path):
                    disk += os.path.getsize(path)
        return {
            "schema": "nobe-shadow-heartbeat-1",
            "ts": _now_iso(self.clock()),
            "ok": self.last_error is None,
            "measure_version": MEASURE_VERSION,
            "mode": "PAPER",
            "live_capable": False,
            "arms": list(self.arms),
            "open_pairs": len(self.pairs),
            "closed_pairs": len(self.closed),
            "skipped": len(self.skipped),
            "pair_errors": self.pair_errors,
            "last_error": self.last_error,
            "disk_bytes": disk,
            "max_log_bytes": self.max_log_bytes,
            "reference_equity_usd": REFERENCE_EQUITY_USD,
        }

    def disk_bytes(self) -> int:
        return int(self.heartbeat_body()["disk_bytes"])


def _replace_shadow(position: Position, source_id: str) -> Position:
    from dataclasses import replace

    position = replace(
        position,
        id=f"nobe-{source_id}",
        breakeven_trigger=0.0,
        mode="PAPER",
        entry_context=dict(position.entry_context or {}),
    )
    return position


def _force_trigger_off(position: Position) -> Position:
    if position.breakeven_trigger == 0 and position.mode == "PAPER":
        return position
    from dataclasses import replace

    return replace(position, breakeven_trigger=0.0, mode="PAPER")


def _preview_exit(position: Position, price: float) -> bool:
    """La règle sortirait-elle à ce prix, sans modifier la position ?"""
    probed = update_water_marks(position, price)
    probed = _force_trigger_off(probed)
    return bool(evaluate_exits(probed, price, None))


def _cost_on_stake(raw: float, price: float, entry: float) -> float:
    """Coût ramené à la mise, plafonné à 100 % du revendu."""
    if not raw or price <= 0 or entry <= 0:
        return 0.0
    return cost_vs_entry(min(float(raw), SOLD_COST_CAP_PCT), price, entry)


def _cost_for(action: ExitAction, pair: dict[str, Any]) -> tuple[float, bool, str]:
    fee = pair.get("fee") or {}
    if action.is_final or not pair.get("shadow_open", True):
        assumption = fee.get("final_cost_assumption_pct")
        if assumption:
            return float(assumption), True, "median_round_trip_frozen_at_entry"
        return 0.0, False, "naked_insufficient_sample"
    partial = fee.get("partial_cost_pct")
    if partial:
        return float(partial), True, "partial_median_half_frozen_at_entry"
    return 0.0, False, "naked_insufficient_sample"


def _reason_code(reason: str) -> str:
    return (reason or "FLAT").split(" ", 1)[0]


def _shadow_net(pair: dict[str, Any]) -> float:
    position = pair.get("position") or {}
    try:
        return float(position.get("realized_pnl_usd") or 0)
    except (TypeError, ValueError):
        return 0.0


def _view_from_row(row: dict[str, Any]) -> dict[str, Any]:
    try:
        return stop_view(position_from_mapping(row))
    except (TypeError, ValueError):
        return {"level_pct": None, "kind": None}


def _view_from_position_dict(row: dict[str, Any]) -> dict[str, Any]:
    if not row:
        return {"level_pct": None, "kind": None}
    try:
        return stop_view(position_from_mapping(row))
    except (TypeError, ValueError):
        return {"level_pct": None, "kind": None}


def _public_pair(pair: dict[str, Any]) -> dict[str, Any]:
    view = _view_from_position_dict(pair.get("position") or {})
    last_baseline = (pair.get("baseline_stop_events") or [{}])[-1]
    return {
        "pair_id": pair.get("pair_id"),
        "source_position_id": pair.get("source_position_id"),
        "symbol": pair.get("symbol"),
        "token_address": pair.get("token_address"),
        "effect_status": pair.get("effect_status"),
        "terminal": pair.get("terminal"),
        "path_quality": pair.get("path_quality"),
        "policy_status": pair.get("policy_status"),
        "policy_note": pair.get("policy_note"),
        "config_changed_after_entry": pair.get("config_changed_after_entry"),
        "rules_sha256": pair.get("rules_sha256"),
        "params_version": pair.get("params_version"),
        "baseline_stop_origin": pair.get("baseline_stop_origin"),
        "baseline_stop_kind": last_baseline.get("kind"),
        "baseline_stop_level_pct": last_baseline.get("level_pct"),
        "shadow_stop_origin": pair.get("shadow_stop_origin"),
        "shadow_stop_kind": view.get("kind"),
        "shadow_stop_level_pct": view.get("level_pct"),
        "baseline_realized_net_usd": pair.get("baseline_realized_net_usd"),
        "shadow_realized_net_usd": _shadow_net(pair),
        "shadow_mtm_net_usd": pair.get("mtm_net_usd"),
        "delta_net_usd": pair.get("delta_net_usd"),
        "delta_gross_usd": pair.get("delta_gross_usd"),
        "live_delta_net_usd": pair.get("live_delta_net_usd"),
        "fee_policy": (pair.get("fee") or {}).get("name"),
        "horizons": pair.get("horizons"),
        "horizon_basis": pair.get("horizon_basis"),
        "baseline_note": pair.get("baseline_note"),
    }
