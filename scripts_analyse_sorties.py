#!/usr/bin/env python3
"""Rapport de sorties : perdants, GAGNANTS, atteignabilité, grille, par bras.

Remplace `scripts_mesure_glissement.py`, qui ne regardait que les sorties par
stop loss et corrigeait donc un seul côté du problème. Il souffrait aussi de
trois défauts hérités :

  - il lisait les lignes `is_final_exit` : une position sortie en TP1 puis
    breakeven apparaissait comme une perte de -1,8%, ses +101% invisibles ;
  - son repli sur un seuil de -25 était périmé (le stop loss vaut -10) et
    faussait le « pire cas » ;
  - il libellait « déclenchement médian » ce qui était en fait le dépassement.

Usage :
  python3 scripts_analyse_sorties.py                 # tous les bras
  python3 scripts_analyse_sorties.py --bras runner   # un seul
  python3 scripts_analyse_sorties.py --sections 1,3  # sections choisies
"""

import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import settings  # noqa: E402
from src.core.arm import load_manifest  # noqa: E402
from src.core.journal import TradeJournal  # noqa: E402
from src.core.learning import LearningEngine  # noqa: E402
from src.core.params import ParamsStore  # noqa: E402
from src.core.stats import verdict_vs_reference  # noqa: E402

PIC_PALIERS = (10, 20, 25, 30, 40, 50, 75, 100, 150, 200)
CREUX_PALIERS = (-10, -15, -20, -25, -30, -40, -50)
MIN_ECHANTILLON = 10


def _titre(texte: str) -> None:
    print(f"\n{texte}\n{'─' * max(48, len(texte))}")


def _pct(part: int, total: int) -> str:
    return f"{100 * part / total:.0f}%" if total else "—"


def _mediane(valeurs, defaut=0.0):
    return statistics.median(valeurs) if valeurs else defaut


# ------------------------------------------------------------------ 1. vue


def section_vue_densemble(positions: list[dict]) -> None:
    _titre("1. VUE D'ENSEMBLE (par position, ventes partielles incluses)")
    if not positions:
        print("  Aucune position clôturée.")
        return

    gagnants = [p for p in positions if p["pnl_usd"] > 0]
    perdants = [p for p in positions if p["pnl_usd"] <= 0]
    gains = sum(p["pnl_usd"] for p in gagnants)
    pertes = abs(sum(p["pnl_usd"] for p in perdants))
    total = gains - pertes

    print(f"  positions        : {len(positions)}")
    print(
        f"  gagnantes        : {len(gagnants)} ({_pct(len(gagnants), len(positions))})"
        f"   perdantes : {len(perdants)}"
    )
    print(f"  P&L total        : {total:+.2f} $")
    print(f"  profit factor    : {gains / pertes:.2f}" if pertes else "  profit factor    : —")
    print(f"  espérance/trade  : {total / len(positions):+.2f} $")
    if gagnants:
        print(f"  gain moyen       : {statistics.fmean(p['pnl_pct'] for p in gagnants):+.1f}%")
    if perdants:
        print(f"  perte moyenne    : {statistics.fmean(p['pnl_pct'] for p in perdants):+.1f}%")

    multi = [p for p in positions if p.get("legs", 1) > 1]
    if multi:
        cache = sum(
            p["pnl_usd"] for p in multi if p["pnl_usd"] > 0
        )
        print(
            f"\n  {len(multi)} positions sorties en plusieurs fois. Compter seulement la"
            f"\n  dernière jambe masquait {cache:+.2f} $ et classait leurs gagnants en pertes."
        )


# -------------------------------------------------------------- 2. perdants


def section_perdants(positions: list[dict], stop_loss_actuel) -> None:
    _titre("2. PERDANTS — glissement du stop loss")
    stops = [
        p
        for p in positions
        if str(p.get("exit_reason", "")).startswith(("STOP_LOSS", "BREAKEVEN_STOP"))
    ]
    if not stops:
        print("  Aucune sortie par stop loss.")
        return

    instrumentes = [p for p in stops if p.get("stop_loss_trigger_pct") is not None]
    print(f"  {len(stops)} sorties par stop loss, {len(instrumentes)} instrumentées")
    if not instrumentes:
        print("  Aucune ne porte son seuil de déclenchement : rien à mesurer.")
        return

    # Uniquement les lignes instrumentées : un repli sur un seuil deviné
    # fausse la mesure de plusieurs points. Et on compare le P&L de la JAMBE
    # qui a touché le stop, pas celui de la position entière : un trade sorti
    # en TP1 puis breakeven vaut +49% au total pour un seuil à 0%.
    ecarts = [
        (p.get("final_leg_pnl_pct", p["pnl_pct"])) - p["stop_loss_trigger_pct"]
        for p in instrumentes
    ]
    depassements = [e for e in ecarts if e < 0]

    print(f"  dépassement médian  : {_mediane(ecarts):+.2f} points")
    print(f"  dépassement moyen   : {statistics.fmean(ecarts):+.2f} points")
    print(f"  pire cas            : {min(ecarts):+.2f} points")
    print(f"  sorties sous seuil  : {len(depassements)}/{len(ecarts)}")

    if depassements:
        suggere = round(abs(_mediane(depassements)), 1)
        print(f"\n  tampon suggéré : {suggere} points (médiane, un gap n'est pas du glissement)")
        print("  → REMPLACER exit_rules.stop_loss_slippage_buffer_pct, ne pas l'ajouter :")
        print("    stop_loss_trigger_pct contient déjà le tampon courant.")
    else:
        print("\n  Aucun dépassement — le tampon peut rester à 0.")

    if len(instrumentes) < MIN_ECHANTILLON:
        print(f"  ⚠ {len(instrumentes)} trades seulement : laisse tourner avant de régler.")


# -------------------------------------------------------------- 3. gagnants


def section_gagnants(positions: list[dict]) -> None:
    _titre("3. GAGNANTS — ce qu'ils atteignent, et ce qu'ils rendent")
    gagnants = [p for p in positions if p["pnl_usd"] > 0]
    if not gagnants:
        print("  Aucune position gagnante.")
        return

    print(f"  {len(gagnants)} positions gagnantes\n")
    print(f"  {'token':>12} {'P&L':>9} {'pic':>8} {'creux':>8} {'min→pic':>8}  chemin")
    for p in sorted(gagnants, key=lambda r: -r["pnl_usd"]):
        pic = f"{p['peak_pct']:+.0f}%" if p.get("peak_pct") is not None else "—"
        creux = f"{p['trough_pct']:+.0f}%" if p.get("trough_pct") is not None else "—"
        delai = f"{p['minutes_to_peak']:.0f}" if p.get("minutes_to_peak") is not None else "—"
        chemin = " → ".join(str(r).split(" ")[0] for r in p.get("exit_path", []))
        print(
            f"  {p['token']:>12} {p['pnl_usd']:>+8.2f}$ {pic:>8} {creux:>8} {delai:>8}  {chemin}"
        )

    delais = [p["minutes_to_peak"] for p in gagnants if p.get("minutes_to_peak")]
    if delais:
        print(f"\n  délai médian jusqu'au pic : {_mediane(delais):.0f} min")

    # Ce que rend le second lot après un TP1 partiel : c'est là que part le
    # gros du gain d'une position qui a fait 2x.
    seconds_lots = [
        p["pnl_pct"]
        for p in positions
        for reason in [str(p.get("exit_reason", ""))]
        if reason.startswith("BREAKEVEN_STOP") and p.get("legs", 1) > 1
    ]
    apres_tp1 = [
        leg
        for p in positions
        if p.get("legs", 1) > 1
        for leg in [str(p.get("exit_reason", ""))]
    ]
    if seconds_lots or apres_tp1:
        breakeven = sum(1 for r in apres_tp1 if r.startswith("BREAKEVEN_STOP"))
        print(
            f"\n  après TP1, {breakeven}/{len(apres_tp1)} positions ont rendu leur second lot"
            f" au breakeven."
        )
        print(
            "  Un 2x dont la moitié ressort à ~0% ne rapporte que la moitié du TP1 :"
            "\n  c'est l'hypothèse que le bras 'runner' teste en supprimant le breakeven."
        )


# --------------------------------------------------------- 4. atteignabilité


def section_atteignabilite(positions: list[dict], exits: dict) -> None:
    _titre("4. ATTEIGNABILITÉ — le TP était-il à portée ? le SL est-il dans le bruit ?")
    instrumentes = [
        p for p in positions
        if p.get("peak_pct") is not None and p.get("trough_pct") is not None
    ]
    if not instrumentes:
        print("  Aucune position instrumentée (pic/creux).")
        return

    total = len(instrumentes)
    pics = [p["peak_pct"] for p in instrumentes]
    creux = [p["trough_pct"] for p in instrumentes]
    tp1 = exits.get("take_profit_1")
    stop_loss = exits.get("stop_loss_pct")

    print(f"  {total} positions instrumentées sur {len(positions)}\n")
    print(f"  {'PIC atteint':<26}{'CREUX atteint'}")
    for pic_seuil, creux_seuil in zip(PIC_PALIERS, CREUX_PALIERS + (None,) * 9):
        touche = sum(1 for v in pics if v >= pic_seuil)
        gauche = f"  +{pic_seuil:>4}% : {touche:>2}/{total} ({_pct(touche, total):>4})"
        if tp1 is not None and pic_seuil == min(
            PIC_PALIERS, key=lambda s: abs(s - tp1)
        ):
            gauche += " ← TP1"
        droite = ""
        if creux_seuil is not None:
            franchi = sum(1 for v in creux if v <= creux_seuil)
            droite = f"{creux_seuil:>5}% : {franchi:>2}/{total} ({_pct(franchi, total):>4})"
            if stop_loss is not None and creux_seuil == min(
                CREUX_PALIERS, key=lambda s: abs(s - stop_loss)
            ):
                droite += " ← SL"
        print(f"{gauche:<34}{droite}")

    print(f"\n  pic médian   : {_mediane(pics):+.1f}%")
    print(f"  creux médian : {_mediane(creux):+.1f}%")

    if stop_loss is not None:
        touche_sl = sum(1 for v in creux if v <= stop_loss)
        if total and 100 * touche_sl / total > 80:
            print(
                f"\n  ⚠ {_pct(touche_sl, total)} des positions touchent {stop_loss}% à un moment."
                f"\n    Un stop que presque tout franchit est dans le bruit : il coupe aussi"
                f"\n    les positions qui repartaient."
            )


# ---------------------------------------------------------------- 5. grille


def section_grille(engine: LearningEngine, positions: list[dict], exits: dict) -> None:
    _titre("5. GRILLE STOP LOSS × TAKE PROFIT (rejeu sur pic/creux)")
    instrumentes = [
        p for p in positions
        if p.get("peak_pct") is not None and p.get("trough_pct") is not None
    ]
    if len(instrumentes) < MIN_ECHANTILLON:
        print(f"  {len(instrumentes)} positions instrumentées : trop peu pour une grille.")
        return

    actuel = engine.simulate_exits(instrumentes, exits)
    print(
        f"  Réglage actuel (SL {exits.get('stop_loss_pct')} / TP1 "
        f"+{exits.get('take_profit_1')}) : {actuel['pnl_per_trade']:+.3f} $/trade, "
        f"WR {actuel['win_rate']}%"
    )
    print(
        f"  glissement mesuré {actuel['slippage_used']} pts | second lot après TP1 "
        f"{actuel['breakeven_rest_used']}% | {actuel['ambiguous']} positions ambiguës"
    )
    print(
        "\n  ⚠ ÉLARGIR le stop est SOUS-ÉVALUÉ ici : quand aucun seuil n'est franchi,"
        "\n    le rejeu retombe sur le P&L réel, produit par le stop historique."
        "\n    La colonne 'chg' dit combien de positions changent vraiment ; une valeur"
        "\n    basse signifie que la ligne ne fait que recopier l'histoire.\n"
    )

    print(f"  {'SL':>5} {'TP1':>6} {'$/trade':>9} {'WR':>6} {'chg':>5} {'amb':>5}")
    for row in engine.exit_grid(instrumentes, exits)[:10]:
        marque = (
            " ←"
            if row["stop_loss_pct"] == exits.get("stop_loss_pct")
            and row["take_profit_1"] == exits.get("take_profit_1")
            else ""
        )
        print(
            f"  {row['stop_loss_pct']:>5g} {row['take_profit_1']:>+6g} "
            f"{row['pnl_per_trade']:>+9.3f} {row['win_rate']:>5.1f}% "
            f"{row['changed']:>5} {row['ambiguous']:>5}{marque}"
        )

    pess = engine.simulate_exits(instrumentes, exits, "pessimistic")
    opti = engine.simulate_exits(instrumentes, exits, "optimistic")
    if abs(pess["pnl_per_trade"] - opti["pnl_per_trade"]) > 1e-6:
        print(
            f"\n  Ambiguïté pic/creux : pessimiste {pess['pnl_per_trade']:+.3f} $/trade vs"
            f" optimiste {opti['pnl_per_trade']:+.3f}."
            f"\n  L'écart est la marge d'erreur du rejeu sur les trades antérieurs à"
            f"\n  l'horodatage du creux. Les nouveaux trades sont tranchés exactement."
        )


# ------------------------------------------------------------ 6. par bras


def section_comparatif(bras: list[str]) -> None:
    _titre("6. COMPARATIF PAR BRAS")
    lignes = []
    for nom in bras:
        chemin = settings.arm_paths(nom)["trades"]
        if not os.path.exists(chemin):
            lignes.append((nom, None))
            continue
        lignes.append((nom, TradeJournal(chemin).read_positions()))

    reference = dict(lignes).get(settings.BASELINE_ARM) or []

    print(
        f"  {'bras':>10} {'trades':>7} {'WR':>7} {'$/trade':>9} {'PF':>6} "
        f"{'pic méd':>9} {'creux méd':>10}   {'vs témoin':<22}"
    )
    for nom, positions in lignes:
        if not positions:
            print(f"  {nom:>10} {'—':>7} {'aucun trade':>26}")
            continue
        gagnants = [p for p in positions if p["pnl_usd"] > 0]
        gains = sum(p["pnl_usd"] for p in gagnants)
        pertes = abs(sum(p["pnl_usd"] for p in positions if p["pnl_usd"] <= 0))
        pics = [p["peak_pct"] for p in positions if p.get("peak_pct") is not None]
        creux = [p["trough_pct"] for p in positions if p.get("trough_pct") is not None]
        total = sum(p["pnl_usd"] for p in positions)

        if nom == settings.BASELINE_ARM:
            verdict = "— (c'est le témoin)"
        else:
            v = verdict_vs_reference(positions, reference)
            ic = v["interval"]
            verdict = v["verdict"]
            if ic:
                verdict += f" [{ic['low']:+.2f}..{ic['high']:+.2f}]"

        print(
            f"  {nom:>10} {len(positions):>7} "
            f"{100 * len(gagnants) / len(positions):>6.1f}% "
            f"{total / len(positions):>+9.3f} "
            f"{(gains / pertes if pertes else 0):>6.2f} "
            f"{_mediane(pics):>+8.1f}% {_mediane(creux):>+9.1f}%   {verdict:<22}"
        )

    print(
        "\n  « indistinguable » = les IC95 se recouvrent. Regarder sept stratégies\n"
        "  et garder la meilleure produit un gagnant PAR HASARD : cette colonne\n"
        "  est ce qui autorise à croire le classement, pas le $/trade."
    )

    actifs = [n for n, p in lignes if p]
    if len(actifs) < 2:
        print(
            "\n  Un seul bras a des trades. Les autres sont en OBSERVATION : ils votent"
            "\n  et publient leur confluence, sans capital. Passer à la phase suivante"
            "\n  quand la table de confluence montre qu'ils divergent vraiment."
        )


# ------------------------------------------------------------------ main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bras", default="tous", help="nom d'un bras, ou 'tous'")
    parser.add_argument("--sections", default="", help="ex. 1,3,5 (défaut : toutes)")
    args = parser.parse_args()

    noms = [a["name"] for a in load_manifest() if a.get("enabled", True)]
    if args.bras != "tous":
        if args.bras not in noms:
            print(f"Bras inconnu : {args.bras}. Connus : {', '.join(noms)}")
            raise SystemExit(1)
        noms = [args.bras]

    principal = noms[0]
    chemins = settings.arm_paths(principal)
    if not os.path.exists(chemins["trades"]):
        print(f"Aucun journal pour '{principal}'.")
        return

    journal = TradeJournal(chemins["trades"])
    params = ParamsStore(chemins["params"])
    engine = LearningEngine(params, journal)
    positions = journal.read_positions()
    exits = params.get("exit_rules", {})

    voulues = {s.strip() for s in args.sections.split(",") if s.strip()}

    def veut(numero: str) -> bool:
        return not voulues or numero in voulues

    print(f"\n{'═' * 62}")
    print(f"  RAPPORT DE SORTIES — bras « {principal} »")
    print(f"{'═' * 62}")

    if veut("1"):
        section_vue_densemble(positions)
    if veut("2"):
        section_perdants(positions, exits.get("stop_loss_pct"))
    if veut("3"):
        section_gagnants(positions)
    if veut("4"):
        section_atteignabilite(positions, exits)
    if veut("5"):
        section_grille(engine, positions, exits)
    if veut("6") and args.bras == "tous":
        section_comparatif(noms)
    print()


if __name__ == "__main__":
    main()
