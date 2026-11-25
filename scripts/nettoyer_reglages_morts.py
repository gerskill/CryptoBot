"""Retire des configs les réglages que AUCUN code ne lit (2026-11-23).

Ces clés (``entry_rules.entry_timing``, ``filters.max_tax_buy``…) sont listées
dans ``src.core.params.PARAMETRES_NON_APPLIQUES`` : elles laissent croire qu'un
bras applique une règle alors qu'il ne l'applique pas.

Par défaut : simple LISTE, rien n'est écrit.
``--appliquer`` : retire les clés, MAIS refuse si le bot tourne — le bot
réécrit ses fichiers de réglages en entier et effacerait le nettoyage.
À lancer seulement bot arrêté (pendant un redémarrage approuvé).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from src.core.params import PARAMETRES_NON_APPLIQUES  # noqa: E402


def fichiers(base: str = BASE) -> list[str]:
    out = [os.path.join(base, "config", "params.json")]
    out += sorted(glob.glob(os.path.join(base, "config", "arms", "*.json")))
    return [p for p in out if os.path.isfile(p)]


def cles_mortes(doc: dict) -> list[str]:
    trouvees = []
    for chemin in PARAMETRES_NON_APPLIQUES:
        section, cle = chemin.split(".", 1)
        if isinstance(doc.get(section), dict) and cle in doc[section]:
            trouvees.append(chemin)
    return trouvees


def retirer(doc: dict) -> list[str]:
    retirees = cles_mortes(doc)
    for chemin in retirees:
        section, cle = chemin.split(".", 1)
        doc[section].pop(cle, None)
    return retirees


def bot_en_marche(base: str = BASE) -> bool:
    pid_path = os.path.join(base, "data", "alpha_loop.pid")
    try:
        pid = int(open(pid_path).read().strip() or 0)
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _ecrire(path: str, doc: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def main(argv: list[str] | None = None, base: str = BASE) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--appliquer", action="store_true")
    args = ap.parse_args(argv)
    if args.appliquer and bot_en_marche(base):
        print("REFUS : le bot tourne. Arrête-le d'abord (sinon il réécrit ses fichiers).")
        return 2
    total = 0
    for path in fichiers(base):
        with open(path) as f:
            doc = json.load(f)
        mortes = retirer(doc) if args.appliquer else cles_mortes(doc)
        if not mortes:
            continue
        total += len(mortes)
        print(f"{os.path.relpath(path, base)} : {', '.join(mortes)}")
        if args.appliquer:
            _ecrire(path, doc)
    verbe = "retirée(s)" if args.appliquer else "à retirer (rien écrit, ajoute --appliquer)"
    print(f"{total} clé(s) non lue(s) {verbe}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
