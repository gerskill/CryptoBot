#!/usr/bin/env python3
"""Audit statistique : intervalles, walk-forward, coût, corrélation.

Complète les deux autres rapports. `scripts_analyse_sorties.py` dit CE QUI
s'est passé, `scripts_analyse_rejets.py` dit OÙ ça bloque ; celui-ci dit
CE QU'ON PEUT EN CONCLURE — et surtout ce qu'on ne peut pas.

Chaque section répond à une critique nommée de l'audit du projet.

Usage : python3 scripts_audit.py
"""

import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import settings  # noqa: E402
from src.core.arm import load_manifest  # noqa: E402
from src.core.journal import TradeJournal  # noqa: E402
from src.core.learning import LearningEngine  # noqa: E402
from src.core.params import ParamsStore  # noqa: E402
from src.core.stats import (  # noqa: E402
    bootstrap_mean,
    compare,
    overfit_gap,
    walk_forward,
    wilson,
)

# Coût en requêtes par cycle. Mesuré dans les logs, pas estimé.
# Sous ce nombre de trades, aucun verdict de comparaison ne compte. Aligné
# sur MIN_TRADES_PER_ARM du moteur d'apprentissage : le même échantillon qui
# ne permet pas d'ajuster un paramètre ne permet pas de classer un bras.
MIN_TRADES_VERDICT = 15

COUT_PAR_CYCLE = {
    "découverte DexScreener": 3,
    "découverte GMGN (3 intervalles)": 3,
    "flux smart money GMGN": 1,
    "RugCheck (par token non caché)": 25,
    "bougies GMGN (par candidat qualifié)": 3,
    "devis Jupiter (par tentative d'entrée)": 3,
}


def _titre(texte: str) -> None:
    print(f"\n{texte}\n{'─' * max(56, len(texte))}")


def arms() -> list[str]:
    return [a["name"] for a in load_manifest() if a.get("enabled", True)]


def _read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def positions_of(arm: str) -> list[dict]:
    path = settings.arm_paths(arm)["trades"]
    return TradeJournal(path).read_positions() if os.path.exists(path) else []


def section_intervalles() -> None:
    _titre("1. INTERVALLES DE CONFIANCE — ce qu'on sait vraiment")
    print(f"  {'bras':>10} {'n':>4} {'win rate (IC95)':>26} {'$/trade (IC95)':>28}")
    for nom in arms():
        pos = positions_of(nom)
        if not pos:
            print(f"  {nom:>10} {0:>4} {'—':>26} {'—':>28}")
            continue
        wr = wilson(sum(1 for p in pos if p["pnl_usd"] > 0), len(pos))
        pnl = bootstrap_mean([p["pnl_usd"] for p in pos])
        pnl_txt = (
            f"{pnl.value:+.2f} [{pnl.low:+.2f} .. {pnl.high:+.2f}]" if pnl else "n<3"
        )
        drapeau = "" if wr.conclusive else "  (trop large)"
        print(
            f"  {nom:>10} {len(pos):>4} "
            f"{f'{wr.value:.1f}% [{wr.low:.1f}-{wr.high:.1f}]':>26} {pnl_txt:>28}{drapeau}"
        )
    print(
        "\n  Un intervalle de plus de 30 points ne distingue pas une bonne\n"
        "  stratégie d'une mauvaise. Wilson, pas Wald : sur 1 succès/36 Wald\n"
        "  rend une borne basse négative."
    )


def section_comparaison() -> None:
    _titre("2. COMPARAISON DES BRAS — qui bat qui, vraiment")
    mesures = {}
    for nom in arms():
        pos = positions_of(nom)
        if len(pos) >= 3:
            mesures[nom] = (
                wilson(sum(1 for p in pos if p["pnl_usd"] > 0), len(pos)),
                bootstrap_mean([p["pnl_usd"] for p in pos]),
                len(pos),
            )

    if len(mesures) < 2:
        prets = sum(1 for n in arms() if len(positions_of(n)) >= MIN_TRADES_VERDICT)
        print(
            f"  {len(mesures)} bras avec au moins 3 trades — la comparaison en\n"
            f"  demande deux. {prets}/{len(arms())} bras ont atteint les "
            f"{MIN_TRADES_VERDICT} trades\n  requis pour un verdict."
        )
        return

    noms = list(mesures)
    print(f"  {'':>10} " + " ".join(f"{n[:9]:>13}" for n in noms))
    for a in noms:
        cellules = []
        for b in noms:
            if a == b:
                cellules.append(f"{'—':>13}")
                continue
            verdict = compare(mesures[a][1], mesures[b][1])
            cellules.append(f"{verdict:>13}")
        print(f"  {a:>10} " + " ".join(cellules))

    print(
        "\n  Verdict sur les INTERVALLES, pas les moyennes. « indistinguable »\n"
        "  ne veut pas dire « égaux » : ça veut dire que l'échantillon ne\n"
        "  permet pas de trancher. Comparer deux moyennes sans leur largeur\n"
        "  fabrique un classement là où il n'y a que du bruit."
    )
    incomplets = [n for n, (_, _, n_pos) in mesures.items() if n_pos < MIN_TRADES_VERDICT]
    if incomplets:
        print(
            f"\n  ⚠ sous {MIN_TRADES_VERDICT} trades, aucun verdict ne compte : "
            f"{', '.join(incomplets)}"
        )


def section_walk_forward() -> None:
    _titre("3. WALK-FORWARD — le réglage tient-il hors échantillon ?")
    journal = TradeJournal(settings.arm_paths("baseline")["trades"])
    params = ParamsStore(settings.arm_paths("baseline")["params"])
    engine = LearningEngine(params, journal)
    pos = [
        p for p in journal.read_positions()
        if p.get("peak_pct") is not None and p.get("trough_pct") is not None
    ]
    if len(pos) < 13:
        print(f"  {len(pos)} positions instrumentées — pas assez pour trois plis.")
        return

    def evaluate(train, test):
        best = engine.exit_grid(train)[0]
        rules = {
            **params.get("exit_rules", {}),
            "stop_loss_pct": best["stop_loss_pct"],
            "take_profit_1": best["take_profit_1"],
        }
        out = engine.simulate_exits(test, rules)
        return {
            "sl": best["stop_loss_pct"], "tp1": best["take_profit_1"],
            "train_score": best["pnl_per_trade"], "test_score": out["pnl_per_trade"],
        }

    folds = walk_forward(pos, evaluate, folds=3)
    print(f"  {'pli':>4} {'train':>6} {'test':>5} {'SL':>5} {'TP1':>5} "
          f"{'apprentissage':>14} {'hors échantillon':>18}")
    for f in folds:
        print(f"  {f['fold']:>4} {f['train_n']:>6} {f['test_n']:>5} "
              f"{f['sl']:>5g} {f['tp1']:>5g} {f['train_score']:>14.3f} "
              f"{f['test_score']:>18.3f}")
    gap = overfit_gap(folds)
    if gap is not None:
        print(f"\n  écart moyen : {gap:+.3f} $/trade")
        if gap > 0.5:
            print("  → POSITIF : le réglage marche mieux sur ce qu'il a vu.")
            print("    Surapprentissage. Ne pas appliquer ces valeurs.")
        else:
            print("  → négatif ou nul : pas de surapprentissage détecté.")
    choisis = Counter((f["sl"], f["tp1"]) for f in folds).most_common(1)
    if choisis and choisis[0][1] > 1:
        (sl, tp1), n = choisis[0]
        print(f"  → {n}/{len(folds)} plis convergent sur SL {sl:g} / TP1 +{tp1:g}")
    print("\n  RÉSERVE : ~5 trades par tranche de test. Indicatif, pas démontré.")


def section_cout() -> None:
    _titre("4. COÛT EN REQUÊTES — ce que chaque bras ajoute vraiment")
    total = sum(COUT_PAR_CYCLE.values())
    for label, cout in COUT_PAR_CYCLE.items():
        print(f"  {label:>42} {cout:>4}")
    print(f"  {'TOTAL par cycle':>42} {total:>4}")
    par_jour = total * (86400 // 90)
    print(f"\n  {86400 // 90} cycles/jour → ~{par_jour:,} requêtes/jour".replace(",", " "))
    print(
        f"\n  Un bras SUPPLÉMENTAIRE coûte 0 requête : la collecte est partagée\n"
        f"  et `evaluate()` est du CPU pur. Ce qui coûte, c'est le nombre de\n"
        f"  POSITIONS ouvertes (monitoring) et de tentatives d'entrée (devis)."
    )
    print(
        "\n  Plafonds : DexScreener 270/min effectifs · Jupiter 60/min\n"
        "  · RugCheck 60/min · Birdeye 60/min ET quota CU mensuel épuisé"
    )


def section_correlation() -> None:
    _titre("5. CORRÉLATION ENTRE BRAS — la diversification est-elle réelle ?")
    tokens_par_bras = {}
    for nom in arms():
        pos = positions_of(nom)
        if pos:
            tokens_par_bras[nom] = {p.get("token_address") for p in pos}

    actifs = [n for n, t in tokens_par_bras.items() if t]
    if len(actifs) < 2:
        print(
            f"  {len(actifs)} bras avec des trades — la corrélation demande au\n"
            f"  moins deux bras actifs. À relancer quand les autres auront tradé."
        )
        return

    print(f"  {'':>10} " + " ".join(f"{n[:9]:>10}" for n in actifs))
    for a in actifs:
        cells = []
        for b in actifs:
            if a == b:
                cells.append(f"{'—':>10}")
                continue
            commun = len(tokens_par_bras[a] & tokens_par_bras[b])
            union = len(tokens_par_bras[a] | tokens_par_bras[b])
            cells.append(f"{100 * commun / union if union else 0:>9.0f}%")
        print(f"  {a:>10} " + " ".join(cells))
    print(
        "\n  Recouvrement de tokens (Jaccard). Un taux élevé signifie que les\n"
        "  bras tiennent les MÊMES positions : le drawdown agrégé sera bien\n"
        "  pire que celui de chaque bras pris isolément."
    )


def section_justesse() -> None:
    _titre("6. JUSTESSE DES BRAS — leurs verdicts étaient-ils bons ?")
    from src.core.funnel import read_funnel
    from src.core.scoreboard import build_from_funnel

    # Issue connue par token : le shadow dit ce que sont devenus les rejets,
    # le journal ce que sont devenus les trades pris.
    outcomes: dict[str, bool] = {}
    for nom in arms():
        for row in _read_jsonl(settings.arm_paths(nom)["shadow"]):
            if row.get("token_address"):
                outcomes[row["token_address"]] = bool(row.get("would_have_won"))
        for pos in positions_of(nom):
            if pos.get("token_address"):
                outcomes[pos["token_address"]] = (pos.get("pnl_usd") or 0) > 0

    if not outcomes:
        print("  Aucun token n'a encore d'issue connue (shadow 4 h, ou trade clos).")
        return

    board = build_from_funnel(read_funnel(settings.FUNNEL_LOG_PATH), outcomes)
    scores = board.scores()
    if not scores:
        print(f"  {len(outcomes)} tokens jugés, mais aucun ne croise l'entonnoir.")
        return

    print(f"  {'bras':>10} {'n':>4} {'précision':>10} {'spécificité':>12} "
          f"{'utilité':>8}  verdict")
    for score in scores:
        precision = f"{score.precision:.0f}%" if score.precision is not None else "—"
        specificity = f"{score.specificity:.0f}%" if score.specificity is not None else "—"
        utilite = f"{score.usefulness:.0f}" if score.usefulness is not None else "—"
        print(f"  {score.agent:>10} {score.sample:>4} {precision:>10} "
              f"{specificity:>12} {utilite:>8}  {score.verdict}")

    allowed, why = board.weights_allowed(sum(len(positions_of(n)) for n in arms()))
    print(f"\n  Pondération automatique : {'autorisée' if allowed else 'bloquée'} — {why}")
    print(
        "  précision = a-t-il raison quand il dit OUI · spécificité = quand il\n"
        "  dit NON. Un bras qui refuse tout a 100% de spécificité et zéro\n"
        "  utilité : c'est ce déséquilibre que la colonne « utilité » révèle."
    )
    print(
        "\n  ⚠ BIAIS À CONNAÎTRE. Les deux colonnes ne mesurent pas la même\n"
        "  chose. La spécificité vient du shadow tracker, dont le critère est\n"
        "  « le token a-t-il fait +100% » — presque jamais vrai, d'où des 100%\n"
        "  mécaniques. La précision vient des trades clos, avec le critère\n"
        "  « le trade a-t-il gagné ». Tant que les deux critères diffèrent,\n"
        "  l'utilité à 50 signifie « pas mesurable », pas « médiocre »."
    )


def section_criteres() -> None:
    _titre("7. CRITÈRES DE RÉSOLUTION — quand saura-t-on ?")
    criteres = [
        ("élargir scalp à 4h",
         "le motif « âge > 2h » tombe sous 30% de ses rejets, ET scalp qualifie ≥1/cycle"),
        ("garder le bras consensus",
         "≥2 bras votants qualifient le même token au moins 5 fois en 24h"),
        ("appliquer SL -15 / TP1 +150",
         "l'écart walk-forward reste ≤0 sur 5 plis avec ≥10 trades par tranche"),
        ("suivi de wallets étape 2",
         "≥30 wallets profilés ET avance médiane >10 min (actuellement +0,4 à +3,6)"),
        ("abonnement Birdeye 39$",
         "GMGN kline échoue sur >20% des candidats — sinon le gratuit suffit"),
        ("passer un bras en LIVE",
         "verrou propriétaire + 20 trades + IC95 du P&L/trade entièrement >0"),
    ]
    for decision, critere in criteres:
        print(f"  ▸ {decision}")
        print(f"    → {critere}\n")
    print(
        "  Une décision sans critère de résolution reste ouverte indéfiniment :\n"
        "  chacun a le sien, personne ne tranche."
    )


def main() -> None:
    print(f"\n{'═' * 64}")
    print("  AUDIT STATISTIQUE — CryptobBot")
    print(f"{'═' * 64}")
    section_intervalles()
    section_comparaison()
    section_walk_forward()
    section_cout()
    section_correlation()
    section_justesse()
    section_criteres()
    print()


if __name__ == "__main__":
    main()
