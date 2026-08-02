"""Entonnoir de décision : à quelle porte chaque token meurt, pour chaque bras.

LA QUESTION À LAQUELLE ÇA RÉPOND. « Pourquoi ce bras ne trade pas ? » Sans
trace, les réponses possibles sont indiscernables : le marché est vide, un
seuil est trop strict, l'analyse technique refuse tout, ou le carnet ne porte
pas la taille. Chacune appelle une correction opposée — desserrer un filtre
alors que le vrai blocage est le coût d'entrée ne fait qu'ajouter du bruit.

CE QUI EXISTAIT DÉJÀ : `ArmEvaluation.result.rejected` porte un
`rejected_reason` par candidat, et `ConfluenceRow` sait qui a voté quoi. Ce
qui manquait, c'est la SUITE : après les filtres viennent le seuil alpha,
`can_open`, l'analyse technique et la garde économique — quatre portes dont
aucun rejet n'était conservé.

CE MODULE NE DÉCIDE RIEN. Il observe et écrit. Aucun appel réseau : tout ce
qu'il journalise a déjà été calculé par la boucle.
"""

import json
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# Portes dans l'ordre où la boucle les applique. L'ordre est la donnée : un
# token rejeté en « filtres » n'a jamais été soumis à la garde économique, et
# compter ses rejets ensemble ferait croire à un blocage inexistant.
GATES = (
    "filtres",
    "seuil_alpha",
    "confluence",
    "portefeuille",
    "technique",
    "economie",
    "entree",
)

# Côté SORTIE. Ce n'est pas la symétrie de l'entrée : à l'entrée on cherche
# pourquoi rien ne passe, à la sortie on cherche si on est sorti AU BON
# MOMENT. La mesure utile n'est pas « quelle règle a fermé » — le journal le
# dit déjà — mais l'écart entre ce qu'on a pris et ce qu'il y avait à prendre.
EXIT_GATES = ("surveille", "alerte_liquidite", "sortie")
# Ligne écrite = 1 par token par bras par cycle. À 960 cycles/jour et 7 bras,
# tout journaliser gonflerait vite ; on ne garde que les tokens ayant franchi
# au moins les filtres, plus un échantillon des rejets de filtres.
#
# ATTENTION au dénominateur : `_cycles` s'incrémente une fois par BRAS, pas
# par cycle. Avec 7 stratégies, « 1 sur 10 » veut dire 1 évaluation sur 10,
# soit environ 1,4 échantillon par cycle réparti sur des bras différents. Le
# comptage agrégé reste juste — les compteurs `filtres_total` sont exhaustifs —
# mais un motif de rejet précis peut manquer plusieurs cycles d'affilée pour
# un bras donné.
FILTER_SAMPLE_EVERY = 10


@dataclass
class FunnelRecorder:
    """Accumule les verdicts d'un cycle, écrit une fois à la fin."""

    path: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    _cycles: int = 0

    def record(
        self,
        arm: str,
        token: str,
        symbol: str,
        gate: str,
        passed: bool,
        reason: str = "",
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        row = {
            "ts": round(time.time(), 3),
            "arm": arm,
            "token": token,
            "symbol": symbol,
            "gate": gate,
            "passed": passed,
            "reason": reason,
        }
        if extra:
            row.update(extra)
        self.rows.append(row)

    def record_evaluation(self, arm: str, evaluation: Any, threshold: float) -> None:
        """Filtres et seuil alpha, depuis ce que `evaluate()` a déjà calculé."""
        self._cycles += 1
        sample = self._cycles % FILTER_SAMPLE_EVERY == 0

        # Les COMPTEURS partent à chaque cycle, le DÉTAIL est échantillonné.
        # Sans ça, un bras qui rejette absolument tout n'écrit aucune ligne et
        # apparaît « sans données » — indiscernable d'un bras que rien
        # n'atteint, alors que c'est exactement le cas qu'on cherche à voir.
        rejected = len(evaluation.result.rejected)
        kept = len(evaluation.result.candidates)
        if rejected or kept:
            self.record(
                arm, "", "", "filtres_total", kept > 0, "",
                {"kept": kept, "rejected": rejected},
            )

        for candidate in evaluation.result.rejected:
            if not sample:
                continue
            self.record(
                arm, candidate.token_address, candidate.symbol, "filtres",
                False, candidate.rejected_reason or "?",
            )

        for candidate in evaluation.result.candidates:
            self.record(
                arm, candidate.token_address, candidate.symbol, "filtres", True
            )
            above = candidate.alpha_score_absolute >= threshold
            self.record(
                arm, candidate.token_address, candidate.symbol, "seuil_alpha", above,
                "" if above else f"alpha {candidate.alpha_score_absolute:.0f} < {threshold:g}",
                {"alpha": candidate.alpha_score_absolute},
            )

    def record_exit(
        self,
        arm: str,
        position: Any,
        price: float,
        reason: str,
        pnl_pct: float,
        liquidity_drop_pct: Optional[float] = None,
    ) -> None:
        """Une sortie, avec ce qu'on aurait pu prendre au lieu de ça.

        `peak_pct` est le plus haut atteint PENDANT la détention. L'écart avec
        le P&L réalisé est l'argent laissé sur la table, et c'est la seule
        mesure qui dise si une règle de sortie est trop lente. Une sortie à
        +5% sur un trade monté à +180% n'est pas un gain, c'est un échec de
        timing que le journal seul présente comme une réussite.
        """
        peak = getattr(position, "high_water_pct", None)
        self.record(
            arm, position.token_address, position.symbol, "sortie", True, reason,
            {
                "pnl_pct": round(pnl_pct, 2),
                "peak_pct": round(peak, 2) if peak is not None else None,
                "laisse_sur_table": (
                    round(peak - pnl_pct, 2) if peak is not None else None
                ),
                "minutes": round(position.duration_minutes(), 1),
                "liquidity_drop_pct": liquidity_drop_pct,
                "price": price,
            },
        )

    def record_liquidity_alert(
        self, arm: str, position: Any, drop_pct: float, acted: bool
    ) -> None:
        """Une chute de liquidité observée, suivie ou non d'une sortie.

        Le rug se joue en secondes. Journaliser les chutes qui n'ont PAS
        déclenché de sortie est la seule façon de savoir si le seuil de -50%
        est trop permissif : une position qui a vu -40% puis est morte aurait
        été sauvée par un seuil plus bas.
        """
        self.record(
            arm, position.token_address, position.symbol, "alerte_liquidite", acted,
            f"liquidité {drop_pct:.0f}%" + ("" if acted else " — sous le seuil de rug"),
            {"liquidity_drop_pct": round(drop_pct, 1)},
        )

    def flush(self) -> int:
        if not self.rows:
            return 0
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        written = len(self.rows)
        with open(self.path, "a", encoding="utf-8") as fh:
            for row in self.rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.rows.clear()
        return written

    @property
    def sampling_note(self) -> str:
        return (
            f"les rejets de filtres sont échantillonnés 1 cycle sur "
            f"{FILTER_SAMPLE_EVERY} — les proportions restent justes, les "
            f"volumes absolus sont à multiplier par {FILTER_SAMPLE_EVERY}"
        )


def read_funnel(path: str, since: Optional[float] = None) -> list[dict[str, Any]]:
    """Lignes du journal d'entonnoir. Ligne corrompue ignorée."""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since is None or (row.get("ts") or 0) >= since:
                rows.append(row)
    return rows


def funnel_by_arm(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, dict[str, int]]]:
    """Passages et rejets par bras et par porte.

    Les lignes `filtres_total` sont des COMPTEURS exhaustifs ; les lignes
    `filtres` sont un échantillon détaillé. Additionner les deux compterait
    deux fois — on prend les compteurs pour la porte « filtres » et on garde
    l'échantillon pour les motifs seulement.
    """
    counts: dict[str, dict[str, dict[str, int]]] = {}
    for row in rows:
        arm = row.get("arm", "?")
        gate = row.get("gate", "?")
        if gate == "filtres":
            continue  # détail échantillonné : motifs uniquement
        if gate == "filtres_total":
            bucket = counts.setdefault(arm, {}).setdefault(
                "filtres", {"passed": 0, "failed": 0}
            )
            bucket["passed"] += int(row.get("kept") or 0)
            bucket["failed"] += int(row.get("rejected") or 0)
            continue
        bucket = counts.setdefault(arm, {}).setdefault(gate, {"passed": 0, "failed": 0})
        bucket["passed" if row.get("passed") else "failed"] += 1
    return counts


def top_reasons(
    rows: Iterable[dict[str, Any]], arm: Optional[str] = None, limit: int = 8
) -> list[tuple[str, int]]:
    """Motifs de rejet les plus fréquents, normalisés.

    Les raisons portent des valeurs (« liquidité 7271$ < 25000$ ») qui rendent
    chaque message unique et le comptage inutile : on tronque au motif.
    """
    counter: Counter = Counter()
    for row in rows:
        if row.get("passed") or (arm and row.get("arm") != arm):
            continue
        # `filtres_total` est un compteur sans motif : l'inclure produirait
        # une ligne « ? » en tête du classement, qui n'apprend rien.
        if row.get("gate") == "filtres_total":
            continue
        counter[_normalise(row.get("reason", "?"))] += 1
    return counter.most_common(limit)


def _normalise(reason: str) -> str:
    """« liquidité 7271$ < 25000$ » -> « liquidité < seuil »."""
    import re

    if not reason:
        return "?"
    sans_nombres = re.sub(r"[-+]?\d[\d\s.,]*%?\$?", "N", reason)
    return re.sub(r"\s+", " ", sans_nombres).strip()


def recent_flow(path: str, arm: str, cycles: int = 20) -> Optional[float]:
    """Candidats retenus par cycle, médiane sur les derniers cycles.

    C'est la mesure qui manquait à l'apprentissage. Sans elle, resserrer un
    filtre est toujours « prudent » — alors que sur un flux déjà nul c'est
    l'inverse : on garantit de ne plus jamais collecter la donnée qui
    permettrait de savoir si le resserrage était justifié.

    `None` = aucune mesure disponible. Ne doit pas bloquer : même invariant
    que le reste du pipeline.
    """
    rows = [
        r for r in read_funnel(path)
        if r.get("gate") == "filtres_total" and r.get("arm") == arm
    ]
    if not rows:
        return None
    recents = rows[-cycles:]
    kept = sorted(int(r.get("kept") or 0) for r in recents)
    return float(kept[len(kept) // 2])


def blocking_gate(counts: dict[str, dict[str, int]]) -> Optional[tuple[str, int, int]]:
    """La porte qui coûte le plus de candidats à ce bras.

    Cherche le plus gros DÉCROCHAGE en valeur absolue, pas le plus fort taux :
    une porte qui rejette 100 % de 2 candidats compte moins qu'une qui rejette
    60 % de 200. La correction doit viser le volume.
    """
    worst = None
    for gate in GATES:
        bucket = counts.get(gate)
        if not bucket or not bucket["failed"]:
            continue
        if worst is None or bucket["failed"] > worst[1]:
            worst = (gate, bucket["failed"], bucket["passed"])
    return worst
