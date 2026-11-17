"""Paris simulés : entrée au carnet, frais, règlement, statistiques.

Un pari = UNE ligne dans `bets.jsonl`, écrite au règlement. Les paris encore
ouverts vivent dans `open_bets.json`. Il n'y a donc pas de jambe partielle à
compter double : chaque ligne du journal est une sortie finale.
"""

import json
import math
import os
from typing import Any, Optional


def simulate_entry(asks: list[tuple[float, float]], stake_usd: float) -> Optional[dict[str, float]]:
    """Achat de `stake_usd` en remontant le carnet vendeur.

    Renvoie le prix MOYEN réellement payé et le nombre de parts, ou `None` si
    le carnet ne contient pas assez de parts : on n'achète pas ce qui n'est
    pas à vendre. Le prix affiché (milieu) n'est jamais utilisé comme prix
    d'entrée, il est meilleur que ce qu'on obtiendrait.
    """
    remaining, shares = stake_usd, 0.0
    for price, size in asks:
        if price <= 0 or price >= 1:
            continue
        take = min(size, remaining / price)
        shares += take
        remaining -= take * price
        if remaining <= 1e-9:
            return {"price": stake_usd / shares, "shares": shares}
    return None


def remaining_asks(asks: list[tuple[float, float]], taken: float) -> list[tuple[float, float]]:
    """Carnet vendeur moins les `taken` parts déjà achetées CE cycle.

    Les parts achetées par un autre bras ne sont plus à vendre : on les retire
    en partant du meilleur prix. Sans ça, cinq bras achetaient le même marché
    au même prix, comme si chacun était seul — relevé le 2026-11-17 sur
    « BTC > 65050 ».
    """
    left, out = max(0.0, taken), []
    for price, size in asks:
        if left > 0:
            used = min(left, size)
            left -= used
            size -= used
        if size > 1e-12:
            out.append((price, size))
    return out


def buy_shares(asks: list[tuple[float, float]], shares: float) -> Optional[dict[str, float]]:
    """Achat de `shares` parts en remontant le carnet vendeur.

    Renvoie le prix moyen payé, les parts et le coût, ou `None` si le carnet
    ne contient pas assez de parts : on n'achète pas ce qui n'est pas à vendre.
    """
    if shares <= 0:
        return None
    wanted, cost = shares, 0.0
    for price, size in asks:
        if price <= 0 or price >= 1:
            continue
        take = min(size, wanted)
        cost += take * price
        wanted -= take
        if wanted <= 1e-9:
            return {"price": cost / shares, "shares": shares, "cost": cost}
    return None


def min_shares(price: float, min_order_usd: float, rule: str = "min_cote") -> float:
    """Plus petit nombre de parts qu'Hyperliquid accepte à ce prix.

    Deux règles publiées, non départagées par la documentation officielle
    (lecture impossible le 2026-11-17) [À VÉRIFIER] :
      - « min_cote »  : parts × min(prix, 1 − prix) ≥ minimum (hypernode-docs) ;
      - « notionnel » : parts × prix ≥ minimum (guide QuickNode).
    La première est la plus sévère : par défaut, le papier ne doit jamais
    accepter un ordre que le réel refuserait.
    """
    if min_order_usd <= 0 or not 0 < price < 1:
        return 0.0
    basis = min(price, 1 - price) if rule == "min_cote" else price
    return float(math.ceil(min_order_usd / basis - 1e-9))


def account(capital: float, settled: list[dict[str, Any]],
            open_bets: list[dict[str, Any]]) -> dict[str, float]:
    """Compte d'un bras, comme un vrai compte : solde, engagé, valeur.

    Une mise et ses frais d'achat quittent le solde à l'achat ; la recette
    du règlement y revient. `valeur` = capital + gains et pertes réglés.
    """
    realized = sum(float(b["pnl_net"]) for b in settled
                   if b.get("status") == "regle" and b.get("pnl_net") is not None)
    engaged = sum(float(b.get("stake_usd") or 0) + float(b.get("fee_entry_usd") or 0)
                  for b in open_bets)
    equity = capital + realized
    return {"capital": round(capital, 4), "valeur": round(equity, 4),
            "engage": round(engaged, 4), "solde": round(equity - engaged, 4),
            "resultat_regle": round(realized, 4)}


def net_pnl(stake_usd: float, shares: float, settle_value: float,
            fee_pct: float, settlement_fee_pct: float) -> float:
    """P&L net d'un pari tenu jusqu'au règlement.

    Coût = mise + frais d'achat. Recette = parts × valeur de règlement (1 ou
    0), moins des frais de règlement. Ces derniers ne sont pas confirmés par
    Liquid : ils sont comptés par prudence, réglables dans la configuration.
    """
    cost = stake_usd * (1 + fee_pct)
    payout = shares * settle_value * (1 - settlement_fee_pct)
    return payout - cost


def _write_atomic(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    os.replace(tmp, path)


def read_json(path: str, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def read_jsonl(path: str) -> list[dict[str, Any]]:
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows


def append_jsonl(path: str, row: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


class BetStore:
    """Fichiers d'un bras : `<dossier>/<bras>/open_bets.json` et `bets.jsonl`."""

    def __init__(self, data_dir: str, arm: str):
        self.arm = arm
        self.directory = os.path.join(data_dir, "arms", arm)
        self.open_path = os.path.join(self.directory, "open_bets.json")
        self.settled_path = os.path.join(self.directory, "bets.jsonl")

    def open_bets(self) -> list[dict[str, Any]]:
        return read_json(self.open_path, [])

    def settled(self) -> list[dict[str, Any]]:
        return read_jsonl(self.settled_path)

    def add(self, bet: dict[str, Any]) -> None:
        _write_atomic(self.open_path, self.open_bets() + [bet])

    def close(self, bet_id: str, closed: dict[str, Any]) -> None:
        # Journal d'abord, retrait ensuite : une coupure entre les deux laisse
        # un doublon repérable par `id`, jamais un pari perdu.
        append_jsonl(self.settled_path, closed)
        _write_atomic(self.open_path, [b for b in self.open_bets() if b.get("id") != bet_id])

    def update(self, bet_id: str, fields: dict[str, Any]) -> None:
        """Annote un pari encore ouvert (ex. : issue pas encore lisible)."""
        _write_atomic(self.open_path, [dict(b, **fields) if b.get("id") == bet_id else b
                                       for b in self.open_bets()])

    def seen_outcomes(self) -> set[int]:
        return {b["outcome"] for b in self.open_bets() + self.settled() if "outcome" in b}


TRANCHES_PRIX = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01))


def order_key(bet: dict[str, Any]) -> tuple[str, str, str]:
    """Ordre chronologique des paris réglés, départagé à heure égale."""
    return (bet.get("settle_time") or "", bet.get("entry_time") or "", str(bet.get("id") or ""))


def _drawdown_pct(bets: list[dict[str, Any]], capital: float) -> Optional[float]:
    peak = equity = worst = 0.0
    for bet in bets:
        equity += float(bet["pnl_net"])
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return round(100 * worst / capital, 3) if capital else None


def compute_stats(settled: list[dict[str, Any]], capital: float, min_bets: int) -> dict[str, Any]:
    """Chiffres d'un bras, sur les paris RÉGLÉS seulement.

    Depuis l'ADR 018, une issue illisible est comptée perdue après le délai de
    prudence (`regles_par_prudence`) : plus aucun pari ne sort des chiffres.
    Seuls restent à part : les « indéterminés » d'avant l'ADR 018, et les paris
    marqués `hors_regle` (pris en dehors de la règle du bras à cause d'un
    défaut corrigé depuis) — leur argent compte dans le compte du bras, mais
    ils ne mesurent pas la règle.
    """
    decided = [b for b in settled if b.get("status") == "regle" and b.get("pnl_net") is not None
               and not b.get("hors_regle")]
    # Départage : 79 paris pour 9 horaires de règlement le 2026-11-17. Trier
    # sur l'heure seule laissait l'ordre — donc le drawdown — au hasard de
    # l'API (de -256 à -281 $ sur le même jeu). Ordre stable, reproductible.
    decided.sort(key=order_key)
    pnls = [float(b["pnl_net"]) for b in decided]
    gains = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)

    peak = equity = 0.0
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    worst = sorted(pnls)[: max(1, round(len(pnls) * 0.05))] if pnls else []

    def brier(key: str) -> Optional[float]:
        pairs = [(b[key], b["won"]) for b in decided if b.get(key) is not None]
        if not pairs:
            return None
        return sum((p - (1.0 if won else 0.0)) ** 2 for p, won in pairs) / len(pairs)

    # Calibration : dans chaque tranche de prix payé, quelle part a gagné ?
    # Un marché bien réglé gagne à peu près aussi souvent que son prix.
    calibration = []
    for low, high in TRANCHES_PRIX:
        band = [b for b in decided if low <= float(b.get("entry_price") or 0) < high]
        calibration.append({
            "tranche": f"{round(low * 100)}–{round(min(high, 1) * 100)} %",
            "paris": len(band),
            "prix_moyen": (sum(float(b["entry_price"]) for b in band) / len(band)) if band else None,
            "part_gagnee": (sum(1 for b in band if b.get("won")) / len(band)) if band else None,
            "pnl_net": round(sum(float(b["pnl_net"]) for b in band), 4),
        })
    par_domaine: dict[str, dict[str, Any]] = {}
    for b in decided:
        row = par_domaine.setdefault(b.get("domain") or "inconnu", {"paris": 0, "gagnes": 0, "pnl_net": 0.0})
        row["paris"] += 1
        row["gagnes"] += 1 if b.get("won") else 0
        row["pnl_net"] = round(row["pnl_net"] + float(b["pnl_net"]), 4)

    count = len(decided)
    return {
        "calibration": calibration,
        "par_domaine": par_domaine,
        "paris_regles": count,
        "paris_indetermines": sum(1 for b in settled if b.get("status") == "indetermine"),
        "gagnes": sum(1 for b in decided if b.get("won")),
        "win_rate": (sum(1 for b in decided if b.get("won")) / count) if count else None,
        # Un pari « gagné » acheté à 0,99 perd de l'argent une fois les frais
        # payés : les deux comptes sont donnés côte à côte, jamais l'un pour l'autre.
        "gagnes_net": sum(1 for p in pnls if p > 0),
        "win_rate_net": (sum(1 for p in pnls if p > 0) / count) if count else None,
        "pertes_reelles": sum(1 for b in decided if b.get("won") is False),
        "regles_par_prudence": sum(1 for b in decided if b.get("regle_par_prudence")),
        "paris_hors_regle": sum(1 for b in settled if b.get("hors_regle")),
        # Le COMPTE, lui, a payé ces paris : leur résultat est donné à part, et
        # le drawdown du compte les inclut. Pour passer en réel, c'est le
        # compte qui compte, pas la règle seule (juge, 2026-11-17).
        "pnl_hors_regle": round(sum(float(b["pnl_net"]) for b in settled
                                    if b.get("hors_regle") and b.get("pnl_net") is not None), 4),
        "drawdown_compte_pct": _drawdown_pct(
            sorted([b for b in settled if b.get("status") == "regle" and b.get("pnl_net") is not None],
                   key=order_key), capital),
        "profit_factor": (gains / losses) if losses > 0 else None,
        "pnl_net": round(sum(pnls), 4),
        "drawdown_max_pct": round(100 * max_dd / capital, 3) if capital else None,
        "pires_5pct_moyenne": round(sum(worst) / len(worst), 4) if worst else None,
        # Brier : écart quadratique moyen entre une probabilité et l'issue.
        # Plus bas = mieux. Le bras bat le marché s'il est sous le marché.
        "brier_bras": brier("prob_estimee"),
        "brier_marche": brier("entry_price"),
        "verdict": "à suivre" if count < min_bets else "échantillon suffisant — à soumettre au juge",
    }
