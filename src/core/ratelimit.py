"""Rate limiting thread-safe (fenêtre glissante).

Un limiteur par famille d'endpoints : un sleep fixe global gaspille le quota
des endpoints les plus généreux.
"""

import threading
import time
from collections import deque

SAFETY_MARGIN = 0.9  # on n'utilise que 90% du quota annoncé


class RateLimiter:
    """Fenêtre glissante : au plus `max_calls` appels par `period` secondes."""

    def __init__(self, max_calls: int, period: float = 60.0, name: str = ""):
        if max_calls <= 0:
            raise ValueError("max_calls doit être > 0")
        self.max_calls = max(1, int(max_calls * SAFETY_MARGIN))
        self.period = period
        self.name = name
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, timeout: float | None = None) -> bool:
        """Bloque jusqu'à obtention d'un slot. False si timeout dépassé."""
        deadline = None if timeout is None else time.monotonic() + timeout

        while True:
            with self._lock:
                now = time.monotonic()
                self._evict(now)
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return True
                wait_for = self.period - (now - self._calls[0])

            if deadline is not None and time.monotonic() + wait_for > deadline:
                return False
            time.sleep(min(max(wait_for, 0.01), 1.0))

    def _evict(self, now: float) -> None:
        while self._calls and now - self._calls[0] >= self.period:
            self._calls.popleft()

    @property
    def available(self) -> int:
        with self._lock:
            self._evict(time.monotonic())
            return self.max_calls - len(self._calls)

    def __enter__(self) -> "RateLimiter":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        return None
