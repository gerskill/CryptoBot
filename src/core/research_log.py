"""Journal de recherche. Il observe, il ne décide pas.

A. Une ligne par jeton et par cycle où il est évalué, tous bras confondus.
   Uniquement des valeurs déjà en mémoire. Un champ absent est `null`,
   jamais 0.

B. Un fil à part suit le prix toutes les 60 s pendant 2 h. Tous les jetons
   entrés. Les refusés sont tirés au hasard. Le fil ne bloque ni le scan
   ni la surveillance des positions : s'il n'a pas de place dans le limiteur
   partagé, il saute le lot.

Tout s'éteint par `config/research_log.json` (`enabled: false`).
"""

from __future__ import annotations

import gzip
import json
import os
import random
import shutil
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

SCHEMA_VERSION = 1
CONFIG_NAME = os.path.join("config", "research_log.json")

# Portes d'entrée, dans l'ordre. La première qui échoue est `gate_failed`.
ENTRY_GATES = (
    "filtres",
    "seuil_alpha",
    "score_v2",
    "copy_smart",
    "anti_rug",
    "confluence",
    "portefeuille",
    "correlation",
    "pre_trade",
    "technique",
    "economie",
    "entree",
)

DEFAULTS = {
    "enabled": False,
    "candidates_enabled": True,
    "price_track_enabled": True,
    "klines_enabled": True,
    "max_total_bytes": 150 * 1024 * 1024,
    "min_free_bytes": 5 * 1024 * 1024 * 1024,
    "price_interval_seconds": 60,
    "price_horizon_seconds": 2 * 3600,
    "reject_sample_rate": 0.02,
    "max_calls_per_min": 2,
    "kline_lookback_seconds": 30 * 60,
}


def load_config(base_dir: str) -> dict[str, Any]:
    path = os.path.join(base_dir, CONFIG_NAME)
    cfg = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        cfg["enabled"] = False
        return cfg
    if isinstance(raw, dict):
        cfg.update({k: raw[k] for k in DEFAULTS if k in raw})
    return cfg


def _num(obj: Any, key: str) -> Optional[float]:
    if not isinstance(obj, dict) or key not in obj or obj[key] is None:
        return None
    try:
        return float(obj[key])
    except (TypeError, ValueError):
        return None


def _nested(obj: Any, *path: str) -> Optional[float]:
    cur = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    if cur is None:
        return None
    try:
        return float(cur)
    except (TypeError, ValueError):
        return None


def _raw_view(raw: Any) -> tuple[Optional[str], Optional[dict]]:
    if not isinstance(raw, dict):
        return None, None
    pair = raw.get("pair")
    if isinstance(pair, dict):
        return "dexscreener", pair
    if "address" in raw or "price" in raw:
        return "gmgn", raw
    return None, None


def _group(source: Optional[str], fields: dict[str, Any]) -> dict[str, Any]:
    body = {"schema_version": SCHEMA_VERSION, "source": source}
    body.update(fields)
    return body


def reason_code(text: Optional[str]) -> Optional[str]:
    """Code court. Le texte français du pipeline n'est pas recopié."""
    if not text:
        return None
    low = text.lower()
    table = (
        ("liquidit", "liquidity"),
        ("volume", "volume"),
        ("âge", "age"),
        ("age ", "age"),
        ("rug", "rugcheck"),
        ("holder", "holders"),
        ("concentration", "concentration"),
        ("social", "social"),
        ("mention", "social"),
        ("smart", "smart_money"),
        ("autorit", "authority"),
        ("mint", "authority"),
        ("freeze", "authority"),
        ("honeypot", "honeypot"),
        ("alpha", "alpha"),
        ("technique", "technical"),
        ("coût", "cost"),
        ("cout", "cost"),
        ("frais", "cost"),
        ("place", "capacity"),
        ("position", "capacity"),
        ("cooldown", "cooldown"),
        ("secteur", "sector"),
        ("correl", "sector"),
        ("confluence", "confluence"),
        ("consensus", "confluence"),
        ("wash", "wash"),
        ("bundle", "bundle"),
        ("dev", "dev"),
    )
    for needle, code in table:
        if needle in low:
            return code
    slug = []
    for ch in low:
        if ch.isalnum():
            slug.append(ch)
        elif slug and slug[-1] != "_":
            slug.append("_")
        if len(slug) >= 24:
            break
    out = "".join(slug).strip("_")
    return out or None


def _age_minutes(source: Optional[str], raw: Optional[dict], candidate: Any, now: float) -> Optional[float]:
    created = None
    if raw:
        if source == "gmgn":
            created = raw.get("creation_timestamp") or raw.get("open_timestamp")
        elif source == "dexscreener":
            created = raw.get("pairCreatedAt")
            if isinstance(created, (int, float)) and created > 1e12:
                created = created / 1000.0
    if isinstance(created, (int, float)) and created > 0:
        return round((now - float(created)) / 60.0, 2)
    age_h = getattr(candidate, "age_hours", None)
    if isinstance(age_h, (int, float)) and 0 <= age_h < 900:
        return round(float(age_h) * 60.0, 2)
    return None


def _gmgn_flags(raw: Optional[dict], source: Optional[str]) -> Optional[list[str]]:
    if source != "gmgn" or not isinstance(raw, dict):
        return None
    names = (
        "is_wash_trading",
        "renounced_mint",
        "renounced_freeze_account",
        "dexscr_ad",
        "dexscr_boost_fee",
    )
    return [name for name in names if raw.get(name) is True]


def _high_and_changes(candles: Optional[list[dict]], now: float) -> dict[str, Optional[float]]:
    """high 5 min, repli, variation 15 min, seulement si des bougies sont déjà là."""
    out = {
        "price_change_15m_pct": None,
        "high_5m": None,
        "drawdown_from_high_5m_pct": None,
    }
    if not candles:
        return out
    recent = [c for c in candles if now - 5 * 60 <= c["t"] <= now + 1]
    if recent:
        high = max(c["h"] for c in recent)
        last = max(recent, key=lambda c: c["t"])["c"]
        out["high_5m"] = high
        if high:
            out["drawdown_from_high_5m_pct"] = round(100.0 * (high - last) / high, 4)
    window = [c for c in candles if now - 30 * 60 <= c["t"] <= now + 1]
    if len(window) >= 2:
        window.sort(key=lambda c: c["t"])
        target = now - 15 * 60
        then = min(window, key=lambda c: abs(c["t"] - target))
        last = window[-1]
        if then["c"] and abs(then["t"] - target) <= 90:
            out["price_change_15m_pct"] = round(100.0 * (last["c"] / then["c"] - 1.0), 4)
    return out


def build_candidate_row(
    candidate: Any,
    *,
    now: float,
    cycle_id: int,
    decisions: list[dict[str, Any]],
    onchain: Optional[dict[str, Any]] = None,
    trades: Optional[list[dict[str, Any]]] = None,
    sol_price: Optional[float] = None,
    launches_volume_1h_usd: Optional[float] = None,
    candles: Optional[list[dict]] = None,
    roundtrip_cost_pct: Optional[float] = None,
    quote_size_usd: Optional[float] = None,
    sample_rate: float = 0.0,
    sampled: Optional[bool] = None,
    tracked: bool = False,
    track_reason: Optional[str] = None,
) -> dict[str, Any]:
    """Une ligne. N'invente aucune valeur : ce qui n'est pas là vaut null."""
    source, raw = _raw_view(getattr(candidate, "raw", None))
    onchain = onchain or {}
    on_ok = onchain.get("onchain_status") == "ok"
    kline = _high_and_changes(candles, now)

    if source == "dexscreener":
        price = _num(raw, "priceUsd")
        liquidity = _nested(raw, "liquidity", "usd")
        market_cap = _num(raw, "marketCap")
        volume_5m = _nested(raw, "volume", "m5")
        change_5m = _nested(raw, "priceChange", "m5")
        change_1m = None
        buys = _nested(raw, "txns", "m5", "buys")
        sells = _nested(raw, "txns", "m5", "sells")
        pool = raw.get("pairAddress") if raw else None
        dex = raw.get("dexId") if raw else None
    elif source == "gmgn":
        price = _num(raw, "price")
        liquidity = _num(raw, "liquidity")
        market_cap = _num(raw, "market_cap")
        # `volume` / `buys` suivent l'intervalle demandé (5m, 1h ou 24h).
        # Les écrire dans un champ « 5m » inventerait une fenêtre.
        volume_5m = _num(raw, "volume_5m")
        change_5m = _num(raw, "price_change_percent5m")
        change_1m = _num(raw, "price_change_percent1m")
        buys = _num(raw, "buys_5m")
        sells = _num(raw, "sells_5m")
        pool = getattr(candidate, "pair_address", None)
        dex = getattr(candidate, "dex", None)
    else:
        price = liquidity = market_cap = volume_5m = None
        change_5m = change_1m = buys = sells = None
        pool = getattr(candidate, "pair_address", None)
        dex = getattr(candidate, "dex", None)

    top10 = getattr(candidate, "top10_holder_pct", None)
    if top10 is None and on_ok:
        top10 = onchain.get("onchain_top10_pct")
    dev = getattr(candidate, "dev_wallet_pct", None)
    if dev is None and on_ok:
        dev = onchain.get("onchain_creator_pct_top20")
    bundle = getattr(candidate, "bundler_rate", None)
    bundle_pct = None if bundle is None else round(float(bundle) * 100.0, 4)
    if bundle_pct is None and on_ok:
        bundle_pct = onchain.get("onchain_bundle_buy_pct")

    buyers_5 = onchain.get("onchain_unique_buyers_5m") if on_ok else None
    buyers_15 = onchain.get("onchain_unique_buyers_15m") if on_ok else None

    tracked_ids: Optional[list[str]] = None
    tracked_n: Optional[int] = None
    if trades is not None:
        ids = []
        for trade in trades:
            token = (
                trade.get("token_address")
                or trade.get("base_address")
                or (trade.get("base_token") or {}).get("address")
            )
            if token != candidate.token_address:
                continue
            side = str(trade.get("side") or "")
            if side and side.lower() not in ("buy", "b"):
                continue
            ts = trade.get("timestamp") or trade.get("time") or trade.get("block_time")
            if isinstance(ts, (int, float)) and ts > 1e12:
                ts = ts / 1000.0
            if isinstance(ts, (int, float)) and now - float(ts) > 15 * 60:
                continue
            wallet = trade.get("maker") or trade.get("wallet") or trade.get("address")
            if wallet:
                ids.append(str(wallet))
        tracked_ids = sorted(set(ids))
        tracked_n = len(tracked_ids)

    progress = None
    migrated = None
    minutes_since = None
    if source == "gmgn" and raw:
        if "bonding_curve_progress" in raw and raw["bonding_curve_progress"] is not None:
            progress = _num(raw, "bonding_curve_progress")
        if "migrated" in raw and isinstance(raw["migrated"], bool):
            migrated = raw["migrated"]
        mig_ts = raw.get("migrated_timestamp")
        if isinstance(mig_ts, (int, float)) and mig_ts > 0:
            if mig_ts > 1e12:
                mig_ts = mig_ts / 1000.0
            minutes_since = round((now - float(mig_ts)) / 60.0, 2)

    return {
        "schema_version": SCHEMA_VERSION,
        "ts_utc": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scan_cycle_id": cycle_id,
        "token_address": candidate.token_address,
        "pool_address": pool,
        "dex": dex,
        "launchpad": getattr(candidate, "launchpad", None),
        "decisions": decisions,
        "lifecycle": _group(source, {
            "age_minutes": _age_minutes(source, raw, candidate, now),
            "bonding_curve_progress_pct": progress,
            "migrated": migrated,
            "minutes_since_migration": minutes_since,
        }),
        "price": _group(source if kline["high_5m"] is None else f"{source}+kline" if source else "kline", {
            "price_usd": price,
            "market_cap": market_cap,
            "liquidity_usd": liquidity,
            "price_change_1m_pct": change_1m,
            "price_change_5m_pct": change_5m,
            "price_change_15m_pct": kline["price_change_15m_pct"],
            "high_5m": kline["high_5m"],
            "drawdown_from_high_5m_pct": kline["drawdown_from_high_5m_pct"],
        }),
        "flow": _group(source, {
            "volume_5m_usd": volume_5m,
            "buys_5m": None if buys is None else int(buys),
            "sells_5m": None if sells is None else int(sells),
            "unique_buyers_5m": buyers_5,
            "unique_buyers_15m": buyers_15,
            "unique_sellers_5m": None,
            "new_wallet_buyer_share": None,
            "both_side_wallet_share": None,
            "is_wash": getattr(candidate, "is_wash_trading", None),
        }),
        "structure": _group("onchain" if on_ok and source is None else source, {
            "top10_pct": top10,
            "dev_hold_pct": dev,
            "bundle_share_pct": bundle_pct,
            "mint_revoked": getattr(candidate, "mint_authority_revoked", None),
            "freeze_revoked": getattr(candidate, "freeze_authority_revoked", None),
            "lp_burned_or_locked_pct": getattr(candidate, "lp_locked_pct", None),
            "gmgn_flags": _gmgn_flags(raw, source),
            "rugcheck_score": getattr(candidate, "rugcheck_score", None),
        }),
        "wallets": _group("gmgn_cache" if trades is not None else None, {
            "tracked_buyers_15m": tracked_n,
            "tracked_buyer_ids": tracked_ids,
        }),
        "cost": _group("jupiter" if roundtrip_cost_pct is not None else None, {
            "roundtrip_cost_pct": roundtrip_cost_pct,
            "quote_size_usd": quote_size_usd,
        }),
        "regime": _group("cycle" if launches_volume_1h_usd is not None or sol_price is not None else None, {
            "sol_price": sol_price,
            "sol_change_1h_pct": None,
            "launches_volume_1h_usd": launches_volume_1h_usd,
        }),
        "price_track": _group("research", {
            "sample_rate": sample_rate,
            "sampled": sampled,
            "tracked": tracked,
            "reason": track_reason,
        }),
    }


def decisions_for(
    arm: str,
    candidate: Any,
    *,
    rejected: bool,
    threshold: float,
    funnel_rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    if rejected:
        return {
            "arm": arm,
            "passed": False,
            "gate_failed": "filtres",
            "reason_code": reason_code(getattr(candidate, "rejected_reason", None)),
        }
    alpha = getattr(candidate, "alpha_score_absolute", None)
    if alpha is None or alpha < threshold:
        return {
            "arm": arm,
            "passed": False,
            "gate_failed": "seuil_alpha",
            "reason_code": "alpha",
        }
    later = [
        row for row in funnel_rows
        if row.get("arm") == arm
        and row.get("token") == candidate.token_address
        and row.get("gate") in ENTRY_GATES
        and row.get("gate") not in ("filtres", "seuil_alpha", "filtres_total")
    ]
    for row in later:
        if not row.get("passed"):
            return {
                "arm": arm,
                "passed": False,
                "gate_failed": row.get("gate"),
                "reason_code": reason_code(row.get("reason")),
            }
    return {"arm": arm, "passed": True, "gate_failed": None, "reason_code": None}


def _cost_from_funnel(token: str, rows: Iterable[dict[str, Any]]) -> tuple[Optional[float], Optional[float]]:
    for row in rows:
        if row.get("token") != token or row.get("gate") != "economie":
            continue
        cost = row.get("round_trip_pct")
        size = row.get("size_usd")
        if cost is not None or size is not None:
            return cost, size
    return None, None


class ResearchDisk:
    """Écriture, gzip du jour précédent, plafond, arrêt si le disque est bas."""

    def __init__(
        self,
        root: str,
        *,
        max_total_bytes: int,
        min_free_bytes: int,
        free_bytes: Optional[Callable[[], int]] = None,
        today: Optional[Callable[[], str]] = None,
    ) -> None:
        self.root = root
        self.max_total_bytes = max_total_bytes
        self.min_free_bytes = min_free_bytes
        self._free_bytes = free_bytes or (lambda: shutil.disk_usage(self.root).free)
        self._today = today or (lambda: datetime.now(timezone.utc).strftime("%Y%m%d"))
        self.halted = False
        self.halt_reason: Optional[str] = None
        self._alerted = False

    def _dir(self, kind: str) -> str:
        path = os.path.join(self.root, kind)
        os.makedirs(path, exist_ok=True)
        return path

    def path_for(self, kind: str, day: Optional[str] = None) -> str:
        return os.path.join(self._dir(kind), f"{day or self._today()}.jsonl")

    def append(self, kind: str, row: dict[str, Any]) -> bool:
        if self.halted:
            return False
        if not self._space_ok():
            return False
        path = self.path_for(kind)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True

    def _space_ok(self) -> bool:
        try:
            free = int(self._free_bytes())
        except OSError:
            return True
        if free < self.min_free_bytes:
            self._halt(
                f"espace libre {free / 1e9:.2f} Go sous {self.min_free_bytes / 1e9:.0f} Go"
            )
            return False
        return True

    def maintain(self) -> None:
        """Gzip des jours précédents, puis respect du plafond. Ne touche pas au jour courant."""
        today = self._today()
        for dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                if not name.endswith(".jsonl"):
                    continue
                day = name[:8]
                if not (day.isdigit() and day < today):
                    continue
                src = os.path.join(dirpath, name)
                dst = src + ".gz"
                if os.path.exists(dst):
                    continue
                try:
                    with open(src, "rb") as raw, gzip.open(dst, "wb") as gz:
                        gz.write(raw.read())
                    os.remove(src)
                except OSError as exc:
                    print(f"[Research] ⚠️ gzip {name} — {type(exc).__name__}")
        files = self._files()
        total = sum(size for _path, size, _mtime in files)
        if total <= self.max_total_bytes:
            return
        # Les .gz les plus vieux d'abord. Le fichier du jour reste.
        old = sorted(
            (item for item in files if item[0].endswith(".gz")),
            key=lambda item: item[2],
        )
        for path, size, _mtime in old:
            if total <= self.max_total_bytes:
                break
            try:
                os.remove(path)
                total -= size
            except OSError:
                continue
        if total > self.max_total_bytes:
            self._halt(
                f"journal {total / 1e6:.0f} Mo au-dessus du plafond "
                f"{self.max_total_bytes / 1e6:.0f} Mo"
            )

    def _files(self) -> list[tuple[str, int, float]]:
        found = []
        if not os.path.isdir(self.root):
            return found
        for dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                path = os.path.join(dirpath, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                found.append((path, st.st_size, st.st_mtime))
        return found

    def _halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason
        if not self._alerted:
            self._alerted = True
            print(f"[Research] arrêt de l'enregistrement — {reason}")


class CallBudget:
    def __init__(self, per_min: int, clock: Callable[[], float]) -> None:
        self.per_min = max(0, int(per_min))
        self._clock = clock
        self._times: list[float] = []

    def take(self) -> bool:
        now = self._clock()
        self._times = [t for t in self._times if now - t < 60.0]
        if len(self._times) >= self.per_min:
            return False
        self._times.append(now)
        return True


class ResearchJournal:
    def __init__(
        self,
        base_dir: str,
        config: dict[str, Any],
        *,
        fetch: Optional[Callable[[list[str]], Optional[dict]]] = None,
        clock: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
        rng: Optional[random.Random] = None,
        free_bytes: Optional[Callable[[], int]] = None,
        today: Optional[Callable[[], str]] = None,
    ) -> None:
        self.config = config
        self.clock = clock
        self.mono = mono
        self.rng = rng or random.Random()
        self.fetch = fetch
        root = os.path.join(base_dir, "data")
        self.disk = ResearchDisk(
            root,
            max_total_bytes=int(config["max_total_bytes"]),
            min_free_bytes=int(config["min_free_bytes"]),
            free_bytes=free_bytes,
            today=today,
        )
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._tracks: dict[str, dict[str, Any]] = {}
        self._klines: dict[str, list[dict]] = {}
        self._entered: set[str] = set()
        self._sol_price: Optional[float] = None
        self._budget = CallBudget(int(config["max_calls_per_min"]), mono)
        self.calls = 0

    @classmethod
    def load(cls, base_dir: str, fetch: Optional[Callable] = None) -> "ResearchJournal":
        return cls(base_dir, load_config(base_dir), fetch=fetch)

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled"))

    def start(self) -> None:
        if not self.enabled or (self._thread is not None and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="research-journal", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def note_sol(self, price: Optional[float]) -> None:
        if not self.enabled or price is None or price <= 0:
            return
        self._sol_price = float(price)

    def note_entered(self, token: str) -> None:
        if not self.enabled or not token:
            return
        with self._lock:
            self._entered.add(token)

    def note_klines(self, token: str, candles: Iterable[Any], source: str) -> None:
        if not self.enabled or not self.config.get("klines_enabled"):
            return
        now = self.clock()
        lookback = float(self.config["kline_lookback_seconds"])
        kept = []
        for candle in candles:
            bucket = getattr(candle, "bucket", None)
            if bucket is None and isinstance(candle, dict):
                bucket = candle.get("t")
            if bucket is None:
                continue
            ts = float(bucket)
            if now - lookback <= ts <= now + 1:
                high = getattr(candle, "high", None)
                low = getattr(candle, "low", None)
                close = getattr(candle, "close", None)
                volume = getattr(candle, "volume", None)
                if isinstance(candle, dict):
                    high = candle.get("h", high)
                    low = candle.get("l", low)
                    close = candle.get("c", close)
                    volume = candle.get("v", volume)
                kept.append({
                    "t": int(ts),
                    "h": None if high is None else float(high),
                    "l": None if low is None else float(low),
                    "c": None if close is None else float(close),
                    "v": None if volume is None else float(volume),
                })
        if not kept:
            return
        with self._lock:
            self._klines[token] = kept
        if self.disk.halted:
            return
        self.disk.append("klines_pre", {
            "schema_version": SCHEMA_VERSION,
            "source": source,
            "ts_utc": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "token_address": token,
            "candles": kept,
        })

    def record_cycle(
        self,
        *,
        cycle_id: int,
        evaluations: dict[str, Any],
        thresholds: dict[str, float],
        funnel_rows: Iterable[dict[str, Any]],
        onchain_lookup: Optional[Callable[[str], dict]] = None,
        trades: Optional[list[dict[str, Any]]] = None,
    ) -> int:
        """Écrit les lignes du cycle. Aucun appel réseau."""
        if not self.enabled or not self.config.get("candidates_enabled"):
            return 0
        if self.disk.halted:
            return 0
        rows_in = list(funnel_rows)
        now = self.clock()
        by_token: dict[str, Any] = {}
        decisions: dict[str, list[dict]] = {}
        for arm, evaluation in evaluations.items():
            result = getattr(evaluation, "result", None)
            if result is None:
                continue
            threshold = float(thresholds.get(arm, 0))
            for candidate in tuple(result.rejected) + tuple(result.candidates):
                by_token.setdefault(candidate.token_address, candidate)
            for candidate in result.rejected:
                decisions.setdefault(candidate.token_address, []).append(
                    decisions_for(
                        arm, candidate, rejected=True, threshold=threshold,
                        funnel_rows=rows_in,
                    )
                )
            for candidate in result.candidates:
                decisions.setdefault(candidate.token_address, []).append(
                    decisions_for(
                        arm, candidate, rejected=False, threshold=threshold,
                        funnel_rows=rows_in,
                    )
                )

        volumes = []
        for candidate in by_token.values():
            _source, raw = _raw_view(getattr(candidate, "raw", None))
            # Seulement un volume dont la fenêtre est vraiment 1 h.
            vol = _nested(raw, "volume", "h1") if _source == "dexscreener" else None
            if vol is not None:
                volumes.append(vol)
        launches = round(sum(volumes), 4) if volumes else None
        rate = float(self.config["reject_sample_rate"])
        written = 0
        with self._lock:
            entered = set(self._entered)
            self._entered.clear()
            klines = dict(self._klines)
            self._klines.clear()
            sol = self._sol_price
        for token, candidate in by_token.items():
            is_entered = token in entered
            if is_entered:
                sampled, tracked, why = None, True, "entered"
            else:
                sampled = self.rng.random() < rate
                tracked, why = sampled, "sample" if sampled else None
            cost, size = _cost_from_funnel(token, rows_in)
            snap = None
            if onchain_lookup is not None:
                try:
                    snap = onchain_lookup(token)
                except Exception:
                    snap = None
            row = build_candidate_row(
                candidate,
                now=now,
                cycle_id=cycle_id,
                decisions=decisions.get(token, []),
                onchain=snap,
                trades=trades,
                sol_price=sol,
                launches_volume_1h_usd=launches,
                candles=klines.get(token),
                roundtrip_cost_pct=cost,
                quote_size_usd=size,
                sample_rate=rate,
                sampled=sampled,
                tracked=tracked,
                track_reason=why,
            )
            if self.disk.append("candidates", row):
                written += 1
            if tracked and self.config.get("price_track_enabled"):
                self._register(token, now, row["price"]["price_usd"], row["price"]["liquidity_usd"], why)
        return written

    def _register(
        self,
        token: str,
        now: float,
        price: Optional[float],
        liquidity: Optional[float],
        reason: Optional[str],
    ) -> None:
        horizon = float(self.config["price_horizon_seconds"])
        with self._lock:
            track = self._tracks.get(token)
            if track is None:
                self._tracks[token] = {
                    "since": now,
                    "until": now + horizon,
                    "last": now,
                    "reason": reason,
                }
            elif reason == "entered":
                track["reason"] = "entered"
        if price is None and liquidity is None:
            return
        self.disk.append("price_track", {
            "schema_version": SCHEMA_VERSION,
            "ts_utc": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "token_address": token,
            "price_usd": price,
            "liquidity_usd": liquidity,
            "source": "scan",
        })

    def tick(self) -> int:
        """Un passage du fil. Retourne le nombre d'appels réellement partis."""
        if not self.enabled or not self.config.get("price_track_enabled"):
            return 0
        if self.disk.halted:
            return 0
        now = self.clock()
        interval = float(self.config["price_interval_seconds"])
        with self._lock:
            due_entered = []
            due_other = []
            expired = []
            for token, track in self._tracks.items():
                if now >= track["until"]:
                    expired.append(token)
                    continue
                if now - track["last"] + 1e-6 < interval:
                    continue
                if track.get("reason") == "entered":
                    due_entered.append(token)
                else:
                    due_other.append(token)
            for token in expired:
                self._tracks.pop(token, None)
        due = due_entered + due_other
        calls = 0
        while due:
            if not self._budget.take():
                break
            batch = due[:30]
            due = due[30:]
            if self.fetch is None:
                break
            try:
                got = self.fetch(batch)
            except Exception:
                got = None
            if got is None:
                # Pas de place dans le limiteur : on rend le créneau.
                self._budget._times.pop()
                break
            self.calls += 1
            calls += 1
            stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            with self._lock:
                for token in batch:
                    track = self._tracks.get(token)
                    if track is not None:
                        track["last"] = now
                    price, liquidity = (None, None)
                    if isinstance(got, dict):
                        price, liquidity = got.get(token, (None, None))
                    self.disk.append("price_track", {
                        "schema_version": SCHEMA_VERSION,
                        "ts_utc": stamp,
                        "token_address": token,
                        "price_usd": price,
                        "liquidity_usd": liquidity,
                        "source": "dexscreener",
                    })
        return calls

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.disk.maintain()
                self.tick()
            except Exception as exc:  # le fil ne doit rien casser
                print(f"[Research] ⚠️ {type(exc).__name__}")
            wait = float(self.config["price_interval_seconds"])
            if self._stop.wait(max(0.05, wait)):
                return


def estimate_day(
    *,
    cycles_per_day: int,
    tokens_per_cycle: int,
    line_bytes: int,
    gzip_ratio: float,
    price_samples_per_track: int,
    tracks_per_day: int,
    price_line_bytes: int,
) -> dict[str, float]:
    """Bornes hautes. Le même jeton revient souvent : le réel est plus bas."""
    lines = cycles_per_day * tokens_per_cycle
    raw = lines * line_bytes
    price_lines = tracks_per_day * price_samples_per_track
    price_raw = price_lines * price_line_bytes
    return {
        "candidate_lines": lines,
        "candidate_mb_gzip": raw * gzip_ratio / 1e6,
        "price_lines": price_lines,
        "price_mb_gzip": price_raw * gzip_ratio / 1e6,
    }
