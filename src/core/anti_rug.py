"""Porte ANTI-ARNAQUE du bras de test `anti_rug` (2026-10-11, PAPER).

Lit UNIQUEMENT des données déjà calculées : l'instantané de l'observateur
on-chain (`OnchainObserver.snapshot`, calculé AVANT la décision — anti
look-ahead) et les autorités déjà portées par le candidat (RugCheck/GMGN).
Aucun appel réseau ici.

Désactivée pour tout bras dont le document n'a pas
`entry_rules.anti_rug_enabled = true` : les bras existants ne passent jamais
par cette porte.

SEUILS (entry_rules.anti_rug_*) : heuristiques standard + distribution des
instantanés observés du 10/10 15:42 au 11/10 18:50 (168 tokens distincts
pris par un bras). Ils ne sont PAS optimisés sur le P&L des trades (trop peu
de données, risque de surapprentissage). Voir docs/anti_rug_2026-10-11.md.

DONNÉES MANQUANTES : on n'entre pas (fail closed), mais le refus est un
`continue` — le token est réévalué au cycle suivant tant qu'il reste
candidat, et entrera dès que l'instantané est prêt et passe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

DEFAULTS: dict[str, Any] = {
    "anti_rug_require_authorities_revoked": True,
    "anti_rug_max_top10_wallets_pct": 20.0,
    "anti_rug_max_creator_pct": 5.0,
    "anti_rug_max_dev_out_transfers": 5,
    "anti_rug_reject_any_dev_sale": False,
    "anti_rug_max_launch_slot_buyers": 4,
    "anti_rug_max_fresh_top10": 1,
    "anti_rug_max_fresh_first_buyers": 1,
    "anti_rug_max_data_age_sec": 3600,
}

READY_STATUSES = ("ok", "partial")


@dataclass
class AntiRugVerdict:
    passed: bool
    reason: str = ""
    missing: bool = False
    checks: dict[str, Any] = field(default_factory=dict)


def rules_from(entry_rules: Optional[dict[str, Any]]) -> dict[str, Any]:
    entry_rules = entry_rules or {}
    return {k: entry_rules.get(k, v) for k, v in DEFAULTS.items()}


def _authority(snapshot_value: Any, candidate_value: Any) -> Optional[bool]:
    if snapshot_value is not None:
        return bool(snapshot_value)
    if candidate_value is not None:
        return bool(candidate_value)
    return None


def anti_rug_gate(
    snapshot: dict[str, Any], candidate: Any, entry_rules: Optional[dict[str, Any]] = None
) -> AntiRugVerdict:
    """Verdict d'entrée. `missing=True` = refus faute de données (réévaluable)."""
    rules = rules_from(entry_rules)
    status = snapshot.get("onchain_status")
    checks: dict[str, Any] = {"status": status}

    if status not in READY_STATUSES:
        return AntiRugVerdict(False, f"données manquantes (on-chain {status})", True, checks)
    age = snapshot.get("onchain_age_at_decision_sec")
    checks["data_age_sec"] = age
    if age is not None and age > float(rules["anti_rug_max_data_age_sec"]):
        return AntiRugVerdict(False, f"données manquantes (instantané de {age:.0f}s, trop vieux)", True, checks)

    mint = _authority(snapshot.get("onchain_mint_authority_revoked"),
                      getattr(candidate, "mint_authority_revoked", None))
    freeze = _authority(snapshot.get("onchain_freeze_authority_revoked"),
                        getattr(candidate, "freeze_authority_revoked", None))
    values = {
        "mint_revoked": mint,
        "freeze_revoked": freeze,
        "top10_wallets_pct": snapshot.get("onchain_top10_wallets_pct"),
        "creator_pct": snapshot.get("onchain_creator_pct_top20"),
        "dev_out_transfers": snapshot.get("onchain_dev_out_transfers"),
        "dev_sold": snapshot.get("onchain_dev_sold"),
        "launch_slot_buyers": snapshot.get("onchain_launch_slot_buyers"),
        "fresh_top10": snapshot.get("onchain_fresh_top10"),
        "fresh_first_buyers": snapshot.get("onchain_fresh_first_buyers"),
    }
    checks.update(values)

    # Refus CERTAINS d'abord : une donnée présente et mauvaise suffit, même si
    # une autre manque (inutile d'attendre la suivante).
    reasons: list[str] = []
    if rules["anti_rug_require_authorities_revoked"]:
        if mint is False:
            reasons.append("autorité mint active")
        if freeze is False:
            reasons.append("autorité freeze active")
    top10 = values["top10_wallets_pct"]
    if top10 is not None and top10 > float(rules["anti_rug_max_top10_wallets_pct"]):
        reasons.append(f"top10 wallets {top10:.1f}% > {rules['anti_rug_max_top10_wallets_pct']}%")
    creator = values["creator_pct"]
    if creator is not None and creator > float(rules["anti_rug_max_creator_pct"]):
        reasons.append(f"créateur {creator:.1f}% > {rules['anti_rug_max_creator_pct']}%")
    out = values["dev_out_transfers"]
    if rules["anti_rug_reject_any_dev_sale"] and values["dev_sold"] is True:
        reasons.append("le dev a vendu")
    elif out is not None and out > int(rules["anti_rug_max_dev_out_transfers"]):
        reasons.append(f"dev : {out} transferts sortants > {rules['anti_rug_max_dev_out_transfers']}")
    slot = values["launch_slot_buyers"]
    if slot is not None and slot > int(rules["anti_rug_max_launch_slot_buyers"]):
        reasons.append(f"{slot} acheteurs au slot de lancement > {rules['anti_rug_max_launch_slot_buyers']}")
    fresh_top = values["fresh_top10"]
    if fresh_top is not None and fresh_top > int(rules["anti_rug_max_fresh_top10"]):
        reasons.append(f"{fresh_top} wallets frais dans le top 10 > {rules['anti_rug_max_fresh_top10']}")
    fresh_fb = values["fresh_first_buyers"]
    if fresh_fb is not None and fresh_fb > int(rules["anti_rug_max_fresh_first_buyers"]):
        reasons.append(f"{fresh_fb} wallets frais parmi les 10 premiers acheteurs > "
                       f"{rules['anti_rug_max_fresh_first_buyers']}")
    if reasons:
        return AntiRugVerdict(False, " / ".join(reasons), False, checks)

    required = ["top10_wallets_pct", "creator_pct", "launch_slot_buyers", "fresh_top10",
                "fresh_first_buyers"]
    required.append("dev_sold" if rules["anti_rug_reject_any_dev_sale"] else "dev_out_transfers")
    if rules["anti_rug_require_authorities_revoked"]:
        required += ["mint_revoked", "freeze_revoked"]
    absent = [name for name in required if values[name] is None]
    if absent:
        return AntiRugVerdict(False, "données manquantes (" + ", ".join(absent) + ")", True, checks)
    return AntiRugVerdict(True, "", False, checks)
