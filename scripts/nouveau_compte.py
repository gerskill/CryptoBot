#!/usr/bin/env python3
"""Ouvrir un nouveau compte paper pour CHAQUE bras, sans effacer l'historique.

Usage (sur le Mac, depuis la racine du dépôt) :
  python -m scripts.nouveau_compte --capital 100          # montre ce qui va changer, n'écrit rien
  python -m scripts.nouveau_compte --capital 100 --oui    # ouvre le compte

Ensuite, relancer le bot, le site et la publication pour qu'ils lisent le nouveau compte :
  ./scripts/installe_services.sh bot ui publie

Ce que ça fait : écrit `data/compte.json` (date de début + solde). Les journaux ne bougent pas ;
l'apprentissage continue de lire tout l'historique. Les positions encore ouvertes finissent leur
vie dans l'ancien compte. Voir src/core/compte.py.
"""

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.compte import compte_actuel, compte_path, ouvrir_compte


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--capital", type=float, required=True, help="solde de départ de CHAQUE bras, en $")
    parser.add_argument("--oui", action="store_true", help="ouvrir vraiment le compte")
    args = parser.parse_args(argv)
    if args.capital <= 0:
        print("Le solde doit être positif.")
        return 2
    actuel = compte_actuel()
    if actuel:
        debut = datetime.fromtimestamp(actuel.depuis, timezone.utc).isoformat(timespec="minutes")
        print(f"Compte actuel : n° {actuel.numero}, {actuel.capital:.2f} $ par bras depuis {debut}.")
    else:
        print("Aucun compte ouvert : le bot compte tout le journal sur capital_total de params.json.")
    print(f"Nouveau compte : {args.capital:.2f} $ par bras, à partir de maintenant (PAPER).")
    print("Les journaux sont conservés ; l'apprentissage garde tout l'historique.")
    if not args.oui:
        print("Rien n'est écrit. Ajoute --oui pour ouvrir le compte.")
        return 0
    compte = ouvrir_compte(args.capital)
    print(f"Compte n° {compte.numero} ouvert : {compte_path()}")
    print("Relance maintenant : ./scripts/installe_services.sh bot ui publie")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
