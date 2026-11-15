#!/usr/bin/env python3
"""Taux de remplissage des champs enrichis du journal d'entrée.

Lecture seule. Parcourt data/arms/*/trades_log.jsonl (lignes de sortie
finale) et, avec --open, les positions ouvertes (entry_context). Affiche,
pour les N dernières positions, la part de valeurs non nulles par champ
préfixé (entry_, alpha_comp_, score_v2_, onchain_, social_).

    python3 scripts/feature_coverage.py            # 50 dernières, tous bras
    python3 scripts/feature_coverage.py -n 20 --open --prefix onchain_
    python3 scripts/feature_coverage.py --since "2026-10-10 16:00"
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import Counter, defaultdict
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIXES = ("entry_", "alpha_comp_", "score_v2_", "onchain_", "social_")


def _ts(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _filled(value) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def load_rows(include_open: bool) -> list[dict]:
    rows = []
    for path in glob.glob(os.path.join(BASE, "data", "arms", "*", "trades_log.jsonl")):
        arm = os.path.basename(os.path.dirname(path))
        with open(path) as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("is_final_exit") is False:
                    continue
                row["_arm"], row["_src"] = arm, "closed"
                row["_t"] = _ts(row.get("timestamp_entry"))
                rows.append(row)
    if include_open:
        for path in glob.glob(os.path.join(BASE, "data", "arms", "*", "open_positions.json")):
            arm = os.path.basename(os.path.dirname(path))
            try:
                positions = json.load(open(path))
            except (OSError, ValueError):
                continue
            for pos in positions if isinstance(positions, list) else []:
                row = dict(pos.get("entry_context") or {})
                row["_arm"], row["_src"] = arm, "open"
                row["_t"] = _ts(pos.get("entry_time"))
                rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-n", type=int, default=50, help="N dernières positions")
    parser.add_argument("--open", action="store_true", help="inclure les positions ouvertes")
    parser.add_argument("--prefix", action="append", help="limiter à ce(s) préfixe(s)")
    parser.add_argument("--since", help="entrées après cette date locale (YYYY-MM-DD HH:MM)")
    parser.add_argument("--arm", action="append", help="limiter à ce(s) bras")
    args = parser.parse_args()
    prefixes = tuple(args.prefix) if args.prefix else PREFIXES

    rows = load_rows(args.open)
    if args.arm:
        rows = [r for r in rows if r["_arm"] in args.arm]
    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d %H:%M").timestamp()
        rows = [r for r in rows if r["_t"] >= since]
    rows = sorted(rows, key=lambda r: r["_t"])[-args.n:]
    if not rows:
        print("Aucune position.")
        return
    fields = sorted({k for r in rows for k in r if k.startswith(prefixes)})
    srcs = Counter(r["_src"] for r in rows)
    print(f"{len(rows)} positions ({dict(srcs)}) ; bras : {dict(Counter(r['_arm'] for r in rows))}")
    statuses = Counter(r.get("onchain_status") for r in rows)
    if any(statuses):
        print(f"onchain_status : {dict(statuses)}")
    by_prefix = defaultdict(list)
    for field in fields:
        n = sum(_filled(r.get(field)) for r in rows)
        by_prefix[next(p for p in prefixes if field.startswith(p))].append((field, n))
    for prefix in prefixes:
        items = by_prefix.get(prefix)
        if not items:
            print(f"\n[{prefix}*] aucun champ présent")
            continue
        mean = sum(n for _, n in items) / (len(items) * len(rows))
        print(f"\n[{prefix}*] {len(items)} champs, remplissage moyen {mean:.0%}")
        for field, n in items:
            print(f"  {field:<42} {n:>4}/{len(rows):<4} {n / len(rows):>5.0%}")


if __name__ == "__main__":
    main()
