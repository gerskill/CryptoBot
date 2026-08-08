#!/usr/bin/env python3
"""Rapport hebdomadaire multi-bras — évolution semaine par semaine.

Câblé sur l'architecture réelle du dépôt, pas sur un schéma inventé :
- un bras = un fichier (`settings.arm_paths`), pas un champ `arm` dans
  chaque ligne (ce champ n'existe pas) ;
- `journal.read_positions()` — la même source que le dashboard, l'apprentissage
  et le verrou LIVE — pour que ce rapport ne raconte jamais une histoire
  différente du reste du dépôt ;
- filtrage sur `timestamp_exit`, le seul horodatage réellement écrit à la
  clôture (le journal ne porte pas de champ `opened_at`) ;
- `exit_reason` comparé par préfixe (`RUG_PULL`, `STOP_LOSS`, ...), jamais par
  égalité stricte : la raison porte toujours un détail entre parenthèses
  (`"RUG_PULL (liquidité -80%)"`), une égalité stricte ne matche jamais.

Usage :
  python3 scripts_rapport_hebdo.py                  # 7 derniers jours
  python3 scripts_rapport_hebdo.py --jours 14        # fenêtre différente
  python3 scripts_rapport_hebdo.py --bras quality    # un seul bras
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from src.core.arm import load_manifest  # noqa: E402
from src.core.journal import TradeJournal  # noqa: E402

# Raisons produites par la boucle elle-même (règles automatiques). Tout ce qui
# n'est PAS l'un de ces préfixes est une fermeture manuelle (panic button,
# force_close) — la raison exacte est libre (`MANUAL_STOP`, `PANIC_STOP`, ...),
# la reconnaître par exclusion évite de deviner une chaîne figée.
RAISONS_AUTOMATIQUES = (
    "STOP_LOSS", "BREAKEVEN_STOP", "TAKE_PROFIT", "TRAILING_STOP",
    "TIME_STOP", "RUG_PULL",
)


def _dans_fenetre(position: dict, depuis: datetime) -> bool:
    horodatage = position.get("timestamp_exit")
    if not horodatage:
        return False
    try:
        exit_dt = datetime.fromisoformat(horodatage)
    except ValueError:
        return False
    if exit_dt.tzinfo is None:
        exit_dt = exit_dt.replace(tzinfo=timezone.utc)
    return exit_dt >= depuis


def _stats_bras(positions: list[dict]) -> dict:
    gagnants = [p for p in positions if p.get("pnl_usd", 0) > 0]
    perdants = [p for p in positions if p.get("pnl_usd", 0) <= 0]
    gains = sum(p.get("pnl_usd", 0) for p in gagnants)
    pertes = abs(sum(p.get("pnl_usd", 0) for p in perdants))
    avec_cout = [p for p in positions if p.get("exit_cost_pct") is not None]

    return {
        "trades": len(positions),
        "win_rate": 100 * len(gagnants) / len(positions) if positions else 0.0,
        "pnl_usd": gains - pertes,
        "profit_factor": (gains / pertes) if pertes else (float("inf") if gains else 0.0),
        "couverture_couts_pct": (
            100 * len(avec_cout) / len(positions) if positions else 0.0
        ),
        "rug_pulls": sum(
            1 for p in positions if str(p.get("exit_reason", "")).startswith("RUG_PULL")
        ),
        "fermetures_manuelles": sum(
            1 for p in positions
            if not str(p.get("exit_reason", "")).startswith(RAISONS_AUTOMATIQUES)
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jours", type=int, default=7, help="taille de la fenêtre (défaut 7)")
    parser.add_argument("--bras", default="tous", help="nom d'un bras, ou 'tous'")
    args = parser.parse_args()

    noms = [a["name"] for a in load_manifest() if a.get("enabled", True)]
    if args.bras != "tous":
        if args.bras not in noms:
            print(f"Bras inconnu : {args.bras}. Connus : {', '.join(noms)}")
            raise SystemExit(1)
        noms = [args.bras]

    depuis = datetime.now(timezone.utc) - timedelta(days=args.jours)

    print(f"\n{'═' * 74}")
    print(f"  RAPPORT HEBDOMADAIRE — {args.jours}j — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    print(f"{'═' * 74}")

    resultats = {}
    for nom in noms:
        chemins = settings.arm_paths(nom)
        if not os.path.exists(chemins["trades"]):
            continue
        journal = TradeJournal(chemins["trades"])
        positions = [p for p in journal.read_positions() if _dans_fenetre(p, depuis)]
        resultats[nom] = _stats_bras(positions)

    if not any(r["trades"] for r in resultats.values()):
        print(f"\n  Aucun trade clôturé dans les {args.jours} derniers jours.")
        return

    print(f"\n{'Bras':<14}{'Trades':>8}{'WR':>8}{'P&L':>12}{'PF':>8}{'Coûts':>8}")
    print("─" * 74)
    total_pnl = 0.0
    total_trades = 0
    for nom in sorted(resultats, key=lambda n: resultats[n]["pnl_usd"], reverse=True):
        r = resultats[nom]
        if r["trades"] == 0:
            print(f"{nom:<14}{'—':>8}")
            continue
        pf = "∞" if r["profit_factor"] == float("inf") else f"{r['profit_factor']:.2f}"
        print(
            f"{nom:<14}{r['trades']:>8}{r['win_rate']:>7.1f}%"
            f"{r['pnl_usd']:>+11.2f}$ {pf:>7}{r['couverture_couts_pct']:>7.0f}%"
        )
        total_pnl += r["pnl_usd"]
        total_trades += r["trades"]

    print("─" * 74)
    print(f"{'TOTAL':<14}{total_trades:>8}{'':>8}{total_pnl:>+11.2f}$")

    print(f"\n{'─' * 74}")
    print("  ÉVÉNEMENTS NOTABLES")
    print(f"{'─' * 74}")
    for nom, r in resultats.items():
        if r["trades"] == 0:
            continue
        alertes = []
        if r["rug_pulls"]:
            alertes.append(f"{r['rug_pulls']} rug pull(s)")
        if r["fermetures_manuelles"]:
            alertes.append(f"{r['fermetures_manuelles']} fermeture(s) manuelle(s)")
        if r["couverture_couts_pct"] < 90 and r["trades"] >= 5:
            alertes.append(
                f"coûts de sortie mesurés sur seulement {r['couverture_couts_pct']:.0f}% des trades"
            )
        if alertes:
            print(f"  {nom:<14} {' | '.join(alertes)}")
    print()


if __name__ == "__main__":
    main()
