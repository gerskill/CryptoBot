#!/usr/bin/env python3
"""RugCheck filtre-t-il quelque chose ? — mesure en lecture seule (option 2 du 2026-11-24).

CONSTAT DE DÉPART (Mac, 2026-11-24, 24 h) : 446 entrées, toutes avec un score RugCheck, dont 442 à
99. RugCheck donne 99 à presque tout token de moins d'une heure (voir `src/agents/dev_history.py`) :
le seuil `filters.min_rugcheck_score` ne rejette donc presque jamais rien. Avant de changer quoi que
ce soit, on mesure deux choses.

  1. LES REJETS AVANT L'ENTRÉE, lus dans l'entonnoir (`data/funnel_log*.jsonl`) : combien de
     tokens distincts RugCheck a écartés (risque critique ou score sous le seuil), et quelle part
     de tous les tokens rejetés. L'entonnoir n'écrit le détail qu'un cycle sur 10 : ce sont des
     proportions sur un échantillon, pas des totaux. Le shadow ne sert pas ici : il exclut exprès
     les rejets RugCheck et Authority (verdict du juge du 2026-11-24).
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
from src.core.funnel import FILTER_SAMPLE_EVERY, rotated_path
from src.core.journal import TradeJournal
from src.core.stats import compare

JOURS_DEFAUT = 7
# Sous ce nombre de rejets, une proportion ne dit rien (même seuil que la Santé des données).
MIN_REJETS = 10


# --- 1. Rejets avant l'entrée (entonnoir) -----------------------------------------------------

def est_rejet_rugcheck(raison: Any) -> str | None:
    """`critique`, `score` ou `None` selon la raison de rejet écrite par le pipeline."""
    texte = str(raison or "")
    if texte.startswith("risque critique RugCheck"):
        return "critique"
    if texte.startswith("rugcheck "):
        return "score"
    return None


def lire_entonnoir(chemins: list[str], depuis: float) -> list[dict[str, Any]]:
    """Lignes `filtres` refusées de l'entonnoir postérieures à `depuis` (epoch).

    Le shadow ne sert à rien ici : il saute exprès les rejets RugCheck et Authority
    (`src/core/shadow.py`, `record_rejections`). L'entonnoir, lui, écrit la raison de chaque rejet,
    mais seulement sur un cycle d'évaluation sur `FILTER_SAMPLE_EVERY` : c'est un ÉCHANTILLON."""
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
                moment = ligne.get("ts")
                if (ligne.get("gate") != "filtres" or ligne.get("passed") is not False
                        or not isinstance(moment, (int, float)) or moment < depuis):
                    continue
                lignes.append(ligne)
    return lignes


def resume_rejets(lignes: list[dict[str, Any]], bras: list[str] | None = None) -> dict[str, Any]:
    """Tokens distincts rejetés (tout motif) et rejetés par RugCheck, dans l'échantillon.

    Un rejet RugCheck se fait dans le pipeline commun : le même token apparaît une fois par bras.
    On compte donc des TOKENS DISTINCTS, pas des lignes."""
    if bras is not None:
        lignes = [ligne for ligne in lignes if ligne.get("arm") in bras]
    tous = {ligne.get("token") for ligne in lignes if ligne.get("token")}
    par_type: dict[str, set] = {"critique": set(), "score": set()}
    exemples: dict[str, int] = {}
    for ligne in lignes:
        genre = est_rejet_rugcheck(ligne.get("reason"))
        if genre and ligne.get("token"):
            par_type[genre].add(ligne["token"])
            exemples[str(ligne.get("reason"))[:90]] = exemples.get(str(ligne.get("reason"))[:90], 0) + 1
    rugcheck = par_type["critique"] | par_type["score"]
    return {
        "tokens_rejetes": len(tous),
        "rugcheck": len(rugcheck),
        "critique": len(par_type["critique"]),
        "score": len(par_type["score"]),
        "motifs": sorted(exemples.items(), key=lambda kv: -kv[1])[:5],
    }


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

def _groupe(etiquette: str, g: dict[str, Any]) -> str:
    pnl = f"{g['pnl_moyen_pct']:+.2f} %" if g["pnl_moyen_pct"] is not None else "—"
    wr = f"{g['win_rate']:.0f} %" if g["win_rate"] is not None else "—"
    i = g["intervalle"]
    ic = f"[{i.low:+.1f} ; {i.high:+.1f}]" if i is not None else "[n/a]"
    return f"      {etiquette:<22}n={g['n']:<5} WR {wr:<6} P&L moyen {pnl:<10} IC95 {ic}"


def rapport_rejets(r: dict[str, Any], jours: int) -> list[str]:
    part = (f"{100 * r['rugcheck'] / r['tokens_rejetes']:.1f} %"
            if r["tokens_rejetes"] >= MIN_REJETS else "trop peu pour juger")
    lignes = [
        f"1. Rejets avant l'entrée (entonnoir, {jours} derniers jours, ÉCHANTILLON 1 cycle sur {FILTER_SAMPLE_EVERY})",
        (f"   tokens distincts rejetés : {r['tokens_rejetes']} ; dont par RugCheck : {r['rugcheck']} ({part}) "
         f"— risque critique {r['critique']}, score sous le seuil {r['score']}"),
    ]
    lignes += [f"   {n:>5} × {motif}" for motif, n in r["motifs"]]
    lignes.append("   (le shadow n'est pas utilisé : il exclut exprès les rejets RugCheck et Authority)")
    return lignes


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
    entonnoir = lire_entonnoir([rotated_path(settings.FUNNEL_LOG_PATH), settings.FUNNEL_LOG_PATH], depuis)
    print("\n".join(rapport_rejets(resume_rejets(entonnoir, noms), args.jours)))

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
