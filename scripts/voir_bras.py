#!/usr/bin/env python3
"""Inspecter un bras SANS jeton : lecture directe des fichiers, pas de l'API.

POURQUOI CE SCRIPT. `/api/params` exige `DASHBOARD_TOKEN` dès qu'il est
défini dans `.env` — et il l'est depuis que le site passe par un tunnel
Cloudflare. C'est voulu : cet endpoint rend la stratégie complète.

La mauvaise solution serait « pas de jeton pour les requêtes venant de
127.0.0.1 » : derrière un tunnel (`cloudflared`, ngrok, Tailscale Funnel),
TOUTES les requêtes extérieures arrivent depuis 127.0.0.1. Ce contournement
ouvrirait la stratégie au monde entier.

Ici, rien n'écoute sur le réseau : on lit `config/arms/<bras>.json` (ou
`config/params.json` pour le témoin) comme le fait la boucle. Quiconque peut
lancer ce script peut déjà lire ces fichiers. Lecture seule, aucun appel API.

Usage :
  python scripts/voir_bras.py                    # liste des bras du manifeste
  python scripts/voir_bras.py quality            # règles clés + derniers ajustements
  python scripts/voir_bras.py quality --diff     # seulement ce qui diffère du témoin
  python scripts/voir_bras.py quality --perf 30d # P&L du bras sur 7d / 30d / all
  python scripts/voir_bras.py quality --json     # document complet, brut
"""

import argparse
import json
import os
import signal
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from src.core.arm import load_manifest  # noqa: E402
from src.core.journal import TradeJournal  # noqa: E402
from src.core.params import PARAMETRES_NON_APPLIQUES  # noqa: E402
from src.core.stats import window_performance  # noqa: E402

SECTIONS = ("filters", "entry_rules", "exit_rules", "scoring_weights", "risk_rules")
FENETRES = {"7d": 7, "30d": 30, "all": None}
CAPITAL_PAPIER_DEFAUT = 1000.0  # même règle que src/main.py::_starting_capital


def _lire_json(chemin: str) -> dict:
    try:
        with open(chemin, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def resoudre_bras(nom: str) -> dict:
    """Entrée du manifeste, ou erreur. Jamais un chemin construit depuis
    l'argument tel quel : `../../.env` ne doit rien ouvrir."""
    for entry in load_manifest():
        if entry["name"] == nom:
            return entry
    connus = ", ".join(e["name"] for e in load_manifest())
    raise SystemExit(f"Bras inconnu : {nom}. Connus : {connus}")


def parametres(nom: str) -> dict:
    return _lire_json(settings.arm_paths(nom)["params"])


def aplatir(document: dict) -> dict:
    """`{"exit_rules": {"stop_loss_pct": 20}}` -> `{"exit_rules.stop_loss_pct": 20}`,
    limité aux sections qui décident d'une entrée ou d'une sortie. Les clés
    `_note…` sont des commentaires, pas des règles."""
    plat = {}
    for section in SECTIONS:
        for cle, valeur in (document.get(section) or {}).items():
            if not cle.startswith("_"):
                plat[f"{section}.{cle}"] = valeur
    return plat


def differences(nom: str) -> dict:
    """Règles du bras qui diffèrent du témoin : `{clé: (témoin, bras)}`."""
    temoin = aplatir(parametres(settings.BASELINE_ARM))
    bras = aplatir(parametres(nom))
    return {
        cle: (temoin.get(cle), bras.get(cle))
        for cle in sorted(set(temoin) | set(bras))
        if temoin.get(cle) != bras.get(cle)
    }


def performance(nom: str, fenetre: str) -> dict:
    chemin = settings.arm_paths(nom)["trades"]
    positions = TradeJournal(chemin).read_positions() if os.path.exists(chemin) else []
    positions.sort(key=lambda r: r.get("timestamp_exit") or "")
    jours = FENETRES[fenetre]
    depuis = datetime.now(timezone.utc) - timedelta(days=jours) if jours else None
    capital = float(_lire_json(settings.PARAMS_PATH).get("capital_total") or 0)
    return window_performance(positions, capital if capital > 0 else CAPITAL_PAPIER_DEFAUT, depuis)


def _resume_valeur(avant, apres) -> str:
    """Un ajustement de section entière stocke tout le dictionnaire (plus
    de 2 000 caractères) : on n'affiche que les clés qui ont bougé."""
    if isinstance(avant, dict) and isinstance(apres, dict):
        bouge = [
            f"{k} {avant.get(k)}→{apres.get(k)}"
            for k in sorted(set(avant) | set(apres))
            if not k.startswith("_") and avant.get(k) != apres.get(k)
        ]
        return "; ".join(bouge) or "(aucune clé modifiée)"
    return f"{avant} → {apres}"


def annotation(cle_pointee: str) -> str:
    """Suffixe d'affichage : signale un paramètre déclaré que le code ne lit pas."""
    raison = PARAMETRES_NON_APPLIQUES.get(cle_pointee)
    return f"  (non appliqué : {raison})" if raison else ""


def _afficher_liste() -> None:
    print(f"{'Bras':<16}{'Actif':<7}{'Rôle':<12}Description")
    print("─" * 78)
    for entry in load_manifest():
        actif = "oui" if entry.get("enabled", True) else "non"
        description = (entry.get("description") or "")[:42]
        print(f"{entry['name']:<16}{actif:<7}{str(entry.get('role') or '—'):<12}{description}")


def _afficher_bras(nom: str, entry: dict, historique: int) -> None:
    document = parametres(nom)
    print(f"\n{nom} — {'actif' if entry.get('enabled', True) else 'DÉSACTIVÉ'}"
          f" — rôle {entry.get('role') or '—'}")
    if entry.get("description"):
        print(f"  {entry['description']}")
    # Les surcharges ne servent qu'à CRÉER le fichier du bras
    # (`materialise_arm_params`) ; l'apprentissage fait ensuite bouger les
    # valeurs. Les deux blocs peuvent donc différer : c'est le second qui joue.
    for titre, cle in (
        ("Surcharges du manifeste (graine à la création — PAS forcément en vigueur)", "overrides"),
        ("Bornes d'apprentissage", "bounds"),
    ):
        if entry.get(cle):
            print(f"\n{titre} :")
            for k, v in sorted(entry[cle].items()):
                print(f"  {k:<44}{v}")
    print("\nValeurs EN VIGUEUR (fichier du bras) :")
    plat = aplatir(document)
    section_courante = None
    for cle, valeur in plat.items():
        section = cle.split(".", 1)[0]
        if section != section_courante:
            print(f"\n{section} :")
            section_courante = section
        print(f"  {cle.split('.', 1)[1]:<42}{valeur}{annotation(cle)}")
    ajustements = (document.get("learning") or {}).get("parameter_adjustment_history") or []
    print(f"\nDerniers ajustements ({min(historique, len(ajustements))} sur {len(ajustements)}) :")
    for a in list(reversed(ajustements))[:historique]:
        print(f"  {str(a.get('timestamp', '?'))[:16]}  {a.get('param_name', '?'):<36}"
              f"{_resume_valeur(a.get('old_value'), a.get('new_value'))}  "
              f"{str(a.get('reason', ''))[:50]}")


def _afficher_perf(nom: str, fenetre: str) -> None:
    p = performance(nom, fenetre)
    pf = p["profit_factor"]
    pf_txt = ("∞" if p["gross_win"] else "—") if pf is None else f"{pf:.2f}"
    ic = p["win_rate_interval"]
    ic_txt = f" [{ic['low']:.0f}–{ic['high']:.0f}]" if ic else ""
    print(f"\n{nom} — fenêtre {fenetre} — PAPIER, coûts de sortie inclus")
    print(f"  trades {p['trades']}  WR {p['win_rate']:.1f}%{ic_txt}  PF {pf_txt}  "
          f"P&L {p['pnl_usd']:+.2f} $  drawdown -{p['max_drawdown_pct']:.1f}%")
    for jour in p["daily"]:
        print(f"    {jour['date']}  {jour['trades']:>3} trade(s)  {jour['pnl_usd']:+9.2f} $")
    if p["trades"] < 30:
        print("  ⚠ moins de 30 trades : aucune conclusion possible sur ce bras.")


def main() -> None:
    # `voir_bras.py quality | head` ne doit pas finir sur un BrokenPipeError.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bras", nargs="?", help="nom du bras (sans argument : liste)")
    parser.add_argument("--diff", action="store_true", help="seulement les écarts avec le témoin")
    parser.add_argument("--json", action="store_true", help="document de paramètres complet")
    parser.add_argument("--perf", choices=sorted(FENETRES), help="P&L du bras sur la fenêtre")
    parser.add_argument("--historique", type=int, default=10, help="ajustements affichés (défaut 10)")
    args = parser.parse_args()

    if not args.bras:
        _afficher_liste()
        return
    entry = resoudre_bras(args.bras)
    if args.json:
        print(json.dumps(parametres(args.bras), indent=2, ensure_ascii=False))
    elif args.diff:
        ecarts = differences(args.bras)
        if not ecarts:
            print(f"{args.bras} : aucune règle ne diffère de {settings.BASELINE_ARM}.")
        for cle, (temoin, bras) in ecarts.items():
            print(f"  {cle:<46}{temoin!s:>10} → {bras}")
    else:
        _afficher_bras(args.bras, entry, args.historique)
    if args.perf:
        _afficher_perf(args.bras, args.perf)


if __name__ == "__main__":
    main()
