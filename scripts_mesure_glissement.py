#!/usr/bin/env python3
"""Mesure le glissement réel du stop loss depuis le journal.

Le glissement = écart entre le seuil qui a DÉCLENCHÉ la sortie et le P&L
réellement obtenu. Il vient de l'échantillonnage : le prix traverse le seuil
entre deux mesures.

Sert à régler `exit_rules.stop_loss_slippage_buffer_pct` sur une mesure au
lieu d'une intuition. À relancer quand l'échantillon grandit.

Usage : python3 scripts_mesure_glissement.py
"""

import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src import settings  # noqa: E402


def main() -> None:
    if not os.path.exists(settings.TRADES_LOG_PATH):
        print("Aucun journal de trades.")
        return

    rows = [
        json.loads(line)
        for line in open(settings.TRADES_LOG_PATH, encoding="utf-8")
        if line.strip()
    ]
    stops = [
        r
        for r in rows
        if r.get("is_final_exit")
        and str(r.get("exit_reason", "")).startswith(("STOP_LOSS", "BREAKEVEN_STOP"))
    ]
    if not stops:
        print("Aucune sortie par stop loss.")
        return

    print(f"{len(stops)} sorties par stop loss\n")

    # `stop_loss_trigger_pct` n'existe que depuis l'instrumentation : les
    # trades antérieurs retombent sur le seuil visé.
    ecarts = []
    for row in stops:
        trigger = row.get("stop_loss_trigger_pct")
        if trigger is None:
            trigger = row.get("stop_loss_target_pct", -25)
        ecarts.append(row["pnl_pct"] - trigger)

    print(f"  déclenchement médian : {statistics.median(e for e in ecarts):+.2f} points")
    print(f"  dépassement moyen    : {statistics.fmean(ecarts):+.2f} points")
    print(f"  pire cas             : {min(ecarts):+.2f} points")
    depasse = [e for e in ecarts if e < 0]
    print(f"  sorties sous le seuil : {len(depasse)}/{len(ecarts)}")

    if depasse:
        suggere = round(abs(statistics.fmean(depasse)), 1)
        print(f"\n  tampon suggéré : {suggere} points")
        print("  (à mettre dans exit_rules.stop_loss_slippage_buffer_pct)")
    else:
        print("\n  Aucun dépassement — le tampon peut rester à 0.")

    avec_instrum = [r for r in stops if r.get("stop_loss_trigger_pct") is not None]
    print(f"\n  {len(avec_instrum)}/{len(stops)} trades avec instrumentation complète")
    if len(avec_instrum) < 10:
        print("  Échantillon trop court : laisse tourner avant de régler.")


if __name__ == "__main__":
    main()
