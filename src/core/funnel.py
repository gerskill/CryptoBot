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
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Optional

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

# ROTATION. Mesuré le 2026-08-02 : 61 922 lignes pour 905 cycles, soit ~68
# lignes par cycle, ~12 Mo par jour à 960 cycles/jour. Sans borne le fichier
# grandit indéfiniment — et `recent_flow` le relisait EN ENTIER pour n'en
# garder que les 20 dernières lignes utiles.
#
# Une seule génération conservée : `recent_flow` ne remonte que quelques
# milliers de lignes, et l'analyse historique se fait sur le journal de
# trades, pas ici. 8 Mo ≈ 45 000 lignes, très au-dessus de ce que la lecture
# par la fin réclame.
FUNNEL_MAX_BYTES = 8 * 1024 * 1024
ROTATED_SUFFIX = ".1"

# Lignes à remonter pour UN cycle d'UN bras. Mesuré : 61 922 lignes totales
# pour 905 lignes `filtres_total` par bras, soit 68 lignes par cycle et par
# bras. 250 laisse une marge de 3,7× — assez pour absorber un cycle chargé
# (beaucoup de candidats retenus = beaucoup de lignes `seuil_alpha`) sans
# jamais relire 11 Mo.
TAIL_LINES_PER_CYCLE = 250


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
        rotate(self.path)
        return written

    @property
    def sampling_note(self) -> str:
        return (
            f"les rejets de filtres sont échantillonnés 1 cycle sur "
            f"{FILTER_SAMPLE_EVERY} — les proportions restent justes, les "
            f"volumes absolus sont à multiplier par {FILTER_SAMPLE_EVERY}"
        )


def rotated_path(path: str) -> str:
    """`data/funnel_log.jsonl` -> `data/funnel_log.1.jsonl`."""
    base, ext = os.path.splitext(path)
    return f"{base}{ROTATED_SUFFIX}{ext}"


def rotate(path: str, max_bytes: Optional[int] = None) -> bool:
    """Bascule le journal s'il dépasse la taille. True si une bascule a eu lieu.

    `os.path.getsize` est en O(1) : le contrôle peut se faire à chaque flush
    sans relire quoi que ce soit. `os.replace` est atomique — un lecteur voit
    l'ancien fichier ou le nouveau, jamais un état intermédiaire.

    Le seuil est résolu à l'APPEL, pas à la définition : une valeur par défaut
    figée dans la signature rendrait `FUNNEL_MAX_BYTES` impossible à ajuster,
    y compris depuis un test.
    """
    limite = FUNNEL_MAX_BYTES if max_bytes is None else max_bytes
    try:
        if os.path.getsize(path) <= limite:
            return False
    except OSError:
        return False
    os.replace(path, rotated_path(path))
    return True


def _iter_lines(path: str) -> Iterator[str]:
    """Lignes de la génération précédente PUIS de la courante, dans l'ordre.

    Sans la génération précédente, `recent_flow` deviendrait aveugle juste
    après une rotation — et un flux inconnu autorise à resserrer un filtre,
    exactement ce que le garde-fou cherche à empêcher.
    """
    for candidate in (rotated_path(path), path):
        if not os.path.exists(candidate):
            continue
        with open(candidate, encoding="utf-8") as fh:
            yield from fh


def read_funnel(
    path: str, since: Optional[float] = None, tail: Optional[int] = None
) -> list[dict[str, Any]]:
    """Lignes du journal d'entonnoir. Ligne corrompue ignorée.

    `tail` borne la lecture aux N dernières lignes brutes. Sans lui, un appelant
    qui ne veut que la fin du fichier paie le parsing JSON de tout l'historique
    — 11 Mo mesurés au 2026-08-02, et ça ne fait que croître.
    """
    lines: Iterable[str] = _iter_lines(path)
    if tail is not None:
        lines = deque(lines, maxlen=tail)
    rows = []
    for line in lines:
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

    Appelée depuis `LearningEngine._starving`, donc à CHAQUE `_bounded_set`, et
    pour chacun des 7 bras. Lire tout le fichier pour n'en garder que 20 lignes
    coûtait un parsing complet à chaque ajustement : d'où le `tail`.
    """
    rows = [
        r for r in read_funnel(path, tail=cycles * TAIL_LINES_PER_CYCLE)
        if r.get("gate") == "filtres_total" and r.get("arm") == arm
    ]
    if not rows:
        return None
    recents = rows[-cycles:]
    kept = sorted(int(r.get("kept") or 0) for r in recents)
    return float(kept[len(kept) // 2])


def inactivity_snapshot(
    path: str, arm: str, tail: Optional[int] = None, motifs: int = 5
) -> tuple[Optional[int], list[str]]:
    """(cycles depuis la dernière entrée, motifs de rejet par ordre de poids).

    PLUSIEURS MOTIFS, PAS UN SEUL. Le motif dominant peut être déjà à sa borne
    — c'est le cas de `quality`, bloqué sur l'âge à 24 h, son plafond. S'en
    tenir au premier laisserait un bras inactif indéfiniment sans rien tenter,
    alors qu'un autre de ses seuils a peut-être encore de la marge.

    COMPTÉ EN CYCLES, PAS EN HEURES, et c'est la seule mesure honnête ici : le
    bot est arrêté et relancé. « 12 h sans entrée » confond une stratégie qui
    ne trouve rien avec un processus qui ne tournait pas. Un cycle évalué est
    une occasion réellement offerte au bras, et refusée par lui.

    `(None, [])` = ce bras n'a jamais été évalué. Un bras qui n'est JAMAIS
    entré rend son nombre de cycles vus : c'est une borne inférieure, et c'est
    exactement le cas qui doit déclencher un relâchement.

    LES DEUX SORTENT D'UNE SEULE LECTURE. Appeler deux fonctions relirait le
    fichier deux fois — et surtout les jugerait sur deux fenêtres différentes
    si le journal bouge entre les deux, donc desserrerait un seuil qui n'était
    pas celui qui bloquait.
    """
    rows = read_funnel(path, tail=tail)
    cycles = [r for r in rows if r.get("gate") == "filtres_total" and r.get("arm") == arm]
    if not cycles:
        return None, []

    entrees = [
        r for r in rows
        if r.get("gate") == "entree" and r.get("arm") == arm and r.get("passed")
    ]
    if entrees:
        dernier = max(r.get("ts") or 0 for r in entrees)
        depuis = sum(1 for r in cycles if (r.get("ts") or 0) > dernier)
    else:
        depuis = len(cycles)

    return depuis, [motif for motif, _ in top_reasons(rows, arm=arm, limit=motifs)]


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
