"""Lecture de `outcomeMeta` : une liste de marchés immuables, rangés par domaine.

Un marché a deux côtés (souvent Oui / Non). Chaque côté est une « pièce »
`#N` avec `N = 10 × outcome + côté` : c'est ce nom qui sert pour le carnet et
les prix.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

# Sous-jacents crypto connus. Tout autre sous-jacent d'un marché de seuil est
# rangé en « actions_indices » : se tromper de domaine ne coûte qu'un
# classement, jamais un pari (le bras revérifie qu'il a des bougies).
CRYPTO = {"BTC", "ETH", "SOL", "HYPE", "XRP", "BNB", "DOGE", "ZEC", "LINK", "AVAX",
          "SUI", "LTC", "ADA", "PUMP", "FARTCOIN", "ENA", "AAVE", "TRX"}

DOMAINES = ("crypto_seuil", "actions_seuil", "barriere", "sport_evenement")


@dataclass(frozen=True)
class Side:
    coin: str
    name: str
    index: int


@dataclass(frozen=True)
class Market:
    outcome: int
    name: str
    description: str
    klass: Optional[str]
    underlying: Optional[str]
    expiry: Optional[datetime]
    target: Optional[float]
    period: Optional[str]
    sides: tuple[Side, ...]
    question: Optional[str]
    is_fallback: bool
    domain: str

    @property
    def title(self) -> str:
        if self.underlying and self.target is not None:
            verbe = "touche" if self.domain == "barriere" else ">"
            fin = self.expiry.strftime("%Y-%m-%d %H:%M UTC") if self.expiry else "échéance inconnue"
            return f"{self.underlying} {verbe} {self.target:g} ({fin})"
        if self.question and self.question != self.name:
            return f"{self.question} — {self.name}"
        return self.name or f"marché {self.outcome}"


def parse_description(description: str) -> dict[str, str]:
    """`class:priceBinary|underlying:BTC|…` → dictionnaire. Texte libre → {}."""
    fields = {}
    for part in (description or "").split("|"):
        key, sep, value = part.partition(":")
        if sep and key.strip() and " " not in key.strip():
            fields[key.strip()] = value.strip()
    return fields


def parse_expiry(text: Optional[str]) -> Optional[datetime]:
    """`20260506-0600` → instant UTC. Format inconnu → None, jamais une date devinée."""
    if not text:
        return None
    for fmt in ("%Y%m%d-%H%M", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def domain_of(klass: Optional[str], underlying: Optional[str]) -> str:
    lowered = (klass or "").lower()
    if "touch" in lowered or "barrier" in lowered:
        return "barriere"
    if underlying and "binary" in lowered:
        return "crypto_seuil" if underlying.upper() in CRYPTO else "actions_seuil"
    return "sport_evenement"


def _to_float(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def parse_outcome_meta(meta: Optional[dict[str, Any]]) -> list[Market]:
    if not isinstance(meta, dict):
        return []

    # Une « question » regroupe plusieurs marchés exclusifs (match de foot :
    # équipe A, nul, équipe B) plus un marché de repli. Le repli s'affiche à
    # 50 % sans prix réel : il est marqué pour ne jamais être traité.
    question_of: dict[int, str] = {}
    fallbacks: set[int] = set()
    for question in meta.get("questions") or []:
        if not isinstance(question, dict):
            continue
        label = str(question.get("name") or question.get("description") or "question")
        for outcome_id in question.get("namedOutcomes") or []:
            if isinstance(outcome_id, int):
                question_of[outcome_id] = label
        fallback = question.get("fallbackOutcome")
        if isinstance(fallback, int):
            question_of[fallback] = label
            fallbacks.add(fallback)

    markets = []
    for row in meta.get("outcomes") or []:
        if not isinstance(row, dict) or not isinstance(row.get("outcome"), int):
            continue
        outcome = row["outcome"]
        description = str(row.get("description") or "")
        fields = parse_description(description)
        klass, underlying = fields.get("class"), fields.get("underlying")
        specs = row.get("sideSpecs") or [{"name": "Yes"}, {"name": "No"}]
        sides = tuple(
            Side(coin=f"#{10 * outcome + index}",
                 name=str(spec.get("name") if isinstance(spec, dict) else spec),
                 index=index)
            for index, spec in enumerate(specs)
        )
        markets.append(Market(
            outcome=outcome,
            name=str(row.get("name") or ""),
            description=description,
            klass=klass,
            underlying=underlying,
            expiry=parse_expiry(fields.get("expiry")),
            target=_to_float(fields.get("targetPrice")),
            period=fields.get("period"),
            sides=sides,
            question=question_of.get(outcome),
            is_fallback=outcome in fallbacks,
            domain=domain_of(klass, underlying),
        ))
    return markets
