"""Surveillance des positions ouvertes, indépendante du scan.

Un seul fil. Il n'achète ni ne vend : il appelle le tick déjà en place
(`AlphaLoop._monitor_positions`). Si un tick est encore en cours, le suivant
est sauté. L'attente est un `Event.wait` : une erreur répétée (429, délai
dépassé) ne tourne pas à vide.

Les prix se lisent hors du verrou des livres. Un prix absent, non fini ou
inférieur ou égal à zéro n'est jamais remplacé par le dernier prix connu.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable, Optional


def quote_is_usable(price: Optional[float]) -> bool:
    """Prix utilisable pour une sortie.

    None, NaN, infini et une valeur <= 0 ne le sont pas. Le dernier prix
    affiché au tableau de bord n'est pas un substitut.
    """
    if price is None or isinstance(price, bool):
        return False
    try:
        value = float(price)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and value > 0


def fetch_unique(items, key_of, fetch):
    """Un appel `fetch` par clé. Plusieurs positions d'une même paire partagent le prix."""
    found = {}
    for item in items:
        key = key_of(item)
        if key in found:
            continue
        found[key] = fetch(item)
    return found


def sample_gap_line(snapshots) -> Optional[str]:
    """Une ligne : secondes depuis l'échantillon précédent, ratio de prix, ratio de liquidité.

    `None` s'il n'y a pas encore deux échantillons. Ne dit pas qu'un passage
    plus fréquent aurait évité la perte : il mesure seulement l'écart réel.
    """
    if snapshots is None or len(snapshots) < 2:
        return None
    prev, last = snapshots[-2], snapshots[-1]
    gap = float(last.ts) - float(prev.ts)
    if prev.price:
        prix = f"prix x{last.price / prev.price:.3f}"
    else:
        prix = "prix ?"
    if prev.liquidity and last.liquidity:
        liq = f"liq x{last.liquidity / prev.liquidity:.3f}"
    else:
        liq = "liq inconnue"
    return f"écart {gap:.0f}s | {prix} | {liq}"


class PositionWatch:
    """Cadence le tick de surveillance. Un fil, pas de boucle serrée."""

    def __init__(
        self,
        tick: Callable[[], None],
        interval_seconds: Callable[[], float],
    ) -> None:
        self._tick = tick
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._busy = False
        self._guard = threading.Lock()
        self.ticks = 0
        self.skipped_overlap = 0
        self.errors = 0
        self.last_duration_sec = 0.0
        self.last_interval_sec = 0.0

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.alive:
            return
        self._stop.clear()
        thread = threading.Thread(target=self._loop, name="position-watch", daemon=True)
        self._thread = thread
        thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                interval = float(self._interval_seconds() or 0)
            except Exception:  # noqa: BLE001
                interval = 5.0
                self.errors += 1
            if interval < 0.05:
                interval = 0.05
            self.last_interval_sec = interval

            with self._guard:
                busy = self._busy
            if busy:
                self.skipped_overlap += 1
                wait = interval
            else:
                started = time.monotonic()
                try:
                    with self._guard:
                        self._busy = True
                    self._tick()
                    self.ticks += 1
                except Exception:  # noqa: BLE001
                    self.errors += 1
                finally:
                    with self._guard:
                        self._busy = False
                    self.last_duration_sec = time.monotonic() - started
                # Un tick plus long que la cadence attend le reliquat, au
                # moins 50 ms : on ne relance pas immédiatement, et une
                # erreur qui revient tout de suite reste espacée.
                wait = max(0.05, interval - self.last_duration_sec)
            if self._stop.wait(wait):
                return


def watch_snapshot(watch: PositionWatch, metrics: dict[str, Any]) -> dict[str, Any]:
    """Chiffres publiables. Aucun secret, aucun prix, aucune adresse."""
    last_quote = metrics.get("last_quote_at")
    age = None
    if isinstance(last_quote, (int, float)):
        age = round(max(0.0, time.time() - float(last_quote)), 1)
    return {
        "alive": watch.alive,
        "ticks": watch.ticks,
        "skipped_overlap": watch.skipped_overlap,
        "errors": watch.errors,
        "interval_sec": watch.last_interval_sec,
        "last_tick_sec": metrics.get("last_tick_sec"),
        "quotes_ok": metrics.get("quotes_ok", 0),
        "quotes_missing": metrics.get("quotes_missing", 0),
        "last_quote_age_sec": age,
        "last_scan_sec": metrics.get("last_scan_sec"),
    }
