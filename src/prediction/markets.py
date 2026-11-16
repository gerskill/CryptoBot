"""Lecture de `outcomeMeta` : une liste de marchés immuables, rangés par domaine.

Un marché a deux côtés (souvent Oui / Non). Chaque côté est une « pièce »
`#N` avec `N = 10 × outcome + côté` : c'est ce nom qui sert pour le carnet et
les prix.

Formes confrontées à la réponse RÉELLE de l'API le 2026-11-16 (extrait dans
`tests/fixtures/outcome_meta_2026-11-16.json`). Deux écritures coexistent :
  - `name: "Recurring"` + `description: "class:priceBinary|underlying:…"` ;
  - `name: "template:<modèle>"` + des champs propres à chaque modèle.
La première version de ce module ne lisait que la première : 4 marchés sur
309. Un modèle inconnu est rangé en « autre » et n'est jamais parié.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

CRYPTO = {"BTC", "ETH", "SOL", "HYPE", "XRP", "BNB", "DOGE", "ZEC", "LINK", "AVAX",
          "SUI", "LTC", "ADA", "PUMP", "FARTCOIN", "ENA", "AAVE", "TRX"}
MATIERES = {"GOLD", "SILVER", "CL", "BRENT", "NATGAS", "COPPER", "PLATINUM", "PALLADIUM"}

# Un domaine = une façon de parier. Une famille = un onglet du dashboard.
FAMILLES = {
    "prix": ("crypto_seuil", "actions_seuil", "matieres_seuil", "barriere", "tranche_prix"),
    "sport": ("sport_match", "sport_tournoi"),
    "questions": ("evenement",),
    "autre": ("autre",),
}
DOMAINES = tuple(d for group in FAMILLES.values() for d in group)
DOMAINES_SEUIL = ("crypto_seuil", "actions_seuil", "matieres_seuil")
FAMILLE_DE = {d: famille for famille, group in FAMILLES.items() for d in group}


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
    underlying: Optional[str]      # nom de pièce pour les bougies (« xyz:NVDA »)
    expiry: Optional[datetime]     # fin du marché, ou date limite de résolution
    target: Optional[float]
    period: Optional[str]
    sides: tuple[Side, ...]
    question: Optional[str]        # identifiant de la question (issues exclusives)
    is_fallback: bool
    domain: str
    venue: Optional[str] = None    # déployeur du marché : skew, out, txyz, fox
    group: Optional[str] = None    # libellé commun aux issues d'une même question
    label: Optional[str] = None    # libellé de cette issue
    start: Optional[datetime] = None  # coup d'envoi ou décision prévue

    @property
    def symbol(self) -> Optional[str]:
        return self.underlying.split(":")[-1] if self.underlying else None

    @property
    def famille(self) -> str:
        return FAMILLE_DE.get(self.domain, "autre")

    @property
    def event_time(self) -> Optional[datetime]:
        """Instant après lequel on ne parie plus : le coup d'envoi s'il est
        connu, sinon la fin du marché."""
        return self.start or self.expiry

    @property
    def title(self) -> str:
        fin = self.expiry.strftime("%Y-%m-%d %H:%M UTC") if self.expiry else "échéance inconnue"
        if self.domain in DOMAINES_SEUIL and self.target is not None:
            return f"{self.symbol} > {self.target:g} ({fin})"
        if self.domain == "barriere" and self.target is not None:
            return f"{self.symbol} touche {self.target:g} (avant {fin})"
        if self.group and self.label:
            return f"{self.group} — {self.label}"
        if self.group:
            return self.group
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


def seuil_domain(underlying: Optional[str]) -> str:
    """Classe d'actif d'un marché de seuil. Les pièces `xyz:…` sont des
    actions, indices ou matières premières, jamais de la crypto."""
    symbol = (underlying or "").split(":")[-1].upper()
    if symbol in MATIERES:
        return "matieres_seuil"
    if ":" not in (underlying or "") and symbol in CRYPTO:
        return "crypto_seuil"
    return "actions_seuil"


def domain_of(klass: Optional[str], underlying: Optional[str]) -> str:
    lowered = (klass or "").lower()
    if "touch" in lowered or "barrier" in lowered:
        return "barriere"
    if underlying and "binary" in lowered:
        return seuil_domain(underlying)
    return "autre"


def _to_float(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _template(name: str) -> str:
    return name[len("template:"):] if name.startswith("template:") else ""


_TAUX = {"policyRateDecrease": "baisse", "policyRateIncrease": "hausse",
         "policyRateNoChange": "inchangé"}


def _match_title(fields: dict[str, str]) -> str:
    teams = f"{fields.get('participantA', '?')} vs {fields.get('participantB', '?')}"
    return f"{teams} — {fields['competition'].strip()}" if fields.get("competition") else teams


def _from_question(template: str, row_fields: dict[str, str], row_template: str,
                   q: dict[str, Any]) -> dict[str, Any]:
    """Champs d'une issue nommée, d'après le modèle de SA question."""
    qf = q["fields"]
    if q["template"] == "sportsContestResult":
        label = row_fields.get("participant") or ("Nul" if "Draw" in row_template else None)
        return {"domain": "sport_match", "group": _match_title(qf), "label": label,
                "start": parse_expiry(qf.get("scheduledStart")),
                "expiry": parse_expiry(qf.get("resolutionDeadline")), "klass": "sportsContestResult"}
    if q["template"] == "sportsTournamentWinner":
        group = f"{qf.get('competition', 'Tournoi').strip()} {qf.get('season', '')}".strip()
        return {"domain": "sport_tournoi", "group": f"Vainqueur {group}",
                "label": row_fields.get("participant"),
                "expiry": parse_expiry(qf.get("resolutionDeadline")), "klass": "sportsTournamentWinner"}
    if q["template"] == "policyRateDecision":
        return {"domain": "evenement",
                "group": f"Taux {qf.get('institution', '')} — {qf.get('decisionLabel', '')}".strip(" —"),
                "label": _TAUX.get(row_template), "start": parse_expiry(qf.get("scheduledDecision")),
                "expiry": parse_expiry(qf.get("decisionDeadline")), "klass": "policyRateDecision"}
    if qf.get("class") == "priceBucket":
        # L'ordre des tranches (index 0 = sous le premier seuil ?) n'est pas
        # documenté : le libellé reste neutre et aucun bras n'estime ce marché.
        return {"domain": "tranche_prix", "underlying": qf.get("underlying"),
                "group": f"{qf.get('underlying')} tranches de prix (seuils {qf.get('priceThresholds')})",
                "label": f"tranche n° {row_fields.get('index', '?')}",
                "expiry": parse_expiry(qf.get("expiry")), "period": qf.get("period"),
                "klass": "priceBucket"}
    return {"domain": "autre", "group": q["name"], "label": row_fields.get("participant")}


def _standalone(template: str, fields: dict[str, str]) -> dict[str, Any]:
    """Champs d'un marché qui n'appartient à aucune question."""
    if fields.get("class") == "priceBinary":
        return {"klass": "priceBinary", "underlying": fields.get("underlying"),
                "expiry": parse_expiry(fields.get("expiry")),
                "target": _to_float(fields.get("targetPrice")), "period": fields.get("period"),
                "domain": seuil_domain(fields.get("underlying"))}
    if template in ("binaryPrice", "binaryPriceExternal"):
        underlying = fields.get("perp") or fields.get("shortName")
        return {"klass": template, "underlying": underlying,
                "expiry": parse_expiry(fields.get("time")),
                "target": _to_float(fields.get("threshold")), "domain": seuil_domain(underlying)}
    if template == "priceTouch":
        return {"klass": template, "underlying": fields.get("perp"),
                "expiry": parse_expiry(fields.get("time")),
                "target": _to_float(fields.get("target")), "domain": "barriere"}
    if template == "sportsContestWinner":
        return {"klass": template, "domain": "sport_match", "group": _match_title(fields),
                "start": parse_expiry(fields.get("scheduledStart")),
                "expiry": parse_expiry(fields.get("resolutionDeadline"))}
    if template == "companyIpoFirstDayMarketCap":
        cap = _to_float(fields.get("marketCapThresholdB"))
        return {"klass": template, "domain": "evenement", "underlying": fields.get("company"),
                "target": cap, "expiry": parse_expiry(fields.get("listingDeadline")),
                "group": f"Introduction en bourse {fields.get('company', '?')}",
                "label": f"capitalisation du 1er jour > {cap:g} Md$" if cap is not None else None}
    if template == "companyIpoConfirmed":
        return {"klass": template, "domain": "evenement", "underlying": fields.get("company"),
                "expiry": parse_expiry(fields.get("dateTime")),
                "group": f"Introduction en bourse {fields.get('company', '?')}",
                "label": "confirmée avant l'échéance"}
    klass = fields.get("class")
    return {"klass": klass, "underlying": fields.get("underlying"),
            "expiry": parse_expiry(fields.get("expiry")),
            "target": _to_float(fields.get("targetPrice")), "period": fields.get("period"),
            "domain": domain_of(klass, fields.get("underlying"))}


def _side_name(raw: Any, fields: dict[str, str]) -> str:
    """`template:{shortNameA}` → `DEN`. Un champ manquant laisse le texte brut."""
    text = str(raw.get("name") if isinstance(raw, dict) else raw)
    text = text[len("template:"):] if text.startswith("template:") else text
    if text.startswith("{") and text.endswith("}"):
        return fields.get(text[1:-1], text)
    return text


def settled_outcomes(meta: Optional[dict[str, Any]]) -> dict[int, bool]:
    """Issues dont la question est officiellement réglée : True = gagnante.

    Lu dans `settledNamedOutcomes`. Hypothèse [À VÉRIFIER sur un premier
    règlement réel] : la liste contient les issues gagnantes. Le règlement ne
    s'y fie donc que si le dernier prix connu dit la même chose.
    """
    result: dict[int, bool] = {}
    for question in (meta or {}).get("questions") or []:
        if not isinstance(question, dict):
            continue
        winners = [o for o in question.get("settledNamedOutcomes") or [] if isinstance(o, int)]
        if not winners:
            continue
        for outcome_id in question.get("namedOutcomes") or []:
            if isinstance(outcome_id, int):
                result[outcome_id] = outcome_id in winners
    return result


def parse_outcome_meta(meta: Optional[dict[str, Any]]) -> list[Market]:
    if not isinstance(meta, dict):
        return []

    # Une « question » regroupe plusieurs marchés exclusifs (match de foot :
    # équipe A, nul, équipe B) plus un marché de repli. Le repli s'affiche à
    # 50 % sans prix réel : il est marqué pour ne jamais être traité.
    # Clé = identifiant de la question : des dizaines de questions portent le
    # même `name` (« template:sportsContestResult »).
    question_of: dict[int, dict[str, Any]] = {}
    fallbacks: set[int] = set()
    for index, question in enumerate(meta.get("questions") or []):
        if not isinstance(question, dict):
            continue
        name = str(question.get("name") or "question")
        info = {"id": str(question.get("question", f"q{index}")), "name": name,
                "template": _template(name),
                "fields": parse_description(str(question.get("description") or ""))}
        for outcome_id in question.get("namedOutcomes") or []:
            if isinstance(outcome_id, int):
                question_of[outcome_id] = info
        fallback = question.get("fallbackOutcome")
        if isinstance(fallback, int):
            question_of[fallback] = info
            fallbacks.add(fallback)

    markets = []
    for row in meta.get("outcomes") or []:
        if not isinstance(row, dict) or not isinstance(row.get("outcome"), int):
            continue
        outcome = row["outcome"]
        name = str(row.get("name") or "")
        description = str(row.get("description") or "")
        fields = parse_description(description)
        template = _template(name)
        info = question_of.get(outcome)
        is_fallback = outcome in fallbacks or "fallback" in name.lower()
        if info is not None and not is_fallback:
            extra = _from_question(template, fields, template, info)
        elif info is not None:
            extra = {"domain": "autre", "group": info["name"], "label": "repli"}
        else:
            extra = _standalone(template, fields)
        specs = row.get("sideSpecs") or [{"name": "Yes"}, {"name": "No"}]
        sides = tuple(
            Side(coin=f"#{10 * outcome + index}", name=_side_name(spec, fields), index=index)
            for index, spec in enumerate(specs)
        )
        markets.append(Market(
            outcome=outcome,
            name=name,
            description=description,
            klass=extra.get("klass"),
            underlying=extra.get("underlying"),
            expiry=extra.get("expiry"),
            target=extra.get("target"),
            period=extra.get("period"),
            sides=sides,
            question=info["id"] if info else None,
            is_fallback=is_fallback,
            domain=extra.get("domain", "autre"),
            venue=row.get("venue") if isinstance(row.get("venue"), str) else None,
            group=extra.get("group"),
            label=extra.get("label"),
            start=extra.get("start"),
        ))
    return markets
