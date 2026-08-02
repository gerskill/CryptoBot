#!/usr/bin/env python3
"""Ce que les filtres ont coûté : les rejets, et ce qu'ils sont devenus.

POURQUOI CE RAPPORT. Toutes les décisions du projet reposent sur 26 positions
instrumentées. Le shadow log en compte 399 pour le seul bras témoin — quinze
fois l'échantillon, déjà collecté, gratuit, et il mesure exactement la question
qui bloque : **les filtres coupent-ils des gagnants ?**

CE QUE CE RAPPORT NE DIT PAS, et il faut le dire avant de lire un chiffre :

  - Un shadow n'a NI SLIPPAGE NI IMPACT DE MARCHÉ. Une entrée réelle aurait
    bougé le prix, surtout sur les carnets minces qui remplissent la famille
    `liquidity`. Le pic affiché est un plafond, pas un gain.
  - Le pic n'est pas un P&L : l'atteindre suppose d'être sorti pile dessus.
    La colonne « TP1 » ci-dessous est la bonne lecture — franchir un seuil est
    vérifiable, « aurait gagné X » ne l'est pas.
  - Donc : ce rapport arbitre des SEUILS. Jamais une performance. Voir ADR 007.

LA DISCIPLINE APPLIQUÉE ICI est celle du reste du dépôt : un taux sans son
intervalle est de la fausse précision. `stats.wilson` accompagne chaque
proportion, et une famille sous `SHADOW_MIN_SAMPLE` est affichée mais marquée
« pas de verdict » — ne pas savoir n'est pas la même chose que savoir que non.

Usage :
  python3 scripts_analyse_shadow.py
  python3 scripts_analyse_shadow.py --bras sniper
  python3 scripts_analyse_shadow.py --seuil 50     # « aurait franchi +50 % »
"""

import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import settings  # noqa: E402
from src.core.arm import load_manifest  # noqa: E402
from src.core.learning import (  # noqa: E402
    MISSED_RATE_RELAX_THRESHOLD,
    SHADOW_MIN_SAMPLE,
)
from src.core.shadow import ShadowTracker, reason_family  # noqa: E402
from src.core.stats import wilson  # noqa: E402

# Familles que `_relax_from_shadow` sait relâcher, et le paramètre visé.
# Les autres sont observables mais rien ne les desserre automatiquement.
RELACHABLES = {
    "liquidity": "filters.min_liquidity_usd",
    "holders": "filters.min_holders",
    "social": "filters.min_social_mentions_1h",
    "smart_money": "filters.min_smart_money_buys_30min",
    "concentration": "filters.max_top_wallet_concentration",
    "age_max": "filters.max_age_hours",
    "age_min": "filters.min_age_hours",
    "volume": "filters.min_volume_1h",
}
# Jamais relâchées, quel que soit le manque à gagner. Elles ne devraient même
# pas apparaître dans le log — `record_rejections` les écarte à l'entrée.
JAMAIS = ("rugcheck", "authority")


def _titre(texte: str) -> None:
    print(f"\n{texte}\n{'─' * max(52, len(texte))}")


def _tp1_du_bras(nom: str) -> float:
    """TP1 configuré, pour juger un rejet contre le seuil qui le concerne."""
    from src.core.params import ParamsStore

    chemin = settings.arm_paths(nom)["params"]
    if not os.path.exists(chemin):
        return 100.0
    return float(ParamsStore(chemin).get("exit_rules.take_profit_1", 100) or 100)


def _rapport_bras(nom: str, seuil: float) -> None:
    tracker = ShadowTracker(settings.arm_paths(nom)["shadow"])
    rows = tracker.read_all()

    _titre(f"BRAS {nom.upper()} — seuil jugé : +{seuil:.0f} %")
    if not rows:
        print("  aucun rejet jugé.")
        print("  Un bras sans shadow log ne peut PAS relâcher ses filtres :")
        print("  `_relax_from_shadow` n'a rien à lire.")
        return

    # Famille RECALCULÉE depuis le motif, comme `missed_rate_by_family` : les
    # lignes déjà écrites portent la taxonomie de leur époque, où l'âge et le
    # volume tombaient tous les deux dans « autre ».
    groupes: dict[str, list[dict]] = {}
    for row in rows:
        famille = reason_family(row.get("reason", "")) or row.get(
            "reason_family", "autre"
        )
        groupes.setdefault(famille, []).append(row)

    print(f"  {len(rows)} rejets jugés, {len(groupes)} familles\n")
    print(f"  {'famille':<14} {'n':>4} {'≥seuil':>7} {'IC95':>14} "
          f"{'pic méd':>8} {'pic max':>8}  verdict")

    total_franchis = 0
    for famille, lignes in sorted(groupes.items(), key=lambda kv: -len(kv[1])):
        pics = [float(r.get("peak_gain_pct") or 0) for r in lignes]
        franchis = [p for p in pics if p >= seuil]
        total_franchis += len(franchis)
        interval = wilson(len(franchis), len(lignes))

        if len(lignes) < SHADOW_MIN_SAMPLE:
            verdict = f"pas de verdict (< {SHADOW_MIN_SAMPLE})"
        elif famille in JAMAIS:
            verdict = "jamais relâchée — sécurité"
        elif famille not in RELACHABLES:
            verdict = "observée, aucun paramètre associé"
        elif interval.value >= MISSED_RATE_RELAX_THRESHOLD:
            verdict = f"RELÂCHE → {RELACHABLES[famille]}"
        elif interval.high >= MISSED_RATE_RELAX_THRESHOLD:
            verdict = "sous le seuil au point, pas à la borne haute"
        else:
            verdict = "rejets justifiés"

        print(f"  {famille:<14} {len(lignes):>4} {interval.value:>6.1f}% "
              f"{f'[{interval.low:.0f}–{interval.high:.0f}]':>14} "
              f"{statistics.median(pics):>7.0f}% {max(pics):>7.0f}%  {verdict}")

    print(f"\n  {total_franchis} rejets sur {len(rows)} ont franchi +{seuil:.0f} %.")

    # Le détail qui pilote la décision : les plus gros manqués, avec la valeur
    # du champ qui les a fait rejeter. Un seuil se déplace sur des cas, pas
    # sur une moyenne.
    manques = sorted(rows, key=lambda r: -(r.get("peak_gain_pct") or 0))
    gros = [r for r in manques if (r.get("peak_gain_pct") or 0) >= seuil]
    if gros:
        print(f"\n  Les plus gros manqués (pic ≥ +{seuil:.0f} %) :")
        for row in gros[:10]:
            liq = row.get("liquidity_at_rejection")
            age = row.get("age_hours_at_rejection")
            print(f"    {row.get('token', '?'):>12} pic "
                  f"{row.get('peak_gain_pct', 0):+7.0f}%  "
                  f"liq {liq:>9,.0f}$  âge {age:>5.1f}h  — {row.get('reason', '')}"
                  if liq is not None and age is not None else
                  f"    {row.get('token', '?'):>12} pic "
                  f"{row.get('peak_gain_pct', 0):+7.0f}%  — {row.get('reason', '')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bras", help="un seul bras, sinon tous")
    parser.add_argument(
        "--seuil", type=float,
        help="pic à franchir, en %%. Par défaut le TP1 configuré du bras",
    )
    args = parser.parse_args()

    noms = [args.bras] if args.bras else [
        entry["name"] for entry in load_manifest() if entry.get("enabled", True)
    ]

    print("=" * 60)
    print("ANALYSE DU SHADOW — ce que les filtres ont coûté")
    print("=" * 60)

    for nom in noms:
        _rapport_bras(nom, args.seuil if args.seuil is not None else _tp1_du_bras(nom))

    _titre("À LIRE AVANT D'AGIR")
    print("  Un shadow n'a ni slippage ni impact de marché : sur un carnet")
    print("  mince, une entrée réelle aurait bougé le prix. Le pic est un")
    print("  PLAFOND, pas un gain. Ce rapport déplace des SEUILS, il ne mesure")
    print("  aucune performance.")
    print()
    print(f"  Une famille sous {SHADOW_MIN_SAMPLE} rejets n'a pas de verdict —")
    print("  ne pas savoir n'est pas savoir que non.")
    print()


if __name__ == "__main__":
    main()
