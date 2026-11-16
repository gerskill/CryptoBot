"""Paris simulés : entrée au carnet, frais, règlement, statistiques.

Un pari = UNE ligne dans `bets.jsonl`, écrite au règlement. Les paris encore
ouverts vivent dans `open_bets.json`. Il n'y a donc pas de jambe partielle à
compter double : chaque ligne du journal est une sortie finale.
"""

import json
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

    def seen_outcomes(self) -> set[int]:
        return {b["outcome"] for b in self.open_bets() + self.settled() if "outcome" in b}


def compute_stats(settled: list[dict[str, Any]], capital: float, min_bets: int) -> dict[str, Any]:
    """Chiffres d'un bras, sur les paris RÉGLÉS seulement.

    Les paris « indéterminés » (règlement non établi) sont comptés à part et
    n'entrent dans aucun chiffre : un résultat deviné n'est pas un résultat.
    """
    decided = [b for b in settled if b.get("status") == "regle" and b.get("pnl_net") is not None]
    decided.sort(key=lambda b: b.get("settle_time") or "")
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

    count = len(decided)
    return {
        "paris_regles": count,
        "paris_indetermines": sum(1 for b in settled if b.get("status") == "indetermine"),
        "gagnes": sum(1 for b in decided if b.get("won")),
        "win_rate": (sum(1 for b in decided if b.get("won")) / count) if count else None,
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
