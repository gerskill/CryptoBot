#!/usr/bin/env python3
"""RugCheck filtre-t-il quelque chose ? — mesure en lecture seule (option 2 du 2026-11-24).

CONSTAT DE DÉPART (Mac, 2026-11-24, 24 h) : 446 entrées, toutes avec un score RugCheck, dont 442 à
99. RugCheck donne 99 à presque tout token de moins d'une heure (voir `src/agents/dev_history.py`) :
le seuil `filters.min_rugcheck_score` ne rejette donc presque jamais rien. Avant de changer quoi que
ce soit, on mesure deux choses.

  1. LES REJETS AVANT L'ENTRÉE, lus dans les journaux shadow : combien de candidats RugCheck a
     écartés (risque critique ou score sous le seuil), quelle part de tous les rejets, et si ces
     candidats « auraient gagné » plus ou moins souvent que les autres rejetés. Le shadow suit les
     rejets pour arbitrer des seuils, pas pour mesurer une performance (ADR 007) : c'est un
     indice, pas un P&L.
  2. LES ENTRÉES PRISES, par bras : répartition des scores, puis P&L des positions avec des
     risques RugCheck listés contre sans, et score 99 contre score plus bas. Même méthode que
     `scripts/analyse_donnees_manquantes.py` : IC95 bootstrap, verdict seulement avec 30 positions
     par groupe et des intervalles disjoints, avertissement si les périodes ne se recouvrent pas.

Lecture seule, ne change aucun comportement. Usage :
  python -m scripts.analyse_rugcheck
  python -m scripts.analyse_rugcheck --jours 7 --bras sniper
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.analyse_donnees_manquantes import (
    MIN_POSITIONS,
    RECOUVREMENT_MIN,
    recouvrement,
    resume_groupe,
)
from src import settings
from src.core.arm import load_manifest
from src.core.journal import TradeJournal
from src.core.stats import compare

JOURS_DEFAUT = 7
# Sous ce nombre de rejets, une proportion ne dit rien (même seuil que la Santé des données).
MIN_REJETS = 10


# --- 1. Rejets avant l'entrée (shadow) -------------------------------------------------------

def est_rejet_rugcheck(raison: Any) -> str | None:
    """`critique`, `score` ou `None` selon la raison de rejet écrite par le pipeline."""
    texte = str(raison or "")
    if texte.startswith("risque critique RugCheck"):
        return "critique"
    if texte.startswith("rugcheck "):
        return "score"
    return None


def _taux_gagnants(lignes: list[dict[str, Any]]) -> float | None:
    jugees = [ligne for ligne in lignes if isinstance(ligne.get("would_have_won"), bool)]
    if len(jugees) < MIN_REJETS:
        return None
    return round(100 * sum(1 for ligne in jugees if ligne["would_have_won"]) / len(jugees), 1)


def resume_rejets(lignes: list[dict[str, Any]]) -> dict[str, Any]:
    """Rejets RugCheck parmi tous les rejets shadow, et taux « aurait gagné » de chaque côté."""
    rugcheck = [ligne for ligne in lignes if est_rejet_rugcheck(ligne.get("reason"))]
    autres = [ligne for ligne in lignes if not est_rejet_rugcheck(ligne.get("reason"))]
    return {
        "total": len(lignes),
        "rugcheck": len(rugcheck),
        "critique": sum(1 for ligne in rugcheck if est_rejet_rugcheck(ligne.get("reason")) == "critique"),
        "score": sum(1 for ligne in rugcheck if est_rejet_rugcheck(ligne.get("reason")) == "score"),
        "gagnants_rugcheck_pct": _taux_gagnants(rugcheck),
        "gagnants_autres_pct": _taux_gagnants(autres),
    }


def lire_shadow(chemins: list[str], depuis: float) -> list[dict[str, Any]]:
    """Lignes shadow postérieures à `depuis` (epoch), sans doublon entre journaux partagés."""
    vues = set()
    lignes = []
    for chemin in dict.fromkeys(chemins):
        if not os.path.exists(chemin):
            continue
        with open(chemin, encoding="utf-8") as fh:
            for brut in fh:
                try:
                    ligne = json.loads(brut)
                except ValueError:
                    continue
                moment = ligne.get("timestamp")
                if not isinstance(moment, (int, float)) or moment < depuis:
                    continue
                cle = (ligne.get("token_address"), moment, ligne.get("reason"))
                if cle in vues:
                    continue
                vues.add(cle)
                lignes.append(ligne)
    return lignes


# --- 2. Entrées prises -------------------------------------------------------------------------

def _moment(texte: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(texte))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def dans_la_fenetre(positions: list[dict[str, Any]], depuis: datetime) -> list[dict[str, Any]]:
    return [p for p in positions if (m := _moment(p.get("timestamp_entry"))) is not None and m >= depuis]


def repartition_scores(positions: list[dict[str, Any]], seuil: float = 70) -> dict[str, int]:
    """Effectifs : score 99, entre le seuil et 99, sous le seuil, absent."""
    compte = {"99": 0, "seuil-98": 0, "sous le seuil": 0, "absent": 0}
    for p in positions:
        score = p.get("rugcheck_score")
        if not isinstance(score, (int, float)):
            compte["absent"] += 1
        elif score >= 99:
            compte["99"] += 1
        elif score >= seuil:
            compte["seuil-98"] += 1
        else:
            compte["sous le seuil"] += 1
    return compte


def comparer_groupes(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> dict[str, Any]:
    """Groupe `a` contre groupe `b` : verdict prudent et avertissements, comme ADR 022."""
    ra, rb = resume_groupe(a), resume_groupe(b)
    if ra["n"] < MIN_POSITIONS or rb["n"] < MIN_POSITIONS or ra["intervalle"] is None or rb["intervalle"] is None:
        verdict = "échantillon trop court"
    else:
        verdict = {"supérieur": "meilleur", "inférieur": "moins bon",
                   "indistinguable": "indistinguable"}[compare(ra["intervalle"], rb["intervalle"])]
    avertissements = []
    part = recouvrement(ra, rb)
    if ra["n"] and rb["n"] and part is not None and part < RECOUVREMENT_MIN:
        avertissements.append(f"périodes différentes (recouvrement {100 * part:.0f} %) : l'époque confond la comparaison")
    return {"a": ra, "b": rb, "verdict": verdict, "avertissements": avertissements}


def a_des_risques(position: dict[str, Any]) -> bool:
    return bool(position.get("entry_rugcheck_risks"))


def score_99(position: dict[str, Any]) -> bool:
    score = position.get("rugcheck_score")
    return isinstance(score, (int, float)) and score >= 99


# --- Rapport -----------------------------------------------------------------------------------

def _pct(valeur: float | None) -> str:
    return f"{valeur:.0f} %" if valeur is not None else "trop peu pour juger"


def _groupe(etiquette: str, g: dict[str, Any]) -> str:
    pnl = f"{g['pnl_moyen_pct']:+.2f} %" if g["pnl_moyen_pct"] is not None else "—"
    wr = f"{g['win_rate']:.0f} %" if g["win_rate"] is not None else "—"
    i = g["intervalle"]
    ic = f"[{i.low:+.1f} ; {i.high:+.1f}]" if i is not None else "[n/a]"
    return f"      {etiquette:<22}n={g['n']:<5} WR {wr:<6} P&L moyen {pnl:<10} IC95 {ic}"


def rapport_rejets(r: dict[str, Any], jours: int) -> list[str]:
    part = f"{100 * r['rugcheck'] / r['total']:.1f} %" if r["total"] else "—"
    return [
        f"1. Rejets avant l'entrée (journaux shadow, {jours} derniers jours)",
        (f"   rejets suivis : {r['total']} ; dont RugCheck : {r['rugcheck']} ({part}) "
         f"— risque critique {r['critique']}, score sous le seuil {r['score']}"),
        (f"   « aurait gagné » : rejetés par RugCheck {_pct(r['gagnants_rugcheck_pct'])}, "
         f"autres rejetés {_pct(r['gagnants_autres_pct'])}"),
        "   (indice seulement : le shadow arbitre des seuils, il ne mesure pas un P&L — ADR 007)",
    ]


def rapport_bras(nom: str, positions: list[dict[str, Any]]) -> list[str]:
    lignes = [f"\n   {nom} — {len(positions)} positions"]
    if not positions:
        return lignes
    lignes.append("      scores : " + ", ".join(f"{k} {v}" for k, v in repartition_scores(positions).items()))
    for titre, critere, oui, non in (
        ("risques listés", a_des_risques, "avec risques", "sans risque"),
        ("score", score_99, "score 99", "score < 99"),
    ):
        groupe_oui = [p for p in positions if critere(p)]
        groupe_non = [p for p in positions if not critere(p)]
        if not groupe_oui or not groupe_non:
            lignes.append(f"      {titre} : un seul groupe, rien à comparer")
            continue
        r = comparer_groupes(groupe_oui, groupe_non)
        lignes += [_groupe(oui, r["a"]), _groupe(non, r["b"]), f"      → {oui} : {r['verdict']}"]
        lignes += [f"      ⚠ {a}" for a in r["avertissements"]]
    return lignes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jours", type=int, default=JOURS_DEFAUT, help=f"fenêtre en jours (défaut {JOURS_DEFAUT})")
    parser.add_argument("--bras", help="un seul bras (défaut : tous les bras actifs)")
    args = parser.parse_args()

    noms = [e["name"] for e in load_manifest() if e.get("enabled", True)]
    if args.bras:
        if args.bras not in noms:
            raise SystemExit(f"bras inconnu ou désactivé : {args.bras} (actifs : {', '.join(noms)})")
        noms = [args.bras]

    depuis = time.time() - args.jours * 86400
    shadow = lire_shadow([settings.arm_paths(n)["shadow"] for n in noms], depuis)
    print("\n".join(rapport_rejets(resume_rejets(shadow), args.jours)))

    print(f"\n2. Entrées prises ({args.jours} derniers jours), par bras")
    print(f"   verdict seulement avec au moins {MIN_POSITIONS} positions par groupe et des IC95 disjoints")
    limite = datetime.now(timezone.utc) - timedelta(days=args.jours)
    for nom in noms:
        chemin = settings.arm_paths(nom)["trades"]
        positions = TradeJournal(chemin).read_positions() if os.path.exists(chemin) else []
        print("\n".join(rapport_bras(nom, dans_la_fenetre(positions, limite))))
    print("\nÀ garder en tête : un même token entre dans plusieurs bras ; seules les entrées PRISES "
          "ont un P&L ; aucune conclusion sous 30 positions par groupe.")


if __name__ == "__main__":
    main()
