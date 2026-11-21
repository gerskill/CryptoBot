#!/usr/bin/env python3
"""Que valent les entrées prises SANS une donnée ? — la mesure qui précède `require_data` (ADR 022).

QUESTION. Des entrées partent avec bundlers, insiders, snipers… vides (GMGN ou Helius en défaut) :
celles-là font-elles pire que les entrées où la donnée était présente ? Si oui et de façon nette,
exiger la donnée dans un bras (`filters.require_data`) a un sens ; sinon, rejeter ces candidats
priverait le bras d'entrées qui valent les autres.

CE QUE LE SCRIPT FAIT. Pour chaque bras (ou `--bras`) et chaque champ surveillé, sépare les POSITIONS
(jambes agrégées, `read_positions`) selon que le champ d'entrée est vide ou non, et compare le P&L
moyen par position avec un intervalle bootstrap à 95 % (`bootstrap_mean`), comme le reste du dépôt :
on ne conclut que si les intervalles sont disjoints.

CE QU'IL NE PEUT PAS DIRE, et l'écrit à chaque fois :
  - il ne voit que les entrées PRISES. Les candidats que `require_data` aurait rejetés et qui
    n'ont pas été pris restent inconnus (le shadow trading les suit, mais pour arbitrer des
    seuils, pas pour mesurer une performance : ADR 007) ;
  - les deux groupes sont souvent dans des PÉRIODES différentes (une panne de source dure des
    jours) : la comparaison est alors confondue par l'époque. Les dates de chaque groupe et leur
    recouvrement sont affichés ;
  - sous 30 positions par groupe, aucun verdict.

Lecture seule. Usage :
  python -m scripts.analyse_donnees_manquantes
  python -m scripts.analyse_donnees_manquantes --bras sniper
"""

import argparse
import os
import sys
from datetime import datetime
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings
from src.core.arm import load_manifest
from src.core.journal import TradeJournal
from src.core.sante_donnees import CHAMPS_ENTREE
from src.core.stats import bootstrap_mean, compare

# Par groupe. Même esprit que le seuil de 30 paris réglés des bras Liquid : sous ce nombre un
# intervalle est trop large pour distinguer quoi que ce soit.
MIN_POSITIONS = 30
# Recouvrement des périodes en dessous duquel on prévient que l'époque confond la comparaison.
RECOUVREMENT_MIN = 0.5


def _moment(texte: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(texte))
    except ValueError:
        return None


def resume_groupe(positions: list[dict[str, Any]]) -> dict[str, Any]:
    """Effectif, taux de gain, P&L moyen (%) avec son intervalle, et période couverte."""
    pnl = [float(p["pnl_pct"]) for p in positions if isinstance(p.get("pnl_pct"), (int, float))]
    moments = [m for m in (_moment(p.get("timestamp_entry")) for p in positions) if m is not None]
    intervalle = bootstrap_mean(pnl) if len(pnl) >= 3 else None
    return {
        "n": len(pnl),
        "win_rate": round(100 * sum(1 for v in pnl if v > 0) / len(pnl), 1) if pnl else None,
        "pnl_moyen_pct": round(sum(pnl) / len(pnl), 2) if pnl else None,
        "intervalle": intervalle,
        "du": min(moments) if moments else None,
        "au": max(moments) if moments else None,
    }


def recouvrement(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    """Part de la période commune sur la période totale (0 à 1), ou `None` si inconnue."""
    if None in (a["du"], a["au"], b["du"], b["au"]):
        return None
    debut = max(a["du"], b["du"])
    fin = min(a["au"], b["au"])
    total = (max(a["au"], b["au"]) - min(a["du"], b["du"])).total_seconds()
    if total <= 0:
        return 1.0
    return max(0.0, (fin - debut).total_seconds()) / total


def comparer(positions: list[dict[str, Any]], champ_journal: str) -> dict[str, Any]:
    """Entrées sans la donnée (`None`) contre avec : verdict prudent + avertissements."""
    sans = resume_groupe([p for p in positions if p.get(champ_journal) is None])
    avec = resume_groupe([p for p in positions if p.get(champ_journal) is not None])
    avertissements = []
    if sans["n"] < MIN_POSITIONS or avec["n"] < MIN_POSITIONS or sans["intervalle"] is None or avec["intervalle"] is None:
        verdict = "échantillon trop court"
    else:
        relation = compare(sans["intervalle"], avec["intervalle"])
        verdict = {"supérieur": "sans donnée : meilleur", "inférieur": "sans donnée : moins bon",
                   "indistinguable": "indistinguable"}[relation]
    part = recouvrement(sans, avec)
    if sans["n"] and avec["n"] and part is not None and part < RECOUVREMENT_MIN:
        avertissements.append(
            f"périodes différentes (recouvrement {100 * part:.0f} %) : l'époque confond la comparaison")
    return {"sans": sans, "avec": avec, "verdict": verdict, "avertissements": avertissements}


def _ic(groupe: dict[str, Any]) -> str:
    i = groupe["intervalle"]
    return f"[{i.low:+.1f} ; {i.high:+.1f}]" if i is not None else "[n/a]"


def _periode(groupe: dict[str, Any]) -> str:
    if groupe["du"] is None:
        return "—"
    return f"{groupe['du']:%d/%m} → {groupe['au']:%d/%m}"


def _ligne(nom: str, etiquette: str, g: dict[str, Any]) -> str:
    pnl = f"{g['pnl_moyen_pct']:+.2f} %" if g["pnl_moyen_pct"] is not None else "—"
    wr = f"{g['win_rate']:.0f} %" if g["win_rate"] is not None else "—"
    return f"    {nom:<12}{etiquette:<10}n={g['n']:<5} WR {wr:<6} P&L moyen {pnl:<10} IC95 {_ic(g):<18} {_periode(g)}"


def rapport(bras: str, positions: list[dict[str, Any]]) -> list[str]:
    lignes = [f"\n{bras} — {len(positions)} positions"]
    if not positions:
        return lignes + ["    aucune position"]
    for nom, champ in CHAMPS_ENTREE:
        r = comparer(positions, champ)
        if r["sans"]["n"] == 0:
            lignes.append(f"    {nom:<12}jamais vide sur ces positions")
            continue
        lignes.append(_ligne(nom, "sans", r["sans"]))
        lignes.append(_ligne("", "avec", r["avec"]))
        lignes.append(f"    {'':<12}→ {r['verdict']}")
        lignes.extend(f"    {'':<12}⚠ {a}" for a in r["avertissements"])
    return lignes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bras", help="un seul bras (défaut : tous les bras actifs)")
    args = parser.parse_args()

    noms = [e["name"] for e in load_manifest() if e.get("enabled", True)]
    if args.bras:
        if args.bras not in noms:
            raise SystemExit(f"bras inconnu ou désactivé : {args.bras} (actifs : {', '.join(noms)})")
        noms = [args.bras]
    print("Entrées prises SANS une donnée d'entrée, comparées à celles où elle était présente.")
    print(f"Verdict seulement avec au moins {MIN_POSITIONS} positions par groupe ; intervalles disjoints.")
    for nom in noms:
        chemin = settings.arm_paths(nom)["trades"]
        positions = TradeJournal(chemin).read_positions() if os.path.exists(chemin) else []
        print("\n".join(rapport(nom, positions)))
    print(
        "\nÀ garder en tête : seules les entrées PRISES sont vues (pas les candidats qu'`require_data` "
        "aurait rejetés) ; des positions d'un même token entrent dans plusieurs bras ; "
        "voir docs/adr/022-donnees-requises-opt-in.md."
    )


if __name__ == "__main__":
    main()
