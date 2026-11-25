#!/usr/bin/env python3
"""Corrige les paris de prédiction « comptés perdus par prudence » dont l'issue
a été vérifiée depuis. Par défaut : simulation (rien n'est écrit).

  python3 scripts/corrige_paris_prudence.py                       # voir l'effet
  python3 scripts/corrige_paris_prudence.py --appliquer           # écrire (service prediction ARRÊTÉ)
  python3 scripts/corrige_paris_prudence.py --inclure-probables   # + issues probables

Une copie `bets.jsonl.avant-correction-<date>` est gardée pour chaque bras modifié.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from src.prediction.runner import load_config  # noqa: E402
from src.prediction.saisons import apply_corrections  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=settings.PREDICTION_DATA_DIR)
    parser.add_argument("--fichier", default=os.path.join(HERE, "corrections_prudence_2026-11-22.json"))
    parser.add_argument("--inclure-probables", action="store_true")
    parser.add_argument("--appliquer", action="store_true")
    args = parser.parse_args()
    spec = json.load(open(args.fichier, encoding="utf-8"))
    fixes = list(spec.get("certaines", [])) + (list(spec.get("probables", [])) if args.inclure_probables else [])
    config = load_config(settings.PREDICTION_ARMS_PATH)
    report = apply_corrections(args.data_dir, fixes, config["frais_pct"], config["frais_reglement_pct"],
                               apply=args.appliquer)
    total = 0.0
    for row in report:
        if row.get("introuvable"):
            print(f"INTROUVABLE  {row['id']}")
            continue
        total += row["effet"]
        print(f"{row['bras']:<18} {row['titre'][:50]:<50} {row['cote']:<4} prix {row['prix']:<8} "
              f"{row['pnl_avant']:+8.2f} -> {row['pnl_apres']:+8.2f}  (effet {row['effet']:+.2f})")
    print(f"Effet total sur le réalisé : {total:+.2f} $ — {'ÉCRIT' if args.appliquer else 'simulation, rien écrit'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
