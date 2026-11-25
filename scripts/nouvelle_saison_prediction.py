#!/usr/bin/env python3
"""Archive la saison des bras de prédiction et ouvre une saison neuve.

  python3 scripts/nouvelle_saison_prediction.py                 # voir ce qui serait fait
  python3 scripts/nouvelle_saison_prediction.py --appliquer     # faire (service prediction ARRÊTÉ)

Rien n'est supprimé : data/prediction/arms et learning partent dans
data/prediction/archive/<nom>/. Le nom doit être celui de `saisons_a_terminer`
dans config/prediction_arms.json, pour que les paris encore ouverts finissent.
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from src.prediction.paper import read_json  # noqa: E402
from src.prediction.saisons import archive_season  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=settings.PREDICTION_DATA_DIR)
    parser.add_argument("--nom", default="saison2_46usd")
    parser.add_argument("--capital-archive", type=float, default=46.0)
    parser.add_argument("--appliquer", action="store_true")
    parser.add_argument("--force", action="store_true", help="ne pas vérifier que le service est arrêté")
    args = parser.parse_args()
    state = read_json(os.path.join(args.data_dir, "state.json"), {}) or {}
    if args.appliquer and not args.force and time.time() - float(state.get("updated_at", 0)) < 300:
        print("Le service prediction a écrit il y a moins de 5 min : arrête-le d'abord "
              "(launchctl unload ~/Library/LaunchAgents/com.cryptobbot.prediction.plist).")
        return 2
    result = archive_season(args.data_dir, args.nom, args.capital_archive, apply=args.appliquer)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
