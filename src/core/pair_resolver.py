"""Résolution de la paire DexScreener des positions entrées SANS `pair_address`.

POURQUOI. Les candidats venus uniquement de GMGN (trending/rank, mature_liquid)
entrent sans paire. Le monitoring n'avait alors que le prix Jupiter et une
liquidité INCONNUE — d'où 225 des 229 faux RUG_PULL « −100 % » mesurés le
2026-10-11, et surtout AUCUNE surveillance réelle de la liquidité.

RÈGLES.
- Paire retenue : même chaîne, prix > 0, liquidité USD la plus élevée. Une
  paire `pumpfun` (courbe de bonding, sans champ liquidité) n'est retenue que
  s'il n'existe AUCUNE paire migrée (PumpSwap/Raydium/Meteora...) avec liquidité.
- Appels GROUPÉS : `get_tokens_data` traite 30 tokens par requête, et un passage
  ne fait au plus que `max_batches` requêtes.
- Cache par token : une tentative (réussie ou non) n'est pas refaite avant
  `retry_seconds`. Pas de martèlement d'un token sans paire indexée.
- Re-résolution : `mark_stale(token)` quand la paire suivie perd son champ
  liquidité ou disparaît (migration pump.fun → PumpSwap/Meteora).
- Aucune décision d'entrée ne passe par ici : n'agit que sur des positions ouvertes.
"""

import time
from typing import Any, Callable, Iterable, Optional

MIGRATED_PREFERRED_OVER = {"pumpfun"}
DEFAULT_RETRY_SECONDS = 120.0
DEFAULT_MAX_BATCHES = 1
BATCH_SIZE = 30


def _liquidity(pair: dict[str, Any]) -> Optional[float]:
    raw = (pair.get("liquidity") or {}).get("usd")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _price(pair: dict[str, Any]) -> float:
    try:
        return float(pair.get("priceUsd") or 0)
    except (TypeError, ValueError):
        return 0.0


def pick_best_pair(pairs: Iterable[dict[str, Any]], chain: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Paire à liquidité max ; `pumpfun` seulement à défaut de paire migrée."""
    usable = [
        p for p in pairs
        if p.get("pairAddress")
        and (chain is None or p.get("chainId") in (None, chain))
        and _price(p) > 0
    ]
    if not usable:
        return None
    with_liq = [p for p in usable if (_liquidity(p) or 0) > 0]
    migrated = [p for p in with_liq if (p.get("dexId") or "").lower() not in MIGRATED_PREFERRED_OVER]
    pool = migrated or with_liq
    if pool:
        return max(pool, key=lambda p: _liquidity(p) or 0)
    # Aucune liquidité lisible nulle part : mieux vaut une paire (prix
    # DexScreener) que rien, mais elle restera « stale » pour re-résolution.
    return usable[0]


class PairResolver:
    def __init__(
        self,
        get_tokens_data: Callable[[list[str]], dict[str, list[dict[str, Any]]]],
        retry_seconds: float = DEFAULT_RETRY_SECONDS,
        max_batches: int = DEFAULT_MAX_BATCHES,
        clock: Callable[[], float] = time.time,
    ):
        self._fetch = get_tokens_data
        self.retry_seconds = retry_seconds
        self.max_batches = max_batches
        self._clock = clock
        self._last_attempt: dict[str, float] = {}
        self._resolved: dict[str, Optional[str]] = {}
        self._stale: set[str] = set()
        self.calls = 0

    def mark_stale(self, token_address: str) -> None:
        """La paire suivie a perdu sa liquidité ou disparu : à re-résoudre."""
        self._stale.add(token_address)

    def needs(self, token_address: str, pair_address: Optional[str]) -> bool:
        return not pair_address or token_address in self._stale

    def _due(self, token_address: str, now: float) -> bool:
        last = self._last_attempt.get(token_address)
        return last is None or now - last >= self.retry_seconds

    def resolve(
        self, wanted: Iterable[tuple[str, Optional[str], Optional[str]]]
    ) -> dict[str, str]:
        """`wanted` : (token, chain, pair_actuelle). Retourne {token: nouvelle_paire}
        UNIQUEMENT pour les tokens dont la paire change. Ne lève jamais."""
        now = self._clock()
        todo: dict[str, Optional[str]] = {}
        current: dict[str, Optional[str]] = {}
        for token, chain, pair in wanted:
            if token in todo or not self.needs(token, pair):
                continue
            if not self._due(token, now):
                continue
            todo[token] = chain
            current[token] = pair
        if not todo:
            return {}
        tokens = list(todo)[: BATCH_SIZE * self.max_batches]
        for token in tokens:
            self._last_attempt[token] = now
        try:
            self.calls += 1
            data = self._fetch(tokens) or {}
        except Exception as exc:  # une résolution ratée ne casse jamais le tick
            print(f"[PairResolver] ⚠️ {type(exc).__name__} — nouvel essai dans {self.retry_seconds:.0f} s")
            return {}
        changes: dict[str, str] = {}
        for token in tokens:
            best = pick_best_pair(data.get(token) or [], todo[token])
            if best is None:
                continue
            self._resolved[token] = best["pairAddress"]
            if (_liquidity(best) or 0) > 0:
                self._stale.discard(token)
            if best["pairAddress"] != current.get(token):
                changes[token] = best["pairAddress"]
        return changes

    def resolved_pair(self, token_address: str) -> Optional[str]:
        return self._resolved.get(token_address)
