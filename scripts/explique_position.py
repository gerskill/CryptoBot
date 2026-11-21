#!/usr/bin/env python3
"""Expliquer une position : pourquoi est-elle sortie là, et un pic était-il « raté » ?

QUESTION D'ORIGINE (2026-11-21). OGTA (position 3d7a4da1aa80) a touché +105,65 % à la 31ᵉ minute
puis est sortie à +3,85 % en BREAKEVEN_STOP, sans aucune prise de profit. Bug (le TP1 franchi n'a
pas joué) ou règle (TP1 placé plus loin) ? Le journal ne gardait pas le TP1 de la position : ce
script répond avec ce qui existe, et dit d'où vient chaque règle.

CE QU'IL FAIT, en lecture seule :
  1. retrouve la position (id complet, début d'id ou symbole) dans les journaux de TOUS les bras,
     jambes partielles comprises ;
  2. règles de sortie : celles écrites dans la ligne (depuis le 2026-11-21) ; à défaut, celles
     ACTUELLES du bras, avec l'avertissement que l'apprentissage a pu les changer depuis l'entrée ;
  3. diagnostic : pic au-dessus du TP1 sans jambe TP1 = ANOMALIE à creuser ; pic sous le TP1 =
     règle, pas bug ; échelle (`ladder`) franchie sans vente = ANOMALIE ;
  4. joint les mesures de détention (`volatility_log`, `microstructure_log`) du même `position_id` :
     un mouvement « extreme » ou une « impulse » rendent un pic sur prix aberrant plausible.

Usage :
  python -m scripts.explique_position 3d7a4da1aa80
  python -m scripts.explique_position OGTA
"""

import argparse
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings
from src.core.arm import load_manifest
from src.core.journal import TradeJournal

CHAMPS_REGLES = ("take_profit_1_pct", "take_profit_2_pct", "breakeven_trigger_pct",
                 "trailing_activation_pct", "max_hold_minutes", "exit_ladder")
# Correspondance règle journalisée ← clé de `exit_rules` dans la config du bras.
DEPUIS_CONFIG = {
    "take_profit_1_pct": "take_profit_1",
    "take_profit_2_pct": "take_profit_2",
    "breakeven_trigger_pct": "breakeven_trigger",
    "trailing_activation_pct": "trailing_stop_activation",
    "max_hold_minutes": "max_hold_time_minutes",
    "exit_ladder": "ladder",
}


def regles(jambes: list[dict[str, Any]], exit_rules_actuelles: dict[str, Any] | None) -> tuple[dict, str]:
    """Règles de sortie de la position et leur provenance (`journal` ou `config actuelle`)."""
    for jambe in jambes:
        if any(jambe.get(k) is not None for k in CHAMPS_REGLES):
            return {k: jambe.get(k) for k in CHAMPS_REGLES}, "journal (figées à l'entrée)"
    actuelles = exit_rules_actuelles or {}
    return ({k: actuelles.get(src) for k, src in DEPUIS_CONFIG.items()},
            "config ACTUELLE du bras : l'apprentissage a pu la changer depuis l'entrée")


def diagnostic(jambes: list[dict[str, Any]], regle: dict[str, Any], mesures: list[dict[str, Any]]) -> list[str]:
    """Constats, du plus important au moins important. Jamais de conclusion sans la donnée."""
    finale = next((j for j in jambes if j.get("is_final_exit")), jambes[-1])
    pic = finale.get("peak_pct")
    raisons = [str(j.get("exit_reason") or "") for j in jambes]
    a_vendu_tp1 = any(r.startswith("TAKE_PROFIT_1") for r in raisons)
    a_vendu_echelle = any(r.startswith("LADDER") or "ÉCHELLE" in r.upper() for r in raisons)
    constats = []
    tp1 = regle.get("take_profit_1_pct")
    echelle = regle.get("exit_ladder") or []
    if not isinstance(pic, (int, float)):
        return ["pic inconnu : rien à conclure"]
    if echelle:
        premier = min(float(r[0]) for r in echelle if isinstance(r, (list, tuple)) and r)
        if pic >= premier and not a_vendu_echelle and len(jambes) == 1:
            constats.append(f"ANOMALIE : le pic {pic:+.1f} % dépasse le premier palier de l'échelle "
                            f"({premier:+.0f} %) sans aucune vente partielle")
    elif isinstance(tp1, (int, float)):
        if pic >= tp1 and not a_vendu_tp1:
            constats.append(f"ANOMALIE : le pic {pic:+.1f} % dépasse le TP1 ({tp1:+.0f} %) sans jambe "
                            "TP1 : prix aberrant ou règle qui n'a pas joué, à creuser")
        elif pic < tp1:
            constats.append(f"règle, pas bug : le pic {pic:+.1f} % n'a pas atteint le TP1 ({tp1:+.0f} %)")
    else:
        constats.append("TP1 inconnu : impossible de dire si le pic aurait dû déclencher une vente")
    sortie = finale.get("pnl_pct")
    if isinstance(sortie, (int, float)) and pic - sortie >= 50:
        constats.append(f"rendu {pic - sortie:.0f} points entre le pic et la sortie "
                        f"({finale.get('exit_reason')})")
    for m in mesures:
        if m.get("extreme") or m.get("impulse"):
            constats.append("mouvement extrême mesuré pendant la détention : un pic sur prix aberrant est "
                            "plausible")
            break
    return constats


def _lignes_log(chemin: str, position_id: str) -> list[dict[str, Any]]:
    if not os.path.exists(chemin):
        return []
    trouvees = []
    with open(chemin, encoding="utf-8") as fh:
        for brut in fh:
            if position_id not in brut:
                continue
            try:
                ligne = json.loads(brut)
            except ValueError:
                continue
            if ligne.get("position_id") == position_id:
                trouvees.append(ligne)
    return trouvees


def trouver(cle: str) -> dict[str, list[dict[str, Any]]]:
    """`{(bras, position_id): [jambes]}` des positions dont l'id commence par `cle` ou dont le symbole vaut `cle`."""
    trouve: dict = {}
    for entree in load_manifest():
        chemin = settings.arm_paths(entree["name"])["trades"]
        if not os.path.exists(chemin):
            continue
        for ligne in TradeJournal(chemin).read_all():
            pid = str(ligne.get("position_id") or "")
            if pid.startswith(cle) or str(ligne.get("token") or "").upper() == cle.upper():
                trouve.setdefault((entree["name"], pid), []).append(ligne)
    return trouve


def _exit_rules(bras: str) -> dict[str, Any] | None:
    try:
        with open(settings.arm_paths(bras)["params"], encoding="utf-8") as fh:
            return json.load(fh).get("exit_rules")
    except (OSError, ValueError):
        return None


def rapport(bras: str, position_id: str, jambes: list[dict[str, Any]]) -> list[str]:
    jambes = sorted(jambes, key=lambda j: str(j.get("timestamp_exit") or ""))
    finale = next((j for j in jambes if j.get("is_final_exit")), jambes[-1])
    regle, provenance = regles(jambes, _exit_rules(bras))
    mesures = (_lignes_log(settings.VOLATILITY_LOG_PATH, position_id)
               + _lignes_log(settings.MICROSTRUCTURE_LOG_PATH, position_id))
    sortie = [
        f"\n{finale.get('token')} — bras {bras}, position {position_id} (PAPER)",
        f"  entrée {finale.get('timestamp_entry')}  prix {finale.get('entry_price')}",
        (f"  pic {finale.get('peak_pct')} % à {finale.get('minutes_to_peak')} min ; "
         f"creux {finale.get('trough_pct')} % à {finale.get('minutes_to_trough')} min"),
    ]
    for j in jambes:
        sortie.append(f"  sortie {j.get('timestamp_exit')}  {j.get('exit_reason')}  "
                      f"{j.get('pnl_pct')} % sur {j.get('fraction_of_initial')} de la position")
    sortie.append(f"  règles ({provenance}) : " + ", ".join(f"{k}={v}" for k, v in regle.items()))
    sortie.append(f"  mesures de détention jointes : {len(mesures)}")
    sortie.extend(f"  → {c}" for c in diagnostic(jambes, regle, mesures))
    return sortie


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cle", help="id de position (ou son début) ou symbole du token")
    parser.add_argument("--max", type=int, default=10, help="positions affichées au plus (défaut 10)")
    args = parser.parse_args()
    trouve = trouver(args.cle)
    if not trouve:
        raise SystemExit(f"aucune position pour « {args.cle} » dans les journaux des bras")
    for (bras, pid), jambes in list(trouve.items())[: args.max]:
        print("\n".join(rapport(bras, pid, jambes)))
    if len(trouve) > args.max:
        print(f"\n… {len(trouve) - args.max} autre(s) : précise l'id ou monte --max.")


if __name__ == "__main__":
    main()
