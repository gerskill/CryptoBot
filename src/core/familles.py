"""Familles d'entrée (plan du 2026-11-23, accord Killian).

POURQUOI. Mesuré du 24/10 au 23/11 : presque tous les bras jugent le même lot
avec la même chaîne (filtres -> score alpha -> technique) et ne changent que
des seuils et la SORTIE. On ne pouvait donc pas savoir si c'était l'entrée ou
la sortie qui perdait. Chaque bras `entree_*` teste UNE théorie d'entrée, avec
la MÊME sortie de référence figée, la même mise (1 %) et le même compte.

Chaque porte est une fonction PURE du candidat (aucun appel réseau, aucune
donnée Helius) : les champs viennent du lot déjà enrichi (DexScreener, GMGN,
RugCheck). Une donnée absente fait REFUSER la porte quand elle est le cœur de
la théorie (ex. aucun achat de wallet suivi connu) : sinon la famille
entrerait sur des tokens qu'elle n'a pas pu juger.

Réglages par bras : `entry_rules.famille` (nom) et `entry_rules.famille_reglages`
(dict facultatif, remplace les valeurs par défaut ci-dessous).
"""

from __future__ import annotations

import random
from typing import Any, Callable, Optional

HASARD = "hasard"
SCORE = "score"
SECURITE = "securite"
WALLETS = "wallets"
ACHETEURS = "acheteurs"
REPLI = "repli"
MATURE = "mature"
CONSENSUS = "consensus"

FAMILLES = (HASARD, SCORE, SECURITE, WALLETS, ACHETEURS, REPLI, MATURE, CONSENSUS)

# Rôle lisible affiché sur le tableau de bord.
LIBELLES = {
    HASARD: "Famille d'entrée E0 · hasard (témoin de comparaison)",
    SCORE: "Famille d'entrée E1 · score alpha actuel",
    SECURITE: "Famille d'entrée E2 · sécurité seule",
    WALLETS: "Famille d'entrée E3 · wallets suivis",
    ACHETEURS: "Famille d'entrée E4 · afflux d'acheteurs et de volume",
    REPLI: "Famille d'entrée E5 · rachat après une première hausse",
    MATURE: "Famille d'entrée E6 · token mature et liquide",
    CONSENSUS: "Famille d'entrée E7 · accord de plusieurs familles",
}

DEFAUTS: dict[str, dict[str, Any]] = {
    # Un candidat qui passe les filtres communs est pris avec cette probabilité.
    HASARD: {"probabilite": 0.2},
    SCORE: {},
    SECURITE: {
        "min_lp_verrouillee_pct": 90.0,
        "min_rugcheck": 85.0,
        "max_top10_pct": 30.0,
        "max_dev_pct": 5.0,
        "max_bundler": 0.3,
        "max_insider": 0.15,
    },
    WALLETS: {"min_wallets": 2},
    ACHETEURS: {
        "min_achats_5m": 40,
        "min_part_acheteurs": 0.6,
        "min_hausse_5m": 1.0,
        "max_hausse_5m": 10.0,
        "min_acceleration_volume": 1.5,
    },
    REPLI: {
        "min_hausse_1h": 20.0,
        "min_variation_5m": -15.0,
        "max_variation_5m": 0.0,
        "min_recul_sommet": 10.0,
        "max_recul_sommet": 40.0,
        "recul_5m_si_sommet_inconnu": -3.0,
    },
    MATURE: {},
    # Bras qui doivent être d'accord, et combien au minimum.
    CONSENSUS: {"membres": [], "min_accords": 2},
}


def reglages(famille: str, entry_rules: Optional[dict[str, Any]]) -> dict[str, Any]:
    cfg = dict(DEFAUTS.get(famille, {}))
    extra = (entry_rules or {}).get("famille_reglages") or {}
    if isinstance(extra, dict):
        cfg.update(extra)
    return cfg


def _num(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def porte(
    famille: str,
    candidate: Any,
    entry_rules: Optional[dict[str, Any]] = None,
    rng: Optional[Callable[[], float]] = None,
) -> tuple[bool, str]:
    """(passe ?, raison). `consensus` n'est PAS jugé ici (il lit les autres bras)."""
    cfg = reglages(famille, entry_rules)

    if famille == HASARD:
        tirage = (rng or random.random)()
        p = float(cfg.get("probabilite", 0.2))
        return (tirage < p, "" if tirage < p else f"tirage {tirage:.2f} ≥ {p:.2f}")

    if famille in (SCORE, MATURE):
        # Le score (seuil alpha) ou la source « mature » + filtres font tout.
        return True, ""

    if famille == SECURITE:
        if candidate.mint_authority_revoked is not True:
            return False, "mint authority non révoquée ou inconnue"
        if candidate.freeze_authority_revoked is not True:
            return False, "freeze authority non révoquée ou inconnue"
        lp = _num(candidate.lp_locked_pct)
        if lp is None or lp < cfg["min_lp_verrouillee_pct"]:
            return False, f"LP verrouillée {lp} < {cfg['min_lp_verrouillee_pct']}"
        rc = _num(candidate.rugcheck_score)
        if rc is None or rc < cfg["min_rugcheck"]:
            return False, f"rugcheck {rc} < {cfg['min_rugcheck']}"
        top10 = _num(candidate.top10_holder_pct)
        if top10 is not None and top10 > cfg["max_top10_pct"]:
            return False, f"top10 {top10:.0f}% > {cfg['max_top10_pct']}%"
        dev = _num(candidate.dev_wallet_pct)
        if dev is not None and dev > cfg["max_dev_pct"]:
            return False, f"dev {dev:.1f}% > {cfg['max_dev_pct']}%"
        b = _num(candidate.bundler_rate)
        if b is not None and b > cfg["max_bundler"]:
            return False, f"bundlers {b:.2f} > {cfg['max_bundler']}"
        i = _num(candidate.insider_rate)
        if i is not None and i > cfg["max_insider"]:
            return False, f"insiders {i:.2f} > {cfg['max_insider']}"
        if candidate.is_wash_trading:
            return False, "wash trading"
        return True, ""

    if famille == WALLETS:
        wallets = candidate.smart_money_wallets_30m
        if wallets is None:
            wallets = candidate.smart_money_buys_30m
        if wallets is None:
            return False, "achats de wallets suivis inconnus"
        if wallets < int(cfg["min_wallets"]):
            return False, f"{wallets} wallet(s) suivi(s) < {cfg['min_wallets']}"
        achats = candidate.smart_money_buys_30m or 0
        ventes = candidate.smart_money_sells_30m or 0
        if ventes >= achats > 0:
            return False, f"wallets suivis vendent ({ventes} ventes ≥ {achats} achats)"
        return True, ""

    if famille == ACHETEURS:
        buys, sells = candidate.buys_5m or 0, candidate.sells_5m or 0
        if buys < cfg["min_achats_5m"]:
            return False, f"{buys} achats 5 min < {cfg['min_achats_5m']}"
        part = buys / (buys + sells) if buys + sells else 0.0
        if part < cfg["min_part_acheteurs"]:
            return False, f"part acheteurs {part:.2f} < {cfg['min_part_acheteurs']}"
        ch5 = _num(candidate.price_change_5m)
        if ch5 is None or not (cfg["min_hausse_5m"] <= ch5 <= cfg["max_hausse_5m"]):
            return False, f"variation 5 min {ch5} hors [{cfg['min_hausse_5m']}, {cfg['max_hausse_5m']}]"
        v1h = _num(candidate.volume_1h) or 0.0
        v5 = _num(candidate.volume_5m) or 0.0
        if v1h <= 0:
            return False, "volume 1 h inconnu"
        accel = v5 / (v1h / 12.0)
        if accel < cfg["min_acceleration_volume"]:
            return False, f"volume 5 min ×{accel:.2f} la moyenne < ×{cfg['min_acceleration_volume']}"
        return True, ""

    if famille == REPLI:
        ch1h = _num(candidate.price_change_1h)
        if ch1h is None or ch1h < cfg["min_hausse_1h"]:
            return False, f"hausse 1 h {ch1h} < {cfg['min_hausse_1h']}"
        ch5 = _num(candidate.price_change_5m)
        if ch5 is None or not (cfg["min_variation_5m"] <= ch5 <= cfg["max_variation_5m"]):
            return False, f"variation 5 min {ch5} hors [{cfg['min_variation_5m']}, {cfg['max_variation_5m']}]"
        recul = candidate.drawdown_from_ath_pct
        if recul is None:
            if ch5 > cfg["recul_5m_si_sommet_inconnu"]:
                return False, f"sommet inconnu et recul 5 min {ch5:+.1f}% trop faible"
            return True, ""
        if not (cfg["min_recul_sommet"] <= recul <= cfg["max_recul_sommet"]):
            return False, f"recul depuis le sommet {recul:.0f}% hors [{cfg['min_recul_sommet']}, {cfg['max_recul_sommet']}]"
        return True, ""

    return False, f"famille inconnue « {famille} »"


def accords_consensus(
    candidate: Any,
    membres: list[tuple[str, Any, float, dict[str, Any]]],
) -> tuple[list[str], list[str]]:
    """Membres d'accord / membres qui ont pu juger.

    `membres` : (nom, évaluation du bras ou None, seuil alpha du bras,
    entry_rules du bras). Un membre est d'accord si le candidat a passé SES
    filtres, SON seuil alpha et SA porte de famille. Le hasard ne vote jamais.
    """
    accord: list[str] = []
    juges: list[str] = []
    for nom, evaluation, seuil, regles in membres:
        famille = (regles or {}).get("famille")
        if evaluation is None or not famille or famille in (HASARD, CONSENSUS):
            continue
        juges.append(nom)
        retenu = next(
            (c for c in evaluation.result.candidates
             if c.token_address == candidate.token_address),
            None,
        )
        if retenu is None or retenu.alpha_score_absolute < seuil:
            continue
        ok, _ = porte(famille, retenu, regles)
        if ok:
            accord.append(nom)
    return accord, juges
