"""Cache des tokens déjà scannés + watchlist + blacklist persistante."""

import json
import os
import tempfile
import threading
import time
from typing import Any, Optional

DEFAULT_TTL_MINUTES = 30
HONEYPOT_BLACKLIST_HOURS = 48
RUGPULL_BLACKLIST_HOURS = 24 * 7
MAX_WATCH_HOURS = 6  # au-delà, le token sort de la fenêtre d'âge de toute façon


class TokenCache:
    """Cache disque thread-safe : tokens vus, surveillés, blacklistés."""

    def __init__(self, path: str, ttl_minutes: int = DEFAULT_TTL_MINUTES):
        self.path = path
        self.ttl_seconds = ttl_minutes * 60
        self._lock = threading.RLock()
        self._seen: dict[str, float] = {}
        self._blacklist: dict[str, dict[str, Any]] = {}
        self._watched: dict[str, float] = {}
        self._load()

    # ------------------------------------------------------------- watchlist

    def watch(self, token_address: str) -> None:
        """Place un token sous surveillance active.

        Un token surveillé est ré-interrogé À CHAQUE cycle en ignorant le TTL :
        sans ça, l'historique de prix ne se remplit jamais et l'analyse
        technique reste bloquée en `pending`.
        """
        with self._lock:
            self._watched.setdefault(token_address, time.time())

    def unwatch(self, token_address: str) -> None:
        with self._lock:
            self._watched.pop(token_address, None)

    def is_watched(self, token_address: str) -> bool:
        with self._lock:
            return token_address in self._watched

    def watched(self) -> list[str]:
        with self._lock:
            return list(self._watched)

    def watched_since(self, token_address: str) -> Optional[float]:
        with self._lock:
            started = self._watched.get(token_address)
            return None if started is None else time.time() - started

    # ------------------------------------------------------------------ seen

    def is_fresh(self, token_address: str) -> bool:
        """True si scanné il y a moins de TTL -> à sauter.

        Toujours False pour un token sous surveillance (voir `watch`).
        """
        with self._lock:
            if token_address in self._watched:
                return False
            seen_at = self._seen.get(token_address)
            if seen_at is None:
                return False
            if time.time() - seen_at >= self.ttl_seconds:
                del self._seen[token_address]
                return False
            return True

    def mark_seen(self, token_address: str) -> None:
        with self._lock:
            self._seen[token_address] = time.time()

    def age_seconds(self, token_address: str) -> Optional[float]:
        with self._lock:
            seen_at = self._seen.get(token_address)
            return None if seen_at is None else time.time() - seen_at

    # ------------------------------------------------------------- blacklist

    def is_blacklisted(self, token_address: str) -> bool:
        with self._lock:
            entry = self._blacklist.get(token_address)
            if entry is None:
                return False
            if time.time() >= entry["expires_at"]:
                del self._blacklist[token_address]
                return False
            return True

    def blacklist_reason(self, token_address: str) -> Optional[str]:
        with self._lock:
            entry = self._blacklist.get(token_address)
            return entry.get("reason") if entry else None

    def blacklist(self, token_address: str, reason: str, hours: float) -> None:
        with self._lock:
            self._blacklist[token_address] = {
                "reason": reason,
                "expires_at": time.time() + hours * 3600,
                "created_at": time.time(),
            }
            self._watched.pop(token_address, None)
        self.save()

    # ------------------------------------------------------------ persistance

    def purge(self) -> int:
        """Supprime les entrées expirées. Retourne le nombre supprimé."""
        now = time.time()
        with self._lock:
            stale_seen = [k for k, v in self._seen.items() if now - v >= self.ttl_seconds]
            stale_bl = [k for k, v in self._blacklist.items() if now >= v["expires_at"]]
            stale_watch = [
                k for k, v in self._watched.items() if now - v >= MAX_WATCH_HOURS * 3600
            ]
            for k in stale_seen:
                del self._seen[k]
            for k in stale_bl:
                del self._blacklist[k]
            for k in stale_watch:
                del self._watched[k]
        return len(stale_seen) + len(stale_bl) + len(stale_watch)

    def save(self) -> None:
        """Écriture atomique (tmp + rename) pour ne pas corrompre le cache."""
        with self._lock:
            payload = {
                "seen": self._seen,
                "blacklist": self._blacklist,
                "watched": self._watched,
            }
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp_path, self.path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            self._seen = {k: float(v) for k, v in data.get("seen", {}).items()}
            self._blacklist = dict(data.get("blacklist", {}))
            self._watched = {k: float(v) for k, v in data.get("watched", {}).items()}
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            print(f"[Cache] Fichier illisible ({exc}) -> repart à vide")
            self._seen, self._blacklist, self._watched = {}, {}, {}
        self.purge()

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "seen": len(self._seen),
                "blacklisted": len(self._blacklist),
                "watched": len(self._watched),
            }
