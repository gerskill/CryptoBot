#!/usr/bin/env python3
"""Vérifie que les CLI externes correspondent aux versions épinglées.

POURQUOI. Le bot exécute `gmgn-cli` en sous-processus. Dans sa version
complète ce binaire peut signer des transactions ; le bot ne l'appelle que sur
une liste blanche de lecture, vérifiée AVANT le sous-processus. Mais la liste
blanche protège contre un mauvais usage de MA part, pas contre une version
compromise qui détournerait une commande de lecture.

`npm i -g gmgn-cli` sans version installe silencieusement la dernière. Ce
script rend la dérive visible.

Usage : python3 scripts_verifie_outils.py
"""

import json
import os
import subprocess
import sys

LOCK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools.lock.json")


def installed_version(package: str) -> str | None:
    try:
        result = subprocess.run(
            ["npm", "ls", "-g", package, "--depth=0", "--json"],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(result.stdout or "{}")
        entry = (data.get("dependencies") or {}).get(package) or {}
        return entry.get("version")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None


def main() -> None:
    with open(LOCK, encoding="utf-8") as fh:
        lock = json.load(fh)

    print(f"\n{'outil':>16} {'épinglé':>10} {'installé':>10}  état")
    print("─" * 58)
    derive = False
    for package, spec in lock["tools"].items():
        attendu = spec["version"]
        trouve = installed_version(package)
        if trouve is None:
            etat, derive = "ABSENT", True
        elif trouve == attendu:
            etat = "ok"
        else:
            etat, derive = "DÉRIVE", True
        print(f"{package:>16} {attendu:>10} {str(trouve or '—'):>10}  {etat}")

    if derive:
        print("\n⚠️  Version non conforme. Ces binaires peuvent signer des")
        print("    transactions. Réinstaller la version épinglée :")
        for package, spec in lock["tools"].items():
            print(f"      {spec['install']}")
        sys.exit(1)
    print("\nToutes les versions correspondent.")


if __name__ == "__main__":
    main()
