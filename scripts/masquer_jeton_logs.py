#!/usr/bin/env python3
"""Nettoie le jeton Telegram déjà écrit dans les journaux (OPTIONNEL, séparé du bot).

Sans `--oui` : compte seulement (rien n'est modifié). Avec `--oui` :
  1. copie chaque fichier concerné dans backups/logs-avant-masquage-<date>/ (droits 600) ;
  2. remplace le jeton par un masque DE MÊME LONGUEUR, sur place : le fichier garde sa taille
     et son identité, donc le bot peut continuer à écrire dans logs/bot.log pendant ce temps ;
  3. recompte : il doit rester 0 occurrence.
Le jeton n'est JAMAIS affiché : seuls les nombres d'occurrences le sont.

ATTENTION : la sauvegarde contient encore le jeton. La supprimer une fois le résultat vérifié
(et de toute façon après avoir créé un nouveau jeton chez BotFather, qui rend l'ancien inutile).

Usage : python3 scripts/masquer_jeton_logs.py [--oui] [--projet ~/Projects/CryptobBot]
"""

from __future__ import annotations

import argparse
import glob
import gzip
import os
import re
import shutil
import sys
from datetime import datetime

MOTIF = re.compile(rb"bot\d{5,15}:[A-Za-z0-9_-]{30,}")
MASQUE = b"bot<jeton-masque>"

MOTIFS_FICHIERS = (
    "logs/*.log", "logs/*.out", "logs/*.log.*", "logs/archive/*",
    "data/*.log", "data/prediction/*.log", "data/shadow_nobe/*.log",
)


def masque_meme_longueur(longueur: int) -> bytes:
    return (MASQUE + b"*" * longueur)[:longueur]


def fichiers(projet: str) -> list[str]:
    vus: list[str] = []
    for motif in MOTIFS_FICHIERS:
        for chemin in sorted(glob.glob(os.path.join(projet, motif))):
            if os.path.isfile(chemin) and chemin not in vus:
                vus.append(chemin)
    return vus


def _lire(chemin: str) -> bytes:
    if chemin.endswith(".gz"):
        with gzip.open(chemin, "rb") as fh:
            return fh.read()
    with open(chemin, "rb") as fh:
        return fh.read()


def compter(chemin: str) -> int:
    try:
        return len(MOTIF.findall(_lire(chemin)))
    except (OSError, EOFError, gzip.BadGzipFile):
        return 0


def nettoyer(chemin: str) -> int:
    """Masque sur place. Retourne le nombre de remplacements."""
    if chemin.endswith(".gz"):
        contenu = _lire(chemin)
        nouveau, n = MOTIF.subn(lambda m: masque_meme_longueur(len(m.group(0))), contenu)
        if n:
            tmp = chemin + ".tmp-masquage"
            with gzip.open(tmp, "wb") as fh:
                fh.write(nouveau)
            shutil.copystat(chemin, tmp)
            os.replace(tmp, chemin)
        return n
    # Fichier texte en cours d'écriture possible : réécriture octet par octet aux positions
    # trouvées, sans changer la taille ni remplacer le fichier.
    with open(chemin, "rb") as fh:
        contenu = fh.read()
    positions = [(m.start(), len(m.group(0))) for m in MOTIF.finditer(contenu)]
    if positions:
        with open(chemin, "r+b") as fh:
            for debut, longueur in positions:
                fh.seek(debut)
                fh.write(masque_meme_longueur(longueur))
            fh.flush()
            os.fsync(fh.fileno())
    return len(positions)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--oui", action="store_true", help="modifier vraiment (sinon : compte seulement)")
    parser.add_argument("--projet", default=os.path.expanduser("~/Projects/CryptobBot"))
    args = parser.parse_args(argv)
    projet = os.path.abspath(args.projet)

    concernes = [(c, compter(c)) for c in fichiers(projet)]
    concernes = [(c, n) for c, n in concernes if n]
    if not concernes:
        print("Aucun jeton trouvé dans les journaux. Rien à faire.")
        return 0
    for chemin, n in concernes:
        print(f"  {os.path.relpath(chemin, projet)} : {n} occurrence(s)")
    if not args.oui:
        print("Mode vérification : rien n'a été modifié. Relancer avec --oui pour masquer.")
        return 0

    sauvegarde = os.path.join(
        projet, "backups", f"logs-avant-masquage-{datetime.now():%Y%m%d-%H%M%S}"
    )
    os.makedirs(sauvegarde, mode=0o700, exist_ok=True)
    os.chmod(sauvegarde, 0o700)
    for chemin, _ in concernes:
        cible = os.path.join(sauvegarde, os.path.relpath(chemin, projet))
        os.makedirs(os.path.dirname(cible), mode=0o700, exist_ok=True)
        shutil.copy2(chemin, cible)
        os.chmod(cible, 0o600)
    print(f"Sauvegarde (contient encore le jeton, à supprimer après vérification) : "
          f"{os.path.relpath(sauvegarde, projet)}")

    restant = 0
    for chemin, _ in concernes:
        n = nettoyer(chemin)
        reste = compter(chemin)
        restant += reste
        print(f"  {os.path.relpath(chemin, projet)} : {n} masqué(s), {reste} restant(s)")
    if restant:
        print("⚠️ Il reste des occurrences (le bot a peut-être écrit entre-temps) : relancer.")
        return 1
    print("Terminé : plus aucun jeton en clair dans les journaux.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
