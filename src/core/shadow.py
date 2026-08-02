"""Suivi des candidats REJETÉS — corrige le biais du survivant.

Un bot qui n'apprend que de ses trades ne peut jamais découvrir qu'un filtre
est trop strict : les candidats rejetés ne produisent aucune donnée. Le seul
signal disponible est "mes trades perdent" -> resserrer. Le système converge
donc mécaniquement vers des filtres de plus en plus durs jusqu'à ne plus rien
laisser passer.

Ce module enregistre chaque rejet avec son prix, puis revient voir ce que le
token est devenu. Ce sont des trades FICTIFS : aucun n'a été pris, aucun
slippage. Ils servent à arbitrer des SEUILS, pas à mesurer une performance.
"""

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

MAX_TRACKING_HOURS = 4
MIN_AGE_BEFORE_REVIEW_MINUTES = 20


@dataclass(frozen=True)
class ShadowVerdict:
    """Ce qu'un rejet est devenu."""

    token_address: str
    symbol: str
    reason: str
    reason_family: str
    entry_price: float
    peak_price: float
    last_price: float
    minutes_tracked: float

    @property
    def peak_gain_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        return 100 * (self.peak_price - self.entry_price) / self.entry_price

    @property
    def would_have_won(self) -> bool:
        """Aurait atteint TP1 (+100%) — critère volontairement exigeant."""
        return self.peak_gain_pct >= 100


def reason_family(reason: str) -> str:
    """Regroupe les motifs de rejet en familles ajustables.

    L'ÂGE ET LE VOLUME TOMBAIENT DANS « autre ». Mesuré au 2026-08-02 sur les
    399 rejets jugés du témoin : 175 rejets d'âge et 42 de volume, soit 54 %
    du total, rangés dans une famille qu'aucun paramètre ne dessert. Le motif
    de rejet DOMINANT était donc invisible à `_relax_from_shadow`.

    L'âge donne DEUX familles, pas une : « âge 6.0h > 6h » et « âge 0.1h <
    1.5h » appellent des corrections opposées (monter le plafond / baisser le
    plancher). Les confondre relâcherait au hasard.
    """
    text = (reason or "").lower()
    if "liquidité" in text or "liquidity" in text:
        return "liquidity"
    if "holders" in text:
        return "holders"
    if "top wallet" in text:
        return "concentration"
    if "rugcheck" in text or "risque critique" in text:
        return "rugcheck"
    if "mentions" in text or "social" in text:
        return "social"
    if "smart money" in text:
        return "smart_money"
    if "dev wallet" in text:
        return "dev_wallet"
    if "authority" in text:
        return "authority"
    if text.startswith("âge") or text.startswith("age"):
        # « > » = trop vieux pour le plafond ; « < » = trop jeune pour le
        # plancher. Le sens du signe EST la donnée.
        return "age_max" if ">" in text else "age_min"
    if "volume" in text:
        return "volume"
    if text.startswith("alpha"):
        # Pas un filtre : la porte du SCORE. Mesuré au 2026-08-02, c'est le
        # blocage dominant de `narrative` (869 cycles sans entrée) et de
        # `consensus` (383). Desserrer un filtre ne les débloquerait jamais —
        # leurs candidats passent les filtres et meurent au seuil.
        return "alpha"
    return "autre"


class ShadowTracker:
    """Enregistre les rejets et mesure ce qu'ils sont devenus."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._tracked: dict[str, dict[str, Any]] = {}

    def record_rejections(self, rejected: Iterable[Any]) -> int:
        """Met sous observation les candidats rejetés de ce cycle."""
        added = 0
        for candidate in rejected:
            address = candidate.token_address
            if address in self._tracked or candidate.price_usd <= 0:
                continue
            # Un rejet pour risque critique n'a pas à être réexaminé : on ne
            # relâchera jamais le filtre honeypot, quel que soit le manque à gagner.
            if reason_family(candidate.rejected_reason or "") in ("rugcheck", "authority"):
                continue
            self._tracked[address] = {
                "symbol": candidate.symbol,
                "chain": candidate.chain,
                "pair_address": candidate.pair_address,
                "reason": candidate.rejected_reason or "",
                "family": reason_family(candidate.rejected_reason or ""),
                "entry_price": candidate.price_usd,
                "peak_price": candidate.price_usd,
                "last_price": candidate.price_usd,
                "rejected_at": time.time(),
                "alpha_absolute": candidate.alpha_score_absolute,
                "liquidity": candidate.liquidity_usd,
                "age_hours": candidate.age_hours,
            }
            added += 1
        return added

    @property
    def tracked_addresses(self) -> list[str]:
        return list(self._tracked)

    def pending_review(self) -> list[str]:
        """Rejets assez vieux pour être jugés, pas encore expirés."""
        now = time.time()
        return [
            address
            for address, entry in self._tracked.items()
            if now - entry["rejected_at"] >= MIN_AGE_BEFORE_REVIEW_MINUTES * 60
        ]

    def update_price(self, token_address: str, price: float) -> None:
        entry = self._tracked.get(token_address)
        if entry is None or price <= 0:
            return
        entry["last_price"] = price
        entry["peak_price"] = max(entry["peak_price"], price)

    def expire(self) -> list[ShadowVerdict]:
        """Clôture les suivis arrivés à terme et les écrit sur disque."""
        now = time.time()
        verdicts = []
        for address in [
            a
            for a, e in self._tracked.items()
            if now - e["rejected_at"] >= MAX_TRACKING_HOURS * 3600
        ]:
            entry = self._tracked.pop(address)
            verdict = ShadowVerdict(
                token_address=address,
                symbol=entry["symbol"],
                reason=entry["reason"],
                reason_family=entry["family"],
                entry_price=entry["entry_price"],
                peak_price=entry["peak_price"],
                last_price=entry["last_price"],
                minutes_tracked=(now - entry["rejected_at"]) / 60,
            )
            self._append(verdict, entry)
            verdicts.append(verdict)
        return verdicts

    def _append(self, verdict: ShadowVerdict, entry: dict[str, Any]) -> None:
        row = {
            "timestamp": time.time(),
            "token": verdict.symbol,
            "token_address": verdict.token_address,
            "reason": verdict.reason,
            "reason_family": verdict.reason_family,
            "entry_price": verdict.entry_price,
            "peak_price": verdict.peak_price,
            "last_price": verdict.last_price,
            "peak_gain_pct": round(verdict.peak_gain_pct, 2),
            "would_have_won": verdict.would_have_won,
            "minutes_tracked": round(verdict.minutes_tracked, 1),
            "alpha_absolute": entry.get("alpha_absolute"),
            "liquidity_at_rejection": entry.get("liquidity"),
            "age_hours_at_rejection": entry.get("age_hours"),
        }
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        rows = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def missed_rate_by_family(self, min_sample: int = 10) -> dict[str, dict[str, Any]]:
        """Taux de rejets qui auraient atteint +100%, par famille de motif."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in self.read_all():
            # La famille est RECALCULÉE depuis le motif, pas relue telle
            # quelle : les lignes déjà sur disque portent la taxonomie du jour
            # où elles ont été écrites. Sans ce recalcul, les 175 rejets d'âge
            # de l'historique resteraient dans « autre » et l'élargissement de
            # la taxonomie ne servirait qu'aux rejets futurs.
            famille = reason_family(row.get("reason", "")) or row.get(
                "reason_family", "autre"
            )
            grouped.setdefault(famille, []).append(row)

        out = {}
        for family, rows in grouped.items():
            if len(rows) < min_sample:
                continue
            missed = [r for r in rows if r.get("would_have_won")]
            out[family] = {
                "sample": len(rows),
                "missed_rate": round(100 * len(missed) / len(rows), 1),
                "avg_peak_gain_pct": round(
                    sum(r.get("peak_gain_pct", 0) for r in rows) / len(rows), 1
                ),
            }
        return out

    @property
    def stats(self) -> dict[str, int]:
        return {"tracked": len(self._tracked), "judged": len(self.read_all())}
