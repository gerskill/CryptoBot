"""Analyse technique — étape 3A du workflow.

Source primaire : vraies bougies Birdeye (`/defi/ohlcv`), 60 bougies 1m
disponibles immédiatement. Repli : bougies reconstruites depuis les snapshots
de chaque cycle — moins fiables, et avec un délai de chauffe.

⚠️ Le critère de structure de la spec (higher highs ET higher lows sur 3
bougies) ne se déclenche JAMAIS : mesuré à 2.3% seul et 0% croisé avec
l'expansion de volume, sur 43 memecoins Solana réels. Voir STRUCTURE_MODES.
"""

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from src.core.models import Candidate

BUCKET_SECONDS = 180  # surchargé par entry_rules.candle_seconds
REQUIRED_BUCKETS = 3  # surchargé par entry_rules.required_candles
MAX_SNAPSHOTS_PER_TOKEN = 240  # ~6h à 90s

# Taux de passage MESURÉS sur 43 memecoins Solana réels, bougies 1m Birdeye.
# Le mode "strict" applique la spec à la lettre — et ne se déclenche jamais.
# Un bot dans ce mode ne prend aucun trade, donc ne produit aucun apprentissage.
STRUCTURE_MODES = {
    # mode        structure seule   combiné au volume
    "strict":    (2.3,              0.0),   # HH + HL sur 3 bougies
    "balanced":  (18.6,             4.7),   # tendance nette close[-1] > close[0]
}
DEFAULT_STRUCTURE_MODE = "balanced"


@dataclass(frozen=True)
class Snapshot:
    ts: float
    price: float
    volume_5m: float
    liquidity: float


@dataclass(frozen=True)
class Candle:
    bucket: int
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class TechnicalVerdict:
    passed: bool
    pending: bool = False  # historique insuffisant, à revoir au prochain cycle
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    candles: tuple[Candle, ...] = ()

    @property
    def label(self) -> str:
        if self.pending:
            return "PENDING"
        return "PASS" if self.passed else "FAIL"


class PriceHistory:
    """Historique de snapshots par token, alimenté à chaque cycle de scan."""

    def __init__(self, max_per_token: int = MAX_SNAPSHOTS_PER_TOKEN):
        self._data: dict[str, deque[Snapshot]] = defaultdict(
            lambda: deque(maxlen=max_per_token)
        )

    def record(self, candidate: Candidate, ts: Optional[float] = None) -> None:
        self.record_raw(
            candidate.token_address,
            price=candidate.price_usd,
            volume_5m=candidate.volume_5m,
            liquidity=candidate.liquidity_usd,
            ts=ts,
        )

    def record_raw(
        self,
        token_address: str,
        price: float,
        volume_5m: float = 0.0,
        liquidity: float = 0.0,
        ts: Optional[float] = None,
    ) -> None:
        """Snapshot hors scan — utilisé par le monitoring des positions ouvertes."""
        if price <= 0:
            return
        self._data[token_address].append(
            Snapshot(
                ts=ts if ts is not None else time.time(),
                price=price,
                volume_5m=volume_5m,
                liquidity=liquidity,
            )
        )

    def record_all(self, candidates: list[Candidate]) -> None:
        now = time.time()
        for candidate in candidates:
            self.record(candidate, ts=now)

    def snapshots(self, token_address: str) -> list[Snapshot]:
        return list(self._data.get(token_address, ()))

    def candles(self, token_address: str, bucket_seconds: int = BUCKET_SECONDS) -> list[Candle]:
        """Agrège les snapshots en bougies. La bougie en cours est incluse."""
        buckets: dict[int, list[Snapshot]] = defaultdict(list)
        for snapshot in self._data.get(token_address, ()):
            buckets[int(snapshot.ts // bucket_seconds)].append(snapshot)

        candles = []
        for bucket in sorted(buckets):
            points = buckets[bucket]
            prices = [p.price for p in points]
            candles.append(
                Candle(
                    bucket=bucket,
                    high=max(prices),
                    low=min(prices),
                    close=points[-1].price,
                    volume=max(p.volume_5m for p in points),
                )
            )
        return candles

    def liquidity_drop_pct(self, token_address: str, window_seconds: float = 120) -> Optional[float]:
        """Chute de liquidité sur la fenêtre (détection de rug pull, étape 4.7)."""
        snapshots = self._data.get(token_address)
        if not snapshots:
            return None
        now = time.time()
        window = [s for s in snapshots if now - s.ts <= window_seconds]
        if len(window) < 2 or window[0].liquidity <= 0:
            return None
        return 100 * (window[-1].liquidity - window[0].liquidity) / window[0].liquidity

    def forget(self, token_address: str) -> None:
        self._data.pop(token_address, None)

    @property
    def tracked_count(self) -> int:
        return len(self._data)


def from_ohlcv(items: Sequence[Any]) -> list[Candle]:
    """Convertit des bougies Birdeye (`OHLCVCandle`) en bougies internes."""
    return [
        Candle(
            bucket=int(getattr(item, "unix_time", 0)),
            high=float(item.high),
            low=float(item.low),
            close=float(item.close),
            volume=float(item.volume),
        )
        for item in items
        if float(item.high) > 0
    ]


def from_gmgn_kline(items: Sequence[dict]) -> list[Candle]:
    """Convertit des bougies GMGN. Deux pièges que Birdeye n'a pas.

    1. `time` est en MILLISECONDES, pas en secondes. Le bucket sert à ordonner
       et à mesurer des durées : une erreur d'un facteur 1000 rendrait toutes
       les bougies contemporaines.
    2. Les prix sont des CHAÎNES, pas des nombres.

    C'est le repli gratuit de Birdeye, dont le quota mensuel s'épuise et qui
    plafonne à 1 requête par seconde.
    """
    candles = []
    for item in items:
        try:
            high = float(item.get("high") or 0)
            if high <= 0:
                continue
            raw_time = float(item.get("time") or 0)
            candles.append(
                Candle(
                    bucket=int(raw_time / 1000 if raw_time > 1e11 else raw_time),
                    high=high,
                    low=float(item.get("low") or 0),
                    close=float(item.get("close") or 0),
                    volume=float(item.get("volume") or 0),
                )
            )
        except (TypeError, ValueError):
            continue
    return sorted(candles, key=lambda c: c.bucket)


def analyze(
    candidate: Candidate,
    history: Optional[PriceHistory] = None,
    max_initial_dump_pct: float = -50.0,
    candle_seconds: int = BUCKET_SECONDS,
    required_candles: int = REQUIRED_BUCKETS,
    candles: Optional[Sequence[Candle]] = None,
    structure_mode: str = DEFAULT_STRUCTURE_MODE,
) -> TechnicalVerdict:
    """Valide la structure d'entrée : pas de dump, tendance, volume en expansion."""
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    # 1. Pas de dump initial depuis la création (h24 ≈ depuis création si âge < 6h)
    no_dump = candidate.price_change_24h > max_initial_dump_pct
    checks["no_initial_dump"] = no_dump
    if not no_dump:
        reasons.append(f"dump initial {candidate.price_change_24h:.1f}% < {max_initial_dump_pct}%")

    source = "réelles"
    series = list(candles) if candles else []
    if not series:
        source = "reconstruites"
        series = (
            history.candles(candidate.token_address, bucket_seconds=candle_seconds)
            if history
            else []
        )

    if len(series) < required_candles:
        minutes = candle_seconds // 60
        return TechnicalVerdict(
            passed=False,
            pending=True,
            checks=checks,
            reasons=(
                f"historique insuffisant ({len(series)}/{required_candles} "
                f"bougies {minutes}m {source})",
            ),
            candles=tuple(series),
        )

    recent = series[-required_candles:]

    # 2. Structure haussière — voir STRUCTURE_MODES pour les taux mesurés.
    if structure_mode == "strict":
        higher_highs = all(recent[i].high > recent[i - 1].high for i in range(1, len(recent)))
        higher_lows = all(recent[i].low >= recent[i - 1].low for i in range(1, len(recent)))
        checks["higher_highs"] = higher_highs
        checks["higher_lows"] = higher_lows
        if not higher_highs:
            reasons.append(f"pas de higher highs sur {required_candles} bougies")
        if not higher_lows:
            reasons.append(f"pas de higher lows sur {required_candles} bougies")
    else:
        uptrend = recent[-1].close > recent[0].close
        checks["uptrend"] = uptrend
        if not uptrend:
            change = 100 * (recent[-1].close - recent[0].close) / recent[0].close
            reasons.append(
                f"pas de tendance haussière sur {required_candles} bougies ({change:+.1f}%)"
            )

    # 3. Volume en EXPANSION, pas strictement croissant bougie par bougie.
    #    Exiger 3 hausses consécutives sur une série bruitée ne se produit
    #    quasiment jamais : mesuré en live, 0 passage sur 12 évaluations.
    prior = series[-2 * required_candles : -required_candles]
    if prior:
        recent_avg = sum(c.volume for c in recent) / len(recent)
        prior_avg = sum(c.volume for c in prior) / len(prior)
        volume_ok = recent_avg > prior_avg
        detail = f"volume moyen {recent_avg:,.0f} ≤ {prior_avg:,.0f} (période précédente)"
    else:
        volume_ok = all(recent[i].volume >= recent[i - 1].volume for i in range(1, len(recent)))
        detail = "volume non croissant (historique trop court pour comparer)"
    checks["volume_expanding"] = volume_ok
    if not volume_ok:
        reasons.append(detail)

    return TechnicalVerdict(
        passed=all(checks.values()),
        pending=False,
        checks=checks,
        reasons=tuple(reasons),
        candles=tuple(recent),
    )
