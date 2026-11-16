#!/usr/bin/env python3
"""Simulation PAPIER des marchés de prédiction Liquid (Hyperliquid HIP-4).

Processus séparé de la boucle memecoin, comme `scripts/gmgn_observe.py`
(ADR 014) : il lit l'API publique d'Hyperliquid, sans clé, et n'écrit que
dans `data/prediction/`. Il ne peut placer aucun pari réel : le code ne
contient aucun appel d'exécution.

Usage :
  python3 scripts/run_prediction.py --sonde     # vérifie la forme des réponses de l'API
  python3 scripts/run_prediction.py --once      # un seul cycle
  python3 scripts/run_prediction.py             # boucle, un cycle par minute
"""

import argparse
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from src.prediction.hl_api import HyperliquidInfo  # noqa: E402
from src.prediction.markets import parse_outcome_meta  # noqa: E402
from src.prediction.runner import PredictionRunner, load_config  # noqa: E402


def sonde(api: HyperliquidInfo) -> int:
    """Premier lancement : montrer ce que l'API renvoie VRAIMENT.

    Le code a été écrit d'après la documentation publique. Cette sonde
    enregistre la réponse brute pour qu'un écart de format se voie tout de
    suite au lieu de produire un journal vide sans explication.
    """
    meta = api.outcome_meta()
    if meta is None:
        print("[sonde] outcomeMeta : AUCUNE réponse. Vérifier la connexion.")
        return 1
    os.makedirs(settings.PREDICTION_DATA_DIR, exist_ok=True)
    raw_path = os.path.join(settings.PREDICTION_DATA_DIR, "sonde_outcomeMeta.json")
    with open(raw_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    markets = parse_outcome_meta(meta)
    mids = api.all_mids()
    by_domain: dict[str, int] = {}
    classes: dict[str, int] = {}
    for market in markets:
        by_domain[market.domain] = by_domain.get(market.domain, 0) + 1
        classes[str(market.klass)] = classes.get(str(market.klass), 0) + 1
    priced = sum(1 for m in markets if m.sides and m.sides[0].coin in mids)
    print(f"[sonde] réponse brute enregistrée : {raw_path}")
    print(f"[sonde] marchés lus : {len(markets)} | avec un prix : {priced}")
    print(f"[sonde] par domaine : {by_domain}")
    print(f"[sonde] classes rencontrées : {classes}")
    if markets:
        sample = next((m for m in markets if m.sides[0].coin in mids), markets[0])
        book = api.l2_book(sample.sides[0].coin)
        print(f"[sonde] exemple : {sample.title} | pièce {sample.sides[0].coin} | "
              f"carnet {'lu' if book else 'ILLISIBLE'}")
    if not markets or not priced:
        print("[sonde] ATTENTION : rien d'exploitable. Le format a peut-être changé ; "
              "ouvrir le fichier brut avant de lancer la boucle.")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="un seul cycle")
    parser.add_argument("--sonde", action="store_true", help="vérifier le format de l'API")
    parser.add_argument("--interval", type=int, default=60, help="secondes entre deux cycles")
    args = parser.parse_args()

    api = HyperliquidInfo()
    if args.sonde:
        return sonde(api)

    runner = PredictionRunner(api, load_config(settings.PREDICTION_ARMS_PATH),
                              settings.PREDICTION_DATA_DIR)
    print("[prediction] mode PAPER — aucun pari réel n'est possible depuis ce script")
    while True:
        try:
            result = runner.cycle()
            if result.get("ok"):
                print(f"[prediction] {result['updated_at_iso']} ouverts {result['ouverts_ce_cycle']} "
                      f"réglés {result['regles_ce_cycle']} incohérences {result['incoherences_ce_cycle']}")
            else:
                print(f"[prediction] cycle sauté : {result.get('raison')}")
        except Exception:  # la boucle ne meurt jamais
            traceback.print_exc()
        if args.once:
            return 0
        time.sleep(max(15, args.interval))


if __name__ == "__main__":
    sys.exit(main())
