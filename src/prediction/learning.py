"""Agent d'apprentissage des bras de prédiction — un diagnostic par bras.

Ce qu'il fait, à chaque passage (au plus une fois par `intervalle_minutes`) :
  1. MESURE chaque bras sur ses paris réglés : résultat par tranche de prix
     payé, par domaine, par espérance au carnet. Écrit `learning/<bras>.json`.
  2. PROPOSE, quand un segment le justifie, un fork qui ne change qu'UNE
     variable, dans les bornes de `overlay.py`. La proposition passe par la
     boîte de demandes, comme une demande du site : la simulation la valide
     et l'applique au cycle suivant.

Ce qu'il ne fait JAMAIS (règles du projet, CLAUDE.md et ADR 017/018) :
  - modifier un bras existant : ses paris déjà pris ne seraient plus
    comparables. Le parent continue tel quel, le fork démarre à zéro ;
  - conclure sous `min_segment` paris (10) dans le segment, ni sous
    `min_bras` paris réglés (20) pour le bras ;
  - relâcher : il ne sait que RESSERRER (fourchette de prix plus étroite,
    espérance minimale plus haute). Élargir ce qui perd n'est pas apprendre ;
  - activer ou désactiver un bras : c'est la décision de Killian.

Un fork créé ici est un bras de test comme un autre : son propre compte de
46 $, ses propres paris, son verdict au bout de `min_paris_verdict` paris.
"""

import os
from datetime import datetime, timezone
from typing import Any, Optional

from src.prediction import overlay
from src.prediction.paper import (TRANCHES_PRIX, BetStore, _write_atomic, append_jsonl,
                                  read_json, read_jsonl)

ROLES = ("temoin", "regle", "parieur")
SEUILS_EDGE = (0.03, 0.05, 0.08, 0.10, 0.15, 0.20)


def learning_dir(data_dir: str) -> str:
    return os.path.join(data_dir, "learning")


def log_path(data_dir: str) -> str:
    return os.path.join(learning_dir(data_dir), "learning_log.jsonl")


def read_diagnostic(data_dir: str, arm: str) -> Optional[dict[str, Any]]:
    """Dernier diagnostic d'un bras, résumé pour le site. None s'il n'existe pas."""
    data = read_json(os.path.join(learning_dir(data_dir), f"{arm}.json"), None)
    if not isinstance(data, dict):
        return None
    return {k: data.get(k) for k in ("t", "paris_regles", "decision", "proposition", "fork_demande")}


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _segment(bets: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(bets)
    pnl = sum(float(b["pnl_net"]) for b in bets)
    return {"paris": n, "pnl_net": round(pnl, 4),
            "pnl_moyen": round(pnl / n, 4) if n else None,
            "part_gagnee": round(sum(1 for b in bets if b.get("won")) / n, 4) if n else None,
            "prix_moyen": round(sum(float(b.get("entry_price") or 0) for b in bets) / n, 4) if n else None}


def diagnose(arm: dict[str, Any], decided: list[dict[str, Any]]) -> dict[str, Any]:
    """Mesures d'un bras. Aucun jugement ici, seulement des comptes."""
    by_band = []
    for low, high in TRANCHES_PRIX:
        band = [b for b in decided if low <= float(b.get("entry_price") or 0) < high]
        by_band.append(dict(_segment(band), tranche=[low, min(high, 1.0)]))
    by_domain: dict[str, Any] = {}
    for domain in sorted({b.get("domain") or "inconnu" for b in decided}):
        by_domain[domain] = _segment([b for b in decided if (b.get("domain") or "inconnu") == domain])
    with_edge = [b for b in decided if b.get("edge_au_carnet") is not None]
    by_edge = []
    for threshold in SEUILS_EDGE:
        below = [b for b in with_edge if float(b["edge_au_carnet"]) < threshold]
        by_edge.append({"seuil": threshold, "sous": _segment(below),
                        "au_dessus": _segment([b for b in with_edge if b not in below])})
    return {"bras": arm["name"], "role": arm.get("role"), "paris_regles": len(decided),
            "total": _segment(decided), "par_tranche": by_band, "par_domaine": by_domain,
            "par_edge": by_edge}


def propose(arm: dict[str, Any], decided: list[dict[str, Any]],
            min_segment: int) -> Optional[dict[str, Any]]:
    """Une proposition de fork (une variable, resserrée), ou None.

    Règle « fourchette » (témoin, règle) : si la tranche de prix au BORD de la
    fourchette du bras perd de l'argent sur au moins `min_segment` paris, et
    qu'il reste au moins `min_segment` paris hors de cette tranche, proposer
    la fourchette sans cette tranche.
    Règle « edge_min » (parieur) : si les paris pris sous un seuil d'espérance
    perdent sur au moins `min_segment` paris, et que ceux au-dessus font mieux
    en moyenne sur au moins `min_segment` paris, proposer ce seuil.
    """
    role = arm.get("role")
    if role in ("temoin", "regle"):
        low, high = float(arm.get("prix_min", 0.5)), float(arm.get("prix_max", 0.95))
        candidates = []
        for band_low, band_high in TRANCHES_PRIX:
            inside_low, inside_high = max(band_low, low), min(band_high, high)
            if inside_low >= inside_high:
                continue
            segment = [b for b in decided if inside_low <= float(b.get("entry_price") or 0) < inside_high]
            rest = [b for b in decided if b not in segment]
            stats = _segment(segment)
            if stats["paris"] < min_segment or len(rest) < min_segment or stats["pnl_net"] >= 0:
                continue
            if inside_low <= low + 1e-9 and band_high < high:      # bord bas
                new = {"prix_min": round(band_high, 4), "prix_max": high}
            elif inside_high >= high - 1e-9 and band_low > low:    # bord haut
                new = {"prix_min": low, "prix_max": round(band_low, 4)}
            else:
                continue  # tranche au milieu : la retirer couperait la fourchette en deux
            candidates.append((stats["pnl_net"], new, stats, [inside_low, inside_high]))
        if not candidates:
            return None
        pnl, new, stats, band = min(candidates, key=lambda c: c[0])
        return {"variable": "fourchette", "value": new, "segment": stats,
                "motif": (f"tranche de prix {band[0]:g}–{band[1]:g} : {stats['paris']} paris, "
                          f"{stats['pnl_net']:+.2f} $ nets ; fourchette resserrée à "
                          f"{new['prix_min']:g}–{new['prix_max']:g}")}
    if role == "parieur":
        current = float(arm.get("edge_min", 0.05))
        low_bound, high_bound = overlay.BOUNDS["edge_min"]
        with_edge = [b for b in decided if b.get("edge_au_carnet") is not None]
        for threshold in SEUILS_EDGE:
            if threshold <= current or not low_bound <= threshold <= high_bound:
                continue
            below = [b for b in with_edge if float(b["edge_au_carnet"]) < threshold]
            above = [b for b in with_edge if b not in below]
            s_below, s_above = _segment(below), _segment(above)
            if (s_below["paris"] >= min_segment and s_above["paris"] >= min_segment
                    and s_below["pnl_net"] < 0 and s_above["pnl_moyen"] > s_below["pnl_moyen"]):
                return {"variable": "edge_min", "value": threshold, "segment": s_below,
                        "motif": (f"paris d'espérance < {threshold:g} : {s_below['paris']} paris, "
                                  f"{s_below['pnl_net']:+.2f} $ nets ; ceux au-dessus font "
                                  f"{s_above['pnl_moyen']:+.2f} $ par pari")}
    return None


def _fork_name(parent: str, taken: set[str]) -> Optional[str]:
    base = parent[3:] if parent.startswith("pm_") else parent
    for index in range(1, 100):
        name = f"pm_{base[:22]}_a{index}"
        if name not in taken and overlay.NAME_PATTERN.match(name):
            return name
    return None


def run(config: dict[str, Any], data_dir: str, moment: datetime) -> dict[str, Any]:
    """Un passage de l'agent. Renvoie un résumé pour `state.json`."""
    settings_ = config.get("apprentissage") or {}
    if not settings_.get("actif"):
        return {"actif": False}
    every = float(settings_.get("intervalle_minutes", 60))
    state_path = os.path.join(learning_dir(data_dir), "etat.json")
    last = float(read_json(state_path, {}).get("dernier", 0))
    if moment.timestamp() - last < every * 60:
        return {"actif": True, "prochain_passage_dans_min": round((last + every * 60 - moment.timestamp()) / 60, 1)}

    min_segment = int(settings_.get("min_segment", 10))
    min_bras = int(settings_.get("min_bras", 20))
    max_par_jour = int(settings_.get("max_forks_par_bras_par_jour", 1))
    max_total = int(settings_.get("max_forks_total", 10))
    arms = config["arms"]
    names = {a.get("name") for a in arms}
    log = read_jsonl(log_path(data_dir))
    submitted = [row for row in log if row.get("demande")]
    pending = {r.get("name") for r in read_jsonl(overlay.requests_path(data_dir))
               if r.get("origine") == "apprentissage"}
    today = moment.strftime("%Y-%m-%d")
    learned_total = sum(1 for a in arms if a.get("origine") == "apprentissage")
    measured, asked = 0, 0

    for arm in arms:
        if arm.get("role") not in ROLES or not arm.get("enabled", True):
            continue
        # Les paris pris hors de la règle du bras (défaut corrigé) ne mesurent
        # pas la règle : ni dans le seuil de 20, ni dans les segments.
        decided = [b for b in BetStore(data_dir, arm["name"]).settled()
                   if b.get("status") == "regle" and b.get("pnl_net") is not None
                   and not b.get("hors_regle")]
        diag = diagnose(arm, decided)
        diag.update(t=_iso(moment), proposition=None, fork_demande=None)
        measured += 1
        if len(decided) < min_bras:
            diag["decision"] = f"pas assez de données : {len(decided)} paris réglés sur {min_bras} requis"
        elif arm.get("origine") == "apprentissage" and not settings_.get("forks_de_forks", False):
            diag["decision"] = "fork de l'agent : mesuré, pas re-forké (pas de fork de fork)"
        else:
            proposal = propose(arm, decided, min_segment)
            diag["proposition"] = proposal
            if proposal is None:
                diag["decision"] = "rien à proposer : aucun segment perdant d'au moins " \
                                   f"{min_segment} paris au bord des réglages"
            else:
                mine = [r for r in submitted if r["demande"].get("parent") == arm["name"]]
                already = any(r["demande"].get("variable") == proposal["variable"]
                              and r["demande"].get("value") == proposal["value"] for r in mine)
                today_count = sum(1 for r in mine if str(r.get("t", "")).startswith(today))
                if already:
                    diag["decision"] = "proposition déjà faite : on attend le verdict du fork"
                elif today_count >= max_par_jour:
                    diag["decision"] = f"plafond de {max_par_jour} fork(s) par jour atteint pour ce bras"
                elif learned_total + asked >= max_total:
                    diag["decision"] = f"plafond de {max_total} forks de l'agent atteint"
                else:
                    name = _fork_name(arm["name"], names | pending)
                    request = {"id": f"apprentissage-{arm['name']}-{int(moment.timestamp())}",
                               "t": _iso(moment), "action": "fork", "parent": arm["name"],
                               "name": name, "variable": proposal["variable"],
                               "value": proposal["value"], "origine": "apprentissage",
                               "motif": proposal["motif"]}
                    fork, reason = overlay.build_fork(arms, request, moment) if name else (None, "nom_indisponible")
                    if fork is None:
                        diag["decision"] = f"proposition refusée par les bornes : {reason}"
                        append_jsonl(log_path(data_dir), {"t": _iso(moment), "bras": arm["name"],
                                                          "refus": reason, "proposition": proposal})
                    else:
                        append_jsonl(overlay.requests_path(data_dir), request)
                        append_jsonl(log_path(data_dir), {"t": _iso(moment), "bras": arm["name"],
                                                          "demande": request})
                        names.add(name)
                        asked += 1
                        diag["fork_demande"] = name
                        diag["decision"] = f"fork {name} demandé : {proposal['motif']}"
        _write_atomic(os.path.join(learning_dir(data_dir), f"{arm['name']}.json"), diag)

    _write_atomic(state_path, {"dernier": moment.timestamp(), "t": _iso(moment)})
    return {"actif": True, "bras_mesures": measured, "forks_demandes": asked}
