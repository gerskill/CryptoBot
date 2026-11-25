"""Changement de saison et corrections d'issues, pour les bras de prédiction.

Deux opérations ponctuelles, faites service ARRÊTÉ, jamais par le cycle :

  - `archive_season` : range la saison en cours (paris réglés ET ouverts,
    diagnostics d'apprentissage, état) dans `archive/<nom>/`. Rien n'est
    supprimé. Les paris encore ouverts y finissent grâce à
    `saisons_a_terminer` (runner) ; la nouvelle saison repart de comptes neufs.
  - `apply_corrections` : remplace l'issue d'un pari « compté perdu par
    prudence » par son issue VÉRIFIÉE (source écrite dans la ligne). Une copie
    du journal est gardée avant toute réécriture.
"""

import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Optional

from src.prediction.paper import net_pnl, read_json, read_jsonl

# Fichiers propres à une saison. Les données de marché (prix, catalogue,
# instantanés, incohérences, perpétuels) sont communes et restent en place.
SEASON_DIRS = ("arms", "learning")
SEASON_FILES = ("state.json", "marks.json", "arms_local.json", "requests_log.jsonl")


def archive_season(data_dir: str, name: str, capital: float,
                   now: Optional[datetime] = None, apply: bool = False) -> dict[str, Any]:
    """Prépare (apply=False) ou fait (apply=True) l'archivage de la saison."""
    now = now or datetime.now(timezone.utc)
    if not name or "/" in name or name.startswith("."):
        raise ValueError(f"nom de saison invalide : {name!r}")
    target = os.path.join(data_dir, "archive", name)
    if os.path.exists(target):
        raise FileExistsError(f"saison déjà archivée : {target}")
    arms_dir = os.path.join(data_dir, "arms")
    resume: dict[str, Any] = {"saison": name, "capital_fictif_usd": capital,
                              "archivee_le": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
                              "bras": {}}
    if os.path.isdir(arms_dir):
        for arm in sorted(os.listdir(arms_dir)):
            folder = os.path.join(arms_dir, arm)
            if not os.path.isdir(folder):
                continue
            settled = read_jsonl(os.path.join(folder, "bets.jsonl"))
            opened = read_json(os.path.join(folder, "open_bets.json"), [])
            resume["bras"][arm] = {
                "regles": sum(1 for b in settled if b.get("status") == "regle"),
                "ouverts": len(opened),
                "resultat_regle": round(sum(float(b.get("pnl_net") or 0) for b in settled
                                            if b.get("status") == "regle"), 4),
            }
    moves = [d for d in SEASON_DIRS if os.path.isdir(os.path.join(data_dir, d))]
    copies = [f for f in SEASON_FILES if os.path.isfile(os.path.join(data_dir, f))]
    resume["deplaces"], resume["copies"] = moves, copies
    if not apply:
        return dict(resume, applique=False)
    os.makedirs(target)
    for folder in moves:
        shutil.move(os.path.join(data_dir, folder), os.path.join(target, folder))
    for fname in copies:
        shutil.copy2(os.path.join(data_dir, fname), os.path.join(target, fname))
    # Les forks créés depuis le site (arms_local.json) appartiennent à la
    # saison archivée : la nouvelle repart de la configuration de base.
    local = os.path.join(data_dir, "arms_local.json")
    if os.path.isfile(local):
        os.remove(local)
    marks = os.path.join(data_dir, "marks.json")
    if os.path.isfile(marks):
        os.remove(marks)
    os.makedirs(arms_dir, exist_ok=True)
    with open(os.path.join(target, "LISEZMOI.json"), "w", encoding="utf-8") as fh:
        json.dump(resume, fh, ensure_ascii=False, indent=2)
    return dict(resume, applique=True)


def apply_corrections(data_dir: str, corrections: list[dict[str, Any]], fee_pct: float,
                      settle_fee_pct: float, now: Optional[datetime] = None,
                      apply: bool = False) -> list[dict[str, Any]]:
    """Applique des issues vérifiées à des paris déjà réglés.

    Chaque correction : {"id", "valeur_cote" (1 = le côté parié a gagné,
    0 = perdu), "source"}. Le P&L est recalculé avec les frais de la config,
    exactement comme au règlement normal. Une correction dont l'id est
    introuvable est signalée, jamais inventée.
    """
    now = now or datetime.now(timezone.utc)
    stamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    wanted = {c["id"]: c for c in corrections}
    report: list[dict[str, Any]] = []
    found: set[str] = set()
    arms_dir = os.path.join(data_dir, "arms")
    for arm in sorted(os.listdir(arms_dir)) if os.path.isdir(arms_dir) else []:
        path = os.path.join(arms_dir, arm, "bets.jsonl")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
        changed, out = False, []
        for line in lines:
            try:
                bet = json.loads(line)
            except json.JSONDecodeError:
                out.append(line)
                continue
            fix = wanted.get(bet.get("id"))
            if fix is None or bet.get("status") != "regle":
                out.append(line)
                continue
            found.add(bet["id"])
            value = float(fix["valeur_cote"])
            pnl = round(net_pnl(float(bet["stake_usd"]), float(bet["shares"]), value,
                                fee_pct, settle_fee_pct), 6)
            before = float(bet.get("pnl_net") or 0)
            new = dict(bet, settle_value=value, pnl_net=pnl, won=value == 1.0,
                       regle_par_prudence=False, corrige_apres_prudence=True,
                       issue_verifiee=fix["source"], corrige_le=stamp,
                       pnl_avant_correction=before,
                       motif_reglement_avant=bet.get("motif_reglement"))
            new.pop("motif_reglement", None)
            report.append({"id": bet["id"], "bras": arm, "titre": bet.get("title"),
                           "cote": bet.get("side_name"), "prix": bet.get("entry_price"),
                           "pnl_avant": round(before, 4), "pnl_apres": round(pnl, 4),
                           "effet": round(pnl - before, 4), "source": fix["source"]})
            out.append(json.dumps(new, ensure_ascii=False) + "\n")
            changed = True
        if changed and apply:
            shutil.copy2(path, f"{path}.avant-correction-{now.strftime('%Y%m%d-%H%M%S')}")
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(out)
            os.replace(tmp, path)
    for missing in sorted(set(wanted) - found):
        report.append({"id": missing, "introuvable": True})
    return report
