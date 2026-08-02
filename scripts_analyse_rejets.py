#!/usr/bin/env python3
"""Entonnoir de décision : où meurent les candidats, bras par bras.

RÉPOND À UNE SEULE QUESTION, celle qui bloque tout le reste : pourquoi un bras
ne trade pas. Quatre causes possibles, indiscernables sans ce rapport, et qui
appellent des corrections OPPOSÉES :

  marché vide          -> élargir la fenêtre d'âge ou de liquidité
  seuil trop strict    -> desserrer un filtre précis
  technique qui refuse -> revoir structure_mode
  carnet trop mince    -> baisser la taille, pas le filtre

Desserrer un filtre quand le vrai blocage est le coût d'entrée ne fait
qu'ajouter du bruit dans le pipeline.

Usage :
  python3 scripts_analyse_rejets.py              # depuis le début
  python3 scripts_analyse_rejets.py --heures 24
  python3 scripts_analyse_rejets.py --bras sniper
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import settings  # noqa: E402
from src.core.arm import load_manifest  # noqa: E402
from src.core.funnel import (  # noqa: E402
    FILTER_SAMPLE_EVERY,
    GATES,
    blocking_gate,
    funnel_by_arm,
    read_funnel,
    top_reasons,
)

CONSEILS = {
    "filtres": "un seuil du bras est trop strict — voir les motifs ci-dessous",
    "seuil_alpha": "le scoring ne monte pas assez : seuil d'entrée trop haut, "
                   "ou composant absent (social coupé = poids redistribués)",
    "confluence": "les autres bras ne voient pas ces tokens — fenêtres trop "
                  "disjointes pour qu'un accord existe",
    "portefeuille": "cooldown, positions déjà pleines, ou token déjà détenu",
    "technique": "pas de tendance ou pas d'expansion de volume — "
                 "entry_rules.structure_mode",
    "economie": "le CARNET, pas le signal : baisser la taille ou remonter le "
                "TP1, surtout pas desserrer un filtre",
}


def _titre(texte: str) -> None:
    print(f"\n{texte}\n{'─' * max(52, len(texte))}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--heures", type=float, default=0, help="fenêtre, 0 = tout")
    parser.add_argument("--bras", default="tous")
    args = parser.parse_args()

    since = time.time() - args.heures * 3600 if args.heures else None
    rows = read_funnel(settings.FUNNEL_LOG_PATH, since=since)
    if not rows:
        print(
            "Aucune donnée d'entonnoir.\n"
            "  Le journal se remplit à chaque cycle : laisse tourner quelques "
            "minutes.\n"
            f"  Fichier attendu : {settings.FUNNEL_LOG_PATH}"
        )
        return

    noms = [a["name"] for a in load_manifest() if a.get("enabled", True)]
    if args.bras != "tous":
        if args.bras not in noms:
            print(f"Bras inconnu : {args.bras}. Connus : {', '.join(noms)}")
            raise SystemExit(1)
        noms = [args.bras]
        rows = [r for r in rows if r.get("arm") == args.bras]

    span = (max(r["ts"] for r in rows) - min(r["ts"] for r in rows)) / 3600
    print(f"\n{'═' * 62}")
    print(f"  ENTONNOIR DE DÉCISION — {len(rows)} verdicts sur {span:.1f} h")
    print(f"{'═' * 62}")

    counts = funnel_by_arm(rows)

    _titre("1. PASSAGES PAR PORTE")
    entete = f"  {'bras':>10} " + " ".join(f"{g[:9]:>10}" for g in GATES)
    print(entete)
    for nom in noms:
        par_porte = counts.get(nom, {})
        cellules = []
        for gate in GATES:
            bucket = par_porte.get(gate)
            if not bucket:
                cellules.append(f"{'—':>10}")
                continue
            total = bucket["passed"] + bucket["failed"]
            cellules.append(f"{bucket['passed']:>4}/{total:<5}")
        print(f"  {nom:>10} " + " ".join(cellules))
    print("\n  lecture : passés/total. 'filtres' vient de compteurs exhaustifs ;"
          f"\n  les MOTIFS (§3) sont échantillonnés 1 cycle sur {FILTER_SAMPLE_EVERY}.")

    _titre("2. LA PORTE QUI BLOQUE, PAR BRAS")
    for nom in noms:
        par_porte = counts.get(nom, {})
        if not par_porte:
            print(f"  {nom:>10} : aucun candidat n'est jamais arrivé jusqu'ici")
            continue
        pire = blocking_gate(par_porte)
        entrees = par_porte.get("entree", {}).get("passed", 0)
        if entrees:
            print(f"  {nom:>10} : {entrees} entrée(s) — rien ne bloque")
            continue
        if pire is None:
            print(f"  {nom:>10} : aucun rejet enregistré")
            continue
        gate, failed, passed = pire
        print(f"  {nom:>10} : « {gate} » coûte {failed} candidats "
              f"({passed} survivent)")
        print(f"  {'':>10}   → {CONSEILS.get(gate, '')}")

    _titre("3. MOTIFS DE REJET LES PLUS FRÉQUENTS")
    for nom in noms:
        motifs = top_reasons([r for r in rows if r.get("arm") == nom], limit=6)
        if not motifs:
            continue
        print(f"\n  {nom}")
        for raison, n in motifs:
            print(f"    {n:>5}x  {raison[:70]}")

    economie = [r for r in rows if r.get("gate") == "economie"]
    if economie:
        _titre("4. GARDE ÉCONOMIQUE — ce que coûtent vraiment les carnets")
        couts = [r["round_trip_pct"] for r in economie
                 if r.get("round_trip_pct") is not None]
        refuses = [r for r in economie if not r.get("passed")]
        print(f"  {len(economie)} évaluations, {len(refuses)} refusées")
        if couts:
            import statistics
            print(f"  frais aller-retour : médiane {statistics.median(couts):.2f}% "
                  f"| min {min(couts):.2f}% | max {max(couts):.2f}%")
        planchers = [r["minimum_tp_pct"] for r in refuses
                     if r.get("minimum_tp_pct") is not None]
        if planchers:
            import statistics
            print(f"  plancher de TP1 exigé sur les refus : médiane "
                  f"+{statistics.median(planchers):.0f}%")
            print("  → si ce plancher dépasse ton TP1, c'est le TP qu'il faut "
                  "remonter,\n    pas le filtre qu'il faut desserrer.")

    sorties = [r for r in rows if r.get("gate") == "sortie"]
    alertes = [r for r in rows if r.get("gate") == "alerte_liquidite"]
    if sorties or alertes:
        _titre("5. SORTIES — est-on sorti au bon moment ?")
    if sorties:
        import statistics

        laisses = [r["laisse_sur_table"] for r in sorties
                   if r.get("laisse_sur_table") is not None]
        print(f"  {len(sorties)} sorties")
        if laisses:
            print(f"  laissé sur la table : médiane {statistics.median(laisses):.1f} pts "
                  f"| pire {max(laisses):.1f} pts")
            gaspillage = [r for r in sorties if (r.get("laisse_sur_table") or 0) > 50]
            if gaspillage:
                print(f"\n  {len(gaspillage)} sortie(s) ont laissé plus de 50 points :")
                for r in gaspillage[:5]:
                    print(f"    {r['symbol']:>12} sorti à {r['pnl_pct']:+.0f}% "
                          f"après un pic à {r['peak_pct']:+.0f}% "
                          f"— {r['reason'].split(' ')[0]}")
                print("  → une sortie à +5% sur un trade monté à +180% n'est pas un")
                print("    gain, c'est un échec de timing que le journal présente")
                print("    comme une réussite.")
    if alertes:
        agies = sum(1 for r in alertes if r.get("passed"))
        print(f"\n  {len(alertes)} chutes de liquidité observées, {agies} ont "
              f"déclenché une sortie")
        if len(alertes) > agies:
            manquees = [r for r in alertes if not r.get("passed")]
            pires = sorted(manquees, key=lambda r: r.get("liquidity_drop_pct") or 0)[:3]
            print(f"  {len(manquees)} sous le seuil de rug (-50%) — les pires :")
            for r in pires:
                print(f"    {r['symbol']:>12} {r['liquidity_drop_pct']:.0f}%")
            print("  → si ces tokens sont morts ensuite, le seuil de rug part trop tard.")

    print()


if __name__ == "__main__":
    main()
