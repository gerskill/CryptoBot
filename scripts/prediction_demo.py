#!/usr/bin/env python3
"""Fabrique un jeu de données « EXEMPLE FICTIF » pour essayer l'onglet Liquid.

Tout ce que ce script écrit est INVENTÉ (tirage pseudo-aléatoire à graine
fixe) et marqué `exemple_fictif: true`. Il écrit dans `data/prediction_demo/`,
jamais dans `data/prediction/` : un exemple ne peut pas se mélanger au réel.
Le dashboard n'affiche ces données que si on clique « Voir l'exemple fictif »,
sous un bandeau permanent.

Usage : python3 scripts/prediction_demo.py
"""

import os
import random
import shutil
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from src.prediction.paper import BetStore, _write_atomic, append_jsonl, net_pnl  # noqa: E402
from src.prediction.runner import load_config  # noqa: E402

START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def main() -> int:
    rng = random.Random(20261116)
    config = load_config(settings.PREDICTION_ARMS_PATH)
    out = settings.PREDICTION_DEMO_DIR
    if os.path.isdir(out):
        shutil.rmtree(out)
    stake = config["capital_fictif_usd"] * config["mise_pct"]
    plan = {"pm_temoin": 36, "pm_vol_edge": 31, "pm_vol_normal": 18, "pm_consensus": 9}
    outcome = 9000
    for arm, count in plan.items():
        store = BetStore(out, arm)
        for index in range(count):
            outcome += 1
            entry = START + timedelta(hours=7 * index + rng.random() * 3)
            minutes = rng.choice([15, 15, 30, 60])
            expiry = entry + timedelta(minutes=minutes)
            price = round(rng.uniform(0.55, 0.9) if arm == "pm_temoin" else rng.uniform(0.3, 0.75), 4)
            prob = None if arm == "pm_temoin" else round(min(0.97, price + rng.uniform(0.06, 0.12)), 4)
            won = rng.random() < (price if prob is None else (price + prob) / 2)
            value = 1.0 if won else 0.0
            shares = stake / price
            coin = f"#{outcome * 10}"
            target = 60000 + rng.randrange(0, 8000, 250)
            bet = {
                "id": f"{arm}-{outcome * 10}-{int(entry.timestamp())}",
                "arm": arm, "outcome": outcome, "coin": coin, "side_index": 0, "side_name": "Yes",
                "title": f"BTC > {target} ({expiry.strftime('%Y-%m-%d %H:%M UTC')})",
                "domain": "crypto_seuil", "klass": "priceBinary", "underlying": "BTC",
                "target": target, "expiry": iso(expiry), "entry_time": iso(entry),
                "entry_price": price, "shares": round(shares, 6), "stake_usd": stake,
                "fee_entry_usd": round(stake * config["frais_pct"], 6), "spread_at_entry": 0.01,
                "prob_estimee": prob,
                "source_estimation": "favori du marché" if prob is None else "exemple fictif",
                "status": "regle", "settle_time": iso(expiry), "settle_value": value,
                "pnl_net": round(net_pnl(stake, shares, value, config["frais_pct"],
                                         config["frais_reglement_pct"]), 6),
                "won": won, "exemple_fictif": True,
            }
            append_jsonl(store.settled_path, bet)
            # Série de prix : démarre EXACTEMENT au prix et à l'heure d'entrée,
            # finit EXACTEMENT à la valeur et à l'heure de règlement.
            path = os.path.join(out, "prices", f"{outcome * 10}.jsonl")
            append_jsonl(path, {"t": iso(entry), "mid": price, "exemple_fictif": True})
            level = price
            for step in range(1, minutes):
                drift = (value - level) / (minutes - step + 1)
                level = min(0.99, max(0.01, level + drift + rng.uniform(-0.03, 0.03)))
                append_jsonl(path, {"t": iso(entry + timedelta(minutes=step)),
                                    "mid": round(level, 4), "exemple_fictif": True})
            append_jsonl(path, {"t": iso(expiry), "mid": value, "exemple_fictif": True})
    _write_atomic(os.path.join(out, "state.json"), {
        "updated_at": START.timestamp(), "updated_at_iso": iso(START), "mode": "PAPER",
        "marches_par_domaine": {"crypto_seuil": 12}, "exemple_fictif": True,
    })
    print(f"Exemple fictif écrit dans {out} ({sum(plan.values())} paris inventés).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
