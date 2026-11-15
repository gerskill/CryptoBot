#!/usr/bin/env python3
"""Sidecar de suivi apparié sans breakeven indépendant. PAPER, lecture seule.

Ne place aucun ordre, n'écrit pas les journaux des bras, ne charge pas .env.
Les prix des positions encore ouvertes viennent de data/state.json.
Après la sortie réelle, DexScreener public (paire d'entrée) est interrogé
au plus 4 fois par minute, et seulement pour les ombres encore ouvertes.

Usage sur la machine du bot, depuis la racine du dépôt, SANS redémarrer
la boucle :

  python3 scripts/shadow_nobe.py --once
  python3 scripts/shadow_nobe.py --interval 5
  python3 scripts/shadow_nobe.py --interval 5 --no-network

Arrêt : Ctrl-C. Les fichiers sont sous data/shadow_nobe/.
Les effacer revient au point de départ ; les bras ne sont pas touchés.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.shadow_nobe import (  # noqa: E402
    DEFAULT_ARMS,
    ShadowTracker,
    collect_bundle,
    select_pair_quote,
    tokens_needing_quotes,
    RateLimitedQuotes,
)

_RUNNING = True


def _stop(_signum, _frame) -> None:
    global _RUNNING
    _RUNNING = False


def _root_from(arg: str) -> str:
    return os.path.abspath(arg or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _dex_fetch(wanted: list[dict]):
    from src.apis.dexscreener import DexScreenerAPI

    api = DexScreenerAPI()
    by_token = {item["token_address"]: item for item in wanted}
    try:
        raw = api.get_tokens_data(list(by_token))
    except Exception:
        return {}
    now = time.time()
    out = {}
    for token, meta in by_token.items():
        out[token] = select_pair_quote(
            raw.get(token) or [],
            meta.get("pair_address"),
            meta.get("chain") or "solana",
            now,
        )
    return out


def run_once(tracker: ShadowTracker, fee_cache: dict, network: bool, feed: RateLimitedQuotes) -> dict:
    bundle, offsets, fee_cache_out = collect_bundle(
        tracker.root, tracker.arms, tracker.offsets, fee_cache
    )
    fee_cache.clear()
    fee_cache.update(fee_cache_out)
    if network:
        wanted = tokens_needing_quotes(tracker, bundle)
        quotes = feed.get([item["token_address"] for item in wanted]) if wanted else {}
        # Le fetch groupé ne connaît pas la paire : on refiltre si le fetch
        # a déjà renvoyé des quotes sélectionnées (tests) ou des paires brutes.
        if quotes and wanted and "matched" not in next(iter(quotes.values()), {}):
            now = tracker.clock()
            selected = {}
            for item in wanted:
                selected[item["token_address"]] = select_pair_quote(
                    quotes.get(item["token_address"]) or [],
                    item.get("pair_address"),
                    item.get("chain"),
                    now,
                )
            quotes = selected
        bundle["continuation_quotes"] = quotes
    return tracker.apply(bundle, offsets=offsets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Suivi shadow sans breakeven indépendant (PAPER)")
    parser.add_argument("--root", default="")
    parser.add_argument("--arms", default=",".join(DEFAULT_ARMS))
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-network", action="store_true")
    args = parser.parse_args(argv)
    root = _root_from(args.root)
    arms = tuple(name.strip() for name in args.arms.split(",") if name.strip())
    lock_path = os.path.join(root, "data", "shadow_nobe", "sidecar.pid")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    if os.path.exists(lock_path):
        try:
            with open(lock_path, encoding="utf-8") as handle:
                pid = int(handle.read().strip() or "0")
        except (OSError, ValueError):
            pid = 0
        if pid and _alive(pid):
            print(f"shadow_nobe déjà en cours (pid {pid}). Un second exemplaire doublerait le journal.")
            return 2
    with open(lock_path, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    tracker = ShadowTracker(root, arms=arms)
    fee_cache: dict = {}
    feed = RateLimitedQuotes(_dex_fetch)
    try:
        while _RUNNING:
            heart = run_once(tracker, fee_cache, network=not args.no_network, feed=feed)
            print(
                f"[shadow_nobe] ok={heart['ok']} ouvertes={heart['open_pairs']} "
                f"closes={heart['closed_pairs']} disque={heart['disk_bytes']}o"
            )
            if args.once or not _RUNNING:
                break
            deadline = time.monotonic() + max(1.0, args.interval)
            while _RUNNING and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))
    finally:
        try:
            with open(lock_path, encoding="utf-8") as handle:
                owner = handle.read().strip()
            if owner == str(os.getpid()):
                os.remove(lock_path)
        except OSError:
            pass
    return 0 if tracker.last_error is None else 0


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())
