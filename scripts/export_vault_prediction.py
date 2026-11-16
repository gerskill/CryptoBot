#!/usr/bin/env python3
"""Exporte les paris de prédiction (papier) en notes Obsidian reliées.

Même principe que `scripts/export_vault.py` : une note par bras, par domaine
et par pari, reliées par des [[liens]], pour répondre par navigation à
« quels paris ce bras a-t-il pris sur ce domaine, et avec quel résultat ».

Le dossier `Prediction/` est réécrit à chaque export : ce sont des notes
GÉNÉRÉES, pas un endroit où écrire à la main. Le journal de session
(`Journal.md`) n'est jamais touché.

Usage :
  python3 scripts/export_vault_prediction.py                    # ~/vaults/cryptobot/Prediction
  python3 scripts/export_vault_prediction.py --vault <chemin>
  python3 scripts/export_vault_prediction.py --demo             # exemple fictif, dossier séparé
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from src.prediction.paper import BetStore  # noqa: E402
from src.prediction.runner import load_config, summary  # noqa: E402

DEFAULT_VAULT = os.path.expanduser("~/vaults/cryptobot/Prediction")


def slug(text: str) -> str:
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in str(text))
    return safe.strip()[:80] or "sans_nom"


def fmt(value, pattern="{:.2f}"):
    return "—" if value is None else pattern.format(value)


def write(path: str, lines: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def export(vault: str, data_dir: str, demo: bool) -> int:
    config = load_config(settings.PREDICTION_ARMS_PATH)
    banner = ["> [!warning] Exemple fictif", "> Données inventées par `scripts/prediction_demo.py`.", ""] if demo else []
    rows = summary(config, data_dir)
    by_domain: dict[str, list[dict]] = {}
    count = 0

    for row in rows:
        store = BetStore(data_dir, row["arm"])
        bets = store.settled() + store.open_bets()
        stats = row["stats"]
        lines = [f"# {row['arm']}", ""] + banner + [
            row["description"], "",
            f"- Rôle : {row['role']}{'' if row['enabled'] else ' (désactivé)'}",
            f"- Domaines : {', '.join('[[' + d + ']]' for d in row['domaines'])}",
            f"- Paris réglés : {stats['paris_regles']} · ouverts : {row['ouverts']} · indéterminés : {stats['paris_indetermines']}",
            f"- P&L net (frais déduits) : {fmt(stats['pnl_net'])} $",
            f"- Profit factor : {fmt(stats['profit_factor'])} · gagnés : {fmt(stats['win_rate'], '{:.1%}')}",
            f"- Drawdown max : {fmt(stats['drawdown_max_pct'])} % du capital fictif · pires 5 % : {fmt(stats['pires_5pct_moyenne'])} $",
            f"- Brier bras / marché : {fmt(stats['brier_bras'], '{:.3f}')} / {fmt(stats['brier_marche'], '{:.3f}')}",
            f"- Verdict : {stats['verdict']}", "",
            "## Paris", "",
            "| entrée (UTC) | pari | prix payé | estimé | issue | P&L net |", "|---|---|---|---|---|---|",
        ]
        for bet in sorted(bets, key=lambda b: b.get("entry_time") or ""):
            note = slug(bet["id"])
            issue = {"regle": "gagné" if bet.get("won") else "perdu", "ouvert": "ouvert"}.get(bet["status"], "indéterminé")
            lines.append(f"| {bet['entry_time']} | [[{note}]] | {bet['entry_price']:.4f} | "
                         f"{fmt(bet.get('prob_estimee'), '{:.4f}')} | {issue} | {fmt(bet.get('pnl_net'))} |")
            write(os.path.join(vault, "Paris", f"{note}.md"), [f"# {bet['title']}", ""] + banner + [
                f"- Bras : [[{row['arm']}]]", f"- Domaine : [[{bet['domain']}]]",
                f"- Côté : {bet['side_name']} · pièce `{bet['coin']}`",
                f"- Entrée : {bet['entry_time']} à {bet['entry_price']:.4f} (mise {bet['stake_usd']:.2f} $)",
                f"- Probabilité estimée : {fmt(bet.get('prob_estimee'), '{:.4f}')} ({bet.get('source_estimation')})",
                f"- Échéance : {bet.get('expiry') or 'inconnue'}",
                f"- Règlement : {bet.get('settle_time') or '—'} · valeur {fmt(bet.get('settle_value'))} · {issue}",
                f"- P&L net : {fmt(bet.get('pnl_net'))} $",
            ])
            by_domain.setdefault(bet["domain"], []).append(bet)
            count += 1
        write(os.path.join(vault, "Bras", f"{row['arm']}.md"), lines)

    for domain in {d for row in rows for d in row["domaines"]}:
        bets = by_domain.get(domain, [])
        decided = [b for b in bets if b["status"] == "regle"]
        write(os.path.join(vault, "Domaines", f"{domain}.md"), [f"# {domain}", ""] + banner + [
            f"- Paris : {len(bets)} · réglés : {len(decided)}",
            f"- P&L net tous bras de ce domaine : {sum(b['pnl_net'] for b in decided):.2f} $ "
            "(somme de bras différents : à lire bras par bras ci-dessous)", "",
            "## Bras actifs sur ce domaine", "",
        ] + [f"- [[{row['arm']}]]" for row in rows if domain in row["domaines"]])

    write(os.path.join(vault, "Index prédiction.md"), ["# Marchés de prédiction Liquid — papier", ""] + banner + [
        "Notes générées par `scripts/export_vault_prediction.py`. Ne pas écrire ici à la main.", "",
        "## Bras", ""] + [f"- [[{row['arm']}]] — {row['stats']['paris_regles']} réglés, {row['stats']['verdict']}"
                          for row in rows])
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vault", default=None)
    parser.add_argument("--demo", action="store_true", help="exporter l'exemple fictif")
    args = parser.parse_args()
    # L'exemple fictif va dans un dossier à part : jamais mêlé aux vraies notes.
    vault = args.vault or (DEFAULT_VAULT + (" - exemple fictif" if args.demo else ""))
    data_dir = settings.PREDICTION_DEMO_DIR if args.demo else settings.PREDICTION_DATA_DIR
    count = export(vault, data_dir, args.demo)
    print(f"{count} paris exportés dans {vault}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
