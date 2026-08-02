#!/usr/bin/env python3
"""Exporte le journal de trading en notes Obsidian reliées.

POURQUOI un vault plutôt qu'un tableau : 36 trades tiennent dans un JSONL,
300 non. Ce qui compte n'est pas la ligne mais le RECOUPEMENT — « tous les
trades sortis en STOP_LOSS entre 1 et 2h », « ce token, quels bras l'ont pris
et avec quel résultat ». Un graphe de notes répond à ça par navigation, sans
requête à écrire.

CE QUE ÇA N'EST PAS : un tableau de bord. L'état vivant (positions ouvertes,
confluence du cycle) reste dans `data/state.json` et le dashboard React. Ici
c'est l'histoire, pas le direct.

Complémentaire de `graphify --obsidian`, qui produit le graphe du CODE dans le
même vault : deux graphes, un dossier, une seule vue.

Usage :
  python3 scripts_export_vault.py                        # ~/vaults/cryptobot
  python3 scripts_export_vault.py --vault <chemin>
  python3 scripts_export_vault.py --bras runner
"""

import argparse
import os
import statistics
import sys
from collections import defaultdict
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import settings  # noqa: E402
from src.core.arm import load_manifest  # noqa: E402
from src.core.journal import TradeJournal  # noqa: E402
from src.core.params import ParamsStore  # noqa: E402

# Le vault héberge DEUX graphes : `Code/` produit par graphify (une note par
# entité de code, noms issus des docstrings) et `Trading/` produit ici. Les
# séparer est indispensable : à plat, 1265 notes de code noieraient les 80
# notes de trading, et Obsidian résout les [[wikilinks]] à travers les dossiers
# de toute façon.
DEFAULT_VAULT = os.path.expanduser("~/vaults/cryptobot/Trading")
AGE_BUCKETS = [(0, 0.5, "0-30min"), (0.5, 1, "30min-1h"), (1, 2, "1-2h"),
               (2, 4, "2-4h"), (4, 24, "4-24h"), (24, 1e9, "24h+")]
LIQ_BUCKETS = [(0, 10_000, "moins de 10K"), (10_000, 25_000, "10-25K"),
               (25_000, 50_000, "25-50K"), (50_000, 1e12, "plus de 50K")]


def bucket(value: Optional[float], buckets: list) -> Optional[str]:
    if value is None:
        return None
    for low, high, label in buckets:
        if low <= value < high:
            return label
    return None


def slug(text: str) -> str:
    """Nom de fichier sûr. Les symboles de memecoin contiennent de tout."""
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in str(text))
    return safe.strip() or "sans-nom"


def write(vault: str, name: str, lines: list[str]) -> None:
    os.makedirs(vault, exist_ok=True)
    with open(os.path.join(vault, f"{slug(name)}.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip() + "\n")


def stats(positions: list[dict]) -> dict[str, Any]:
    if not positions:
        return {"n": 0, "wr": 0.0, "pnl": 0.0, "par_trade": 0.0, "pf": 0.0}
    wins = [p for p in positions if p["pnl_usd"] > 0]
    gains = sum(p["pnl_usd"] for p in wins)
    losses = abs(sum(p["pnl_usd"] for p in positions if p["pnl_usd"] <= 0))
    total = sum(p["pnl_usd"] for p in positions)
    return {
        "n": len(positions),
        "wr": round(100 * len(wins) / len(positions), 1),
        "pnl": round(total, 2),
        "par_trade": round(total / len(positions), 3),
        "pf": round(gains / losses, 2) if losses else 0.0,
    }


def table(rows: list[tuple], headers: tuple) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return lines


# ------------------------------------------------------------------- notes


def note_trade(vault: str, arm: str, p: dict) -> str:
    """Une position = une note. Elle relie son bras, son token, sa sortie."""
    title = f"Trade {p.get('token', '?')} {str(p.get('timestamp_exit', ''))[:16]}"
    reason = str(p.get("exit_reason", "")).split(" ")[0] or "INCONNU"
    age = bucket(p.get("age_hours_at_entry"), AGE_BUCKETS)
    liq = bucket(p.get("liquidity_at_entry"), LIQ_BUCKETS)
    verdict = "gagnant" if p["pnl_usd"] > 0 else "perdant"

    lines = [
        "---",
        f"bras: {arm}",
        f"token: {p.get('token')}",
        f"resultat: {verdict}",
        f"pnl_usd: {p['pnl_usd']}",
        f"pnl_pct: {p['pnl_pct']}",
        f"sortie: {reason}",
        f"age_entree_h: {p.get('age_hours_at_entry')}",
        "---",
        "",
        f"# {title}",
        "",
        f"**{p['pnl_usd']:+.2f} $** ({p['pnl_pct']:+.1f} %) — {verdict}",
        "",
    ]

    facts = [
        ("bras", f"[[Bras {arm}]]"),
        ("token", f"[[Token {p.get('token')}]]"),
        ("sortie", f"[[Sortie {reason}]]"),
        ("entrée", p.get("timestamp_entry", "?")),
        ("durée", f"{p.get('duration_min', '?')} min"),
        ("taille", f"{p.get('position_size', '?')} $"),
        ("score alpha", p.get("score_alpha", "?")),
    ]
    if age:
        facts.append(("tranche d'âge", f"[[Age {age}]]"))
    if liq:
        facts.append(("liquidité", f"[[Liquidite {liq}]]"))
    lines += table([(k, v) for k, v in facts], ("champ", "valeur"))

    peak, trough = p.get("peak_pct"), p.get("trough_pct")
    if peak is not None:
        lines += [
            "",
            "## Trajectoire",
            "",
            f"- pic **{peak:+.1f} %**"
            + (f" atteint à {p['minutes_to_peak']:.0f} min"
               if p.get("minutes_to_peak") is not None else ""),
            f"- creux **{trough:+.1f} %**"
            + (f" atteint à {p['minutes_to_trough']:.0f} min"
               if p.get("minutes_to_trough") is not None else ""),
        ]
        target = p.get("stop_loss_target_pct")
        trigger = p.get("stop_loss_trigger_pct")
        if target is not None and trigger is not None:
            lines.append(f"- stop visé {target} %, déclenché à {trigger} %")

    path = p.get("exit_path") or []
    if len(path) > 1:
        lines += ["", "## Chemin de sortie", ""]
        lines += [f"{i}. {step}" for i, step in enumerate(path, 1)]
        lines += ["", "> Sortie en plusieurs fois : le P&L ci-dessus agrège "
                  "toutes les jambes. Ne compter que la dernière classait ce "
                  "trade en perte."]

    write(vault, title, lines)
    return title


def note_arm(vault: str, arm: str, positions: list[dict], trades: list[str],
             manifest: dict, params: Optional[ParamsStore]) -> None:
    s = stats(positions)
    lines = [
        "---", f"type: bras", f"nom: {arm}", "---", "",
        f"# Bras {arm}", "",
        manifest.get("description", ""), "",
        "## Résultats", "",
    ]
    lines += table(
        [("trades", s["n"]), ("win rate", f"{s['wr']} %"),
         ("P&L", f"{s['pnl']:+.2f} $"), ("P&L / trade", f"{s['par_trade']:+.3f} $"),
         ("profit factor", s["pf"])],
        ("mesure", "valeur"),
    )

    if params is not None:
        f = params.get("filters", {})
        e = params.get("exit_rules", {})
        lines += ["", "## Sélection", ""]
        lines += table(
            [(k, v) for k, v in sorted(f.items()) if not k.startswith("_")],
            ("filtre", "seuil"),
        )
        lines += ["", "## Sorties", ""]
        lines += table(
            [(k, v) for k, v in sorted(e.items()) if not k.startswith("_")],
            ("règle", "valeur"),
        )
        history = params.get("learning.parameter_adjustment_history", []) or []
        if history:
            lines += ["", "## Ce que le bras a appris", ""]
            for h in history[-10:]:
                lines.append(
                    f"- `{h.get('param_name')}` : {h.get('old_value')} → "
                    f"{h.get('new_value')} — {h.get('reason', '')[:120]}"
                )

    if trades:
        lines += ["", "## Trades", ""] + [f"- [[{t}]]" for t in trades[-40:]]
    else:
        lines += ["", "_Aucun trade. Bras en observation, ou fenêtre de "
                  "sélection vide sur la période._"]

    lines += ["", "---", "", "[[Index Bras]] · [[Index CryptobBot]]"]
    write(vault, f"Bras {arm}", lines)


def note_group(vault: str, prefix: str, label: str, entries: list[tuple[str, dict]],
               index: str) -> None:
    """Note d'agrégat : une tranche d'âge, une raison de sortie, un token."""
    positions = [p for _, p in entries]
    s = stats(positions)
    lines = [
        "---", f"type: {prefix.lower()}", "---", "",
        f"# {prefix} {label}", "",
    ]
    lines += table(
        [("trades", s["n"]), ("win rate", f"{s['wr']} %"),
         ("P&L", f"{s['pnl']:+.2f} $"), ("P&L / trade", f"{s['par_trade']:+.3f} $")],
        ("mesure", "valeur"),
    )

    by_arm: dict[str, list] = defaultdict(list)
    for arm, p in entries:
        by_arm[arm].append(p)
    if len(by_arm) > 1:
        lines += ["", "## Par bras", ""]
        lines += table(
            [(f"[[Bras {a}]]", stats(ps)["n"], f"{stats(ps)['wr']} %",
              f"{stats(ps)['pnl']:+.2f} $") for a, ps in sorted(by_arm.items())],
            ("bras", "trades", "WR", "P&L"),
        )

    lines += ["", "## Trades", ""]
    for arm, p in entries[-40:]:
        titre = f"Trade {p.get('token', '?')} {str(p.get('timestamp_exit', ''))[:16]}"
        lines.append(f"- [[{titre}]] — {p['pnl_usd']:+.2f} $ ({arm})")

    lines += ["", "---", "", f"[[{index}]] · [[Index CryptobBot]]"]
    write(vault, f"{prefix} {label}", lines)


def note_index(vault: str, titre: str, items: list[str], intro: str = "") -> None:
    lines = ["---", "type: index", "---", "", f"# {titre}", ""]
    if intro:
        lines += [intro, ""]
    lines += [f"- [[{i}]]" for i in items]
    lines += ["", "---", "", "[[Index CryptobBot]]"]
    write(vault, titre, lines)


# -------------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", default=DEFAULT_VAULT)
    parser.add_argument("--bras", default="tous")
    args = parser.parse_args()

    manifest = {a["name"]: a for a in load_manifest() if a.get("enabled", True)}
    noms = list(manifest) if args.bras == "tous" else [args.bras]
    if args.bras != "tous" and args.bras not in manifest:
        print(f"Bras inconnu : {args.bras}. Connus : {', '.join(manifest)}")
        raise SystemExit(1)

    vault = os.path.expanduser(args.vault)
    os.makedirs(vault, exist_ok=True)

    par_bras: dict[str, list[dict]] = {}
    titres_par_bras: dict[str, list[str]] = {}
    par_token: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    par_sortie: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    par_age: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    par_liq: dict[str, list[tuple[str, dict]]] = defaultdict(list)

    for nom in noms:
        chemins = settings.arm_paths(nom)
        positions = (
            TradeJournal(chemins["trades"]).read_positions()
            if os.path.exists(chemins["trades"]) else []
        )
        par_bras[nom] = positions
        titres_par_bras[nom] = []
        for p in positions:
            titres_par_bras[nom].append(note_trade(vault, nom, p))
            par_token[str(p.get("token", "?"))].append((nom, p))
            par_sortie[str(p.get("exit_reason", "")).split(" ")[0] or "INCONNU"].append((nom, p))
            age = bucket(p.get("age_hours_at_entry"), AGE_BUCKETS)
            if age:
                par_age[age].append((nom, p))
            liq = bucket(p.get("liquidity_at_entry"), LIQ_BUCKETS)
            if liq:
                par_liq[liq].append((nom, p))

        params = (
            ParamsStore(chemins["params"]) if os.path.exists(chemins["params"]) else None
        )
        note_arm(vault, nom, positions, titres_par_bras[nom], manifest.get(nom, {}), params)

    for label, entries in par_token.items():
        note_group(vault, "Token", label, entries, "Index Tokens")
    for label, entries in par_sortie.items():
        note_group(vault, "Sortie", label, entries, "Index Sorties")
    for label, entries in par_age.items():
        note_group(vault, "Age", label, entries, "Index Age")
    for label, entries in par_liq.items():
        note_group(vault, "Liquidite", label, entries, "Index Liquidite")

    note_index(vault, "Index Bras", [f"Bras {n}" for n in noms],
               "Une stratégie par note : sélection, sorties, ce qu'elle a appris.")
    note_index(vault, "Index Tokens", sorted(f"Token {t}" for t in par_token))
    note_index(vault, "Index Sorties", sorted(f"Sortie {s}" for s in par_sortie),
               "Quelle règle a fermé la position.")
    note_index(vault, "Index Age", [f"Age {label}" for _, _, label in AGE_BUCKETS
                                    if label in par_age],
               "Âge du token au moment de l'achat.")
    note_index(vault, "Index Liquidite", [f"Liquidite {label}" for _, _, label in LIQ_BUCKETS
                                          if label in par_liq])

    # --- racine ---
    toutes = [p for ps in par_bras.values() for p in ps]
    s = stats(toutes)
    racine = [
        "---", "type: index", "---", "", "# Index CryptobBot", "",
        "Journal de trading en graphe. Chaque position relie son bras, son "
        "token, sa raison de sortie, sa tranche d'âge et de liquidité — les "
        "recoupements se lisent en naviguant, sans écrire de requête.", "",
        "> L'**état vivant** (positions ouvertes, confluence du cycle) n'est "
        "pas ici : il est dans le dashboard. Ce vault, c'est l'histoire.", "",
        "## Ensemble", "",
    ]
    racine += table(
        [("trades", s["n"]), ("win rate", f"{s['wr']} %"),
         ("P&L", f"{s['pnl']:+.2f} $"), ("P&L / trade", f"{s['par_trade']:+.3f} $"),
         ("profit factor", s["pf"])],
        ("mesure", "valeur"),
    )
    racine += ["", "## Par bras", ""]
    racine += table(
        [(f"[[Bras {n}]]", stats(ps)["n"], f"{stats(ps)['wr']} %",
          f"{stats(ps)['pnl']:+.2f} $", f"{stats(ps)['par_trade']:+.3f} $")
         for n, ps in par_bras.items()],
        ("bras", "trades", "WR", "P&L", "$/trade"),
    )

    instrumentes = [p for p in toutes if p.get("peak_pct") is not None]
    if instrumentes:
        pics = [p["peak_pct"] for p in instrumentes]
        creux = [p["trough_pct"] for p in instrumentes]
        racine += [
            "", "## Ce que la trajectoire raconte", "",
            f"Sur {len(instrumentes)} positions instrumentées : pic médian "
            f"**{statistics.median(pics):+.1f} %**, creux médian "
            f"**{statistics.median(creux):+.1f} %**.", "",
        ]
        racine += table(
            [(f"{seuil:+} %", sum(1 for v in pics if v >= seuil),
              sum(1 for v in creux if v <= -seuil))
             for seuil in (10, 25, 50, 100)],
            ("seuil", "pics l'atteignent", "creux le franchissent (négatif)"),
        )

    racine += [
        "", "## Index", "",
        "- [[Index Bras]]", "- [[Index Tokens]]", "- [[Index Sorties]]",
        "- [[Index Age]]", "- [[Index Liquidite]]",
    ]
    write(vault, "Index CryptobBot", racine)

    notes = len([f for f in os.listdir(vault) if f.endswith(".md")])
    print(f"{notes} notes écrites dans {vault}")
    print(f"  {s['n']} positions | {len(par_token)} tokens | {len(par_sortie)} raisons de sortie")
    print(f"  ouvrir : Obsidian > Open folder as vault > {vault}")


if __name__ == "__main__":
    main()
