#!/usr/bin/env python3
"""Familles d'entrée (2026-11-23) : met à jour config/strategies.json.

  - ajoute les 8 bras `entree_*` (même sortie de référence figée, mise fixe
    1 %, apprentissage coupé, 3 positions max, aucune dépendance Helius) ;
  - met EN PAUSE les 7 doublons (rien n'est supprimé : journaux et historique
    restent, leurs positions ouvertes vont jusqu'à leur sortie normale) ;
  - donne à chaque bras un rôle lisible pour le tableau de bord.

Idempotent : relancer ne change rien. Sans `--appliquer`, affiche seulement.
Lit `config/arms/mature_liquid.json` pour copier les filtres du lot « mature ».
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFESTE = os.path.join(ROOT, "config", "strategies.json")
MATURE = os.path.join(ROOT, "config", "arms", "mature_liquid.json")

# Sortie de référence FIGÉE, identique pour les 8 familles : stop -25 % (sans
# tampon), vente TOTALE à +30 % (TP3 = sortie finale, TP1/TP2 au même niveau
# ne se déclenchent jamais avant), 4 h max, ni breakeven ni trailing.
SORTIE_REFERENCE = {
    "stop_loss_pct": -25.0,
    "stop_loss_slippage_buffer_pct": 0.0,
    "take_profit_1": 30.0,
    "take_profit_2": 30.0,
    "take_profit_3": 30.0,
    "partial_sell_tp1_pct": 1.0,
    "partial_sell_tp2_pct": 1.0,
    "breakeven_trigger": 0,
    "breakeven_stop_exact": False,
    "trailing_stop_activation": 1000.0,
    "trailing_stop_distance_pct": 80.0,
    "max_hold_time_minutes": 240,
}

# Filtres COMMUNS (sécurité minimale identique) : seule la porte de famille
# change d'un bras à l'autre. Tous dans l'enveloppe de découverte actuelle,
# donc les autres bras voient exactement le même lot.
FILTRES_COMMUNS = {
    "min_liquidity_usd": 40000,
    "min_volume_1h": 2000,
    "min_age_hours": 0.5,
    "max_age_hours": 20.5,
    "min_holders": 25,
    "min_rugcheck_score": 70,
    "max_top_wallet_concentration": 40,
    "max_dev_wallet_pct": 10,
    "min_lp_locked_pct": 50,
    "min_smart_money_buys_30min": 0,
    "min_social_mentions_1h": 0,
    "reject_wash_trading": True,
}

MEMBRES_CONSENSUS = [
    "entree_score", "entree_securite", "entree_wallets", "entree_acheteurs", "entree_repli",
]

EN_PAUSE = {
    "sniper_ar": "En pause · doublon de sniper (81 % des mêmes achats)",
    "sniper_young": "En pause · doublon de sniper (72 % des mêmes achats)",
    "tp1_20": "En pause · doublon de tp1_30 (75 % des mêmes achats)",
    "tp1_20_vl2": "En pause · doublon de tp1_30 (72 % des mêmes achats)",
    "score_v2": "En pause · doublon de scaled_be (87 % des mêmes achats)",
    "anti_rug": "En pause · doublon (70 % des achats de score_v2, dépend de Helius saturé)",
    "consensus_af": "En pause · doublon de sniper_ar / sniper (65-68 % des mêmes achats)",
}

ROLES = {
    "baseline": "Témoin historique · entrée score + technique, sortie qui apprend",
    "sniper": "Stratégie complète · jeunes tokens, sortie qui apprend",
    "scalp": "Stratégie complète · petits gains rapides",
    "runner": "Stratégie complète · laisser courir (paliers)",
    "quality": "Stratégie complète · sélection stricte",
    "tp1_15": "Sortie testée · TP1 +15 % puis stop au prix d'entrée",
    "tp1_30": "Sortie testée · TP1 +30 % puis stop au prix d'entrée",
    "scaled_be": "Sortie testée · entrée progressive + breakeven",
    "fixes3": "Sortie testée · TP1 +15 % + refus frais > 3 % et pump 5 min",
    "mature_liquid": "Stratégie complète · tokens matures (sortie du témoin)",
    "copy_smart": "Stratégie complète · copie de smart wallets",
    "consensus": "Consensus des bras votants",
}


def _famille(nom, famille, description, entry_extra=None, overrides_extra=None, **manifest_extra):
    entry_rules = {"famille": famille, "taille_fixe": True, "sans_technique": True}
    entry_rules.update(entry_extra or {})
    overrides = {
        "risk_per_trade": 0.01,
        "max_concurrent_positions": 3,
        "risk_rules.max_concurrent_positions": 3,
        "scan.alpha_score_entry_threshold": 0,
        "filters": copy.deepcopy(FILTRES_COMMUNS),
        "exit_rules": copy.deepcopy(SORTIE_REFERENCE),
        "entry_rules": entry_rules,
    }
    overrides.update(overrides_extra or {})
    from_libelles = {
        "hasard": "Famille d'entrée E0 · hasard (témoin de comparaison)",
        "score": "Famille d'entrée E1 · score alpha actuel",
        "securite": "Famille d'entrée E2 · sécurité seule",
        "wallets": "Famille d'entrée E3 · wallets suivis",
        "acheteurs": "Famille d'entrée E4 · afflux d'acheteurs et de volume",
        "repli": "Famille d'entrée E5 · rachat après une première hausse",
        "mature": "Famille d'entrée E6 · token mature et liquide",
        "consensus": "Famille d'entrée E7 · accord de plusieurs familles",
    }
    entree = {
        "name": nom,
        "role": "test",
        "role_label": from_libelles[famille],
        "enabled": True,
        "capital_pct": 0.0,
        "notify": "none",
        "learning": False,
        "social_wishlist": False,
        "description": description,
        "overrides": overrides,
    }
    entree.update(manifest_extra)
    return entree


def bras_familles(mature_doc: dict) -> list[dict]:
    filtres_mature = {
        k: v for k, v in (mature_doc.get("filters") or {}).items()
        if not k.startswith("_") and k not in ("max_tax_buy", "max_tax_sell")
    }
    source_mature = copy.deepcopy((mature_doc.get("scan") or {}).get("mature_source") or {})
    commun = ("TEST 2026-11-23 (accord Killian) : famille d'entrée. Sortie de référence "
              "figée (stop -25 %, vente totale +30 %, 4 h max, sans breakeven ni trailing), "
              "mise fixe 1 %, compte 500 $, apprentissage coupé, 3 positions max. ")
    return [
        _famille("entree_hasard", "hasard",
                 commun + "E0 HASARD : parmi les tokens qui passent la sécurité minimale commune, "
                 "un tirage au sort (20 %). Sert de point de comparaison : une famille "
                 "n'est gardée que si elle fait clairement mieux que le hasard.",
                 entry_extra={"famille_reglages": {"probabilite": 0.2}}),
        _famille("entree_score", "score",
                 commun + "E1 SCORE ACTUEL : l'entrée d'aujourd'hui (score alpha >= 55, "
                 "plafond score 75, anti-pump volume/liquidité 5, analyse technique).",
                 entry_extra={"sans_technique": False, "max_score_alpha": 75,
                              "max_volume_liquidity_ratio": 5},
                 overrides_extra={"scan.alpha_score_entry_threshold": 55}),
        _famille("entree_securite", "securite",
                 commun + "E2 SÉCURITÉ SEULE : autorités révoquées, LP verrouillée >= 90 %, "
                 "rugcheck >= 85, top10 <= 30 %, dev <= 5 %, bundlers/insiders bas. "
                 "Aucun score."),
        _famille("entree_wallets", "wallets",
                 commun + "E3 WALLETS SUIVIS : au moins 2 wallets smart money (GMGN) ont "
                 "acheté dans les 30 dernières minutes et ne revendent pas."),
        _famille("entree_acheteurs", "acheteurs",
                 commun + "E4 AFFLUX D'ACHETEURS : >= 40 achats en 5 min, >= 60 % d'acheteurs, "
                 "+1 à +10 % sur 5 min, volume 5 min >= 1,5 x la moyenne de l'heure."),
        _famille("entree_repli", "repli",
                 commun + "E5 REPLI : +20 % ou plus sur 1 h, puis recul de 0 à -15 % sur "
                 "5 min et 10 à 40 % sous le plus haut."),
        _famille("entree_mature", "mature",
                 commun + "E6 MATURE : tokens de 24 h à 30 jours, liquidité >= 250 000 $, "
                 "volume 1 h >= 20 000 $ (même lot que mature_liquid), sans score ni technique.",
                 overrides_extra={"filters": filtres_mature,
                                  "scan.mature_source": source_mature},
                 discovery="mature"),
        _famille("entree_consensus", "consensus",
                 commun + "E7 CONSENSUS : au moins 2 des familles E1 à E5 acceptent le même "
                 "token au même cycle (le hasard ne vote pas).",
                 entry_extra={"famille_reglages": {"membres": MEMBRES_CONSENSUS,
                                                   "min_accords": 2}}),
    ]


def appliquer(manifeste: dict, mature_doc: dict) -> tuple[dict, list[str]]:
    doc = copy.deepcopy(manifeste)
    bras = doc.setdefault("arms", [])
    noms = {b["name"] for b in bras}
    notes: list[str] = []
    for b in bras:
        nom = b["name"]
        if nom in EN_PAUSE:
            if not b.get("paused"):
                notes.append(f"pause : {nom}")
            b["paused"] = True
            b["role_label"] = EN_PAUSE[nom]
        elif nom in ROLES and b.get("role_label") != ROLES[nom]:
            b["role_label"] = ROLES[nom]
    for nouveau in bras_familles(mature_doc):
        if nouveau["name"] in noms:
            continue
        bras.append(nouveau)
        notes.append(f"ajout : {nouveau['name']}")
    doc["_note_familles"] = (
        "2026-11-23 : 8 bras entree_* (familles d'entrée, sortie de référence commune) "
        "et 7 doublons en pause (paused=true : plus d'entrée, positions jusqu'à leur "
        "sortie normale, historique gardé). Voir scripts/familles_entree_manifeste.py."
    )
    total = sum(float(b.get("capital_pct", 0)) for b in bras if b.get("enabled", True))
    if abs(total - 1.0) > 1e-6:
        raise SystemExit(f"capital_pct somme à {total} au lieu de 1.0 : refus")
    return doc, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appliquer", action="store_true")
    parser.add_argument("--manifeste", default=MANIFESTE)
    parser.add_argument("--mature", default=MATURE)
    args = parser.parse_args()
    with open(args.manifeste, encoding="utf-8") as fh:
        manifeste = json.load(fh)
    with open(args.mature, encoding="utf-8") as fh:
        mature_doc = json.load(fh)
    doc, notes = appliquer(manifeste, mature_doc)
    for note in notes or ["rien à changer"]:
        print(note)
    if args.appliquer:
        tmp = args.manifeste + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, args.manifeste)
        print(f"écrit : {args.manifeste}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
