"""Contrefactuel de timing — et si on était entré 30 s plus tôt ?

LE CHIFFRE QUI JUSTIFIE CET AGENT. Sur les 26 positions instrumentées du
2026-08-02 :

    +10 % de pic atteint : 42 %       -10 % de creux atteint : 96 %
    pic médian           : +4,9 %     creux médian          : -20,9 %

**96 % des trades touchent -10 %.** Trois lectures sont compatibles avec ça,
et elles appellent des corrections opposées :

  a) la sélection est mauvaise — il faut filtrer autrement ;
  b) le stop est trop serré — il faut l'élargir ;
  c) **l'entrée est trop tard** — on achète le haut d'une impulsion déjà faite,
     et le creux qui suit est la respiration normale.

Rien dans le dépôt ne distingue (c) des deux autres. Le journal enregistre le
prix d'entrée retenu, jamais ce que valait le token dix secondes plus tôt. Cet
agent rejoue chaque entrée un, deux et trois cycles plus tôt sur l'historique
de prix déjà collecté, et rend l'écart. Si (c) est vraie, elle est la seule
des trois qui se corrige sans toucher ni aux filtres ni au risque.

CE QUE CE REJEU NE SAIT PAS — à lire avant d'agir dessus :

1. LA GRANULARITÉ EST CELLE DU SCAN, ET ELLE BORNE LA QUESTION. `PriceHistory`
   enregistre un point par cycle de scan (90 s), et un par tick de monitoring
   (5 s) SEULEMENT une fois la position ouverte. Avant l'entrée, la résolution
   est donc de 90 s : demander « et à t-10 s ? » n'a pas de réponse observée.
   D'où des décalages en multiples du cycle. Voir `DEFAULT_OFFSETS`.
2. ENTRER PLUS TÔT N'EST PAS GRATUIT. Un cycle plus tôt, le signal qui a
   déclenché l'entrée n'existait pas encore : ce rejeu mesure un prix, pas une
   décision qu'on aurait pu prendre. C'est une borne supérieure du gain de
   timing, jamais un gain réalisable.
3. LE PRIX N'EST PAS L'EXÉCUTION. Ni slippage ni frais ici. L'aller-retour
   médian mesuré est de 3,06 % — un avantage de timing inférieur à ça
   n'existe pas en pratique.
4. UNE ENTRÉE PLUS TÔT AURAIT DÉPLACÉ LE PRIX. Marginal à 20 $ sur 20 K de
   liquidité, mais non nul, et toujours dans le sens défavorable.

D'où la règle de restitution : cet agent rend des ÉCARTS DE PRIX horodatés et
n'ajuste aucun paramètre. `ParameterSuggestionAgent`, quand il existera,
lira ce journal — et devra porter les quatre réserves ci-dessus.
"""

import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from src.agents import _journal
from src.analysis.technical import Snapshot

# Cadence du scan. C'est elle qui fixe la résolution de tout ce module.
SCAN_CYCLE_SECONDS = 90.0

# Décalages rejoués, en secondes. Négatif = avant le signal.
#
# POURQUOI PAS -30 / -10 / +10, QUI ÉTAIT LE RÉFLEXE. Ces décalages sont plus
# FINS QUE L'ÉCHANTILLONNAGE. Avant l'ouverture d'une position, le seul
# historique disponible vient du scan, qui tourne à 90 s : au moment d'entrer,
# le dernier snapshot a déjà entre 0 et 90 s. Un décalage de -30 s tombe donc
# quasi systématiquement au-delà de la tolérance, et l'agent rendait `null`
# sur les trois décalages — vérifié de bout en bout avant de corriger.
#
# Les décalages sont donc des MULTIPLES DU CYCLE : ce sont les seuls instants
# où un prix a réellement été observé plutôt qu'inventé.
#
# PAS DE DÉCALAGE POSITIF ICI. « Et si on était entré 90 s plus tard ? » est
# une bonne question, mais à l'ouverture cet instant est dans le futur. Le
# monitoring échantillonne ensuite à 5 s : c'est de LÀ que le côté « plus
# tard » devra être mesuré, pas d'ici.
DEFAULT_OFFSETS = (
    -3 * SCAN_CYCLE_SECONDS,
    -2 * SCAN_CYCLE_SECONDS,
    -1 * SCAN_CYCLE_SECONDS,
)
# Au-delà, le snapshot le plus proche est trop loin pour prétendre décrire le
# prix à l'instant voulu. La moitié d'un cycle : tout instant visé est alors à
# portée d'un snapshot réel, ou bien encadré par deux.
MAX_GAP_SECONDS = SCAN_CYCLE_SECONDS / 2
# Sous ce seuil, l'écart de timing est noyé par l'aller-retour médian mesuré
# (3,06 %) : le signaler comme une opportunité serait trompeur.
MEANINGFUL_EDGE_PCT = 3.06


@dataclass(frozen=True)
class PricePoint:
    """Prix reconstitué à un instant, et à quel point on y croit."""

    offset_sec: float
    price: Optional[float]
    gap_sec: Optional[float]
    interpolated: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "offset_sec": self.offset_sec,
            "price": self.price,
            "gap_sec": round(self.gap_sec, 1) if self.gap_sec is not None else None,
            "interpolated": self.interpolated,
        }


@dataclass(frozen=True)
class TimingVerdict:
    """Ce que l'entrée aurait coûté à d'autres instants."""

    token_address: str
    symbol: str
    entry_price: float
    entry_ts: float
    points: tuple[PricePoint, ...]
    deltas_pct: dict[str, Optional[float]]
    best_offset_sec: Optional[float]
    best_edge_pct: Optional[float]
    reason: str

    @property
    def meaningful(self) -> bool:
        """L'avantage dépasse-t-il le coût de franchissement médian ?"""
        return (
            self.best_edge_pct is not None
            and self.best_edge_pct >= MEANINGFUL_EDGE_PCT
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "token_address": self.token_address,
            "symbol": self.symbol,
            "entry_price": self.entry_price,
            "entry_ts": self.entry_ts,
            "points": [p.as_dict() for p in self.points],
            "deltas_pct": self.deltas_pct,
            "best_offset_sec": self.best_offset_sec,
            "best_edge_pct": (
                round(self.best_edge_pct, 2) if self.best_edge_pct is not None else None
            ),
            "meaningful": self.meaningful,
            "reason": self.reason,
        }


def price_at(
    snapshots: Sequence[Snapshot], target_ts: float, max_gap: float = MAX_GAP_SECONDS
) -> tuple[Optional[float], Optional[float], bool]:
    """(prix, écart au snapshot le plus proche, interpolé ?) à un instant.

    Interpole linéairement entre les deux snapshots qui encadrent l'instant.
    Hors de l'intervalle couvert, ne se rabat PAS sur l'extrémité la plus
    proche au-delà de `max_gap` : extrapoler un prix de memecoin sur une
    minute produirait un chiffre inventé, et c'est précisément ce que cet
    agent doit éviter pour rester utilisable comme preuve.
    """
    points = sorted(snapshots, key=lambda s: s.ts)
    if not points:
        return None, None, False

    avant = [s for s in points if s.ts <= target_ts]
    apres = [s for s in points if s.ts >= target_ts]

    if avant and apres:
        gauche, droite = avant[-1], apres[0]
        if gauche.ts == droite.ts:
            return gauche.price, 0.0, False
        span = droite.ts - gauche.ts
        poids = (target_ts - gauche.ts) / span
        prix = gauche.price + poids * (droite.price - gauche.price)
        ecart = min(target_ts - gauche.ts, droite.ts - target_ts)
        return prix, ecart, True

    proche = (avant or apres)[-1] if avant else apres[0]
    ecart = abs(proche.ts - target_ts)
    if ecart > max_gap:
        return None, ecart, False
    return proche.price, ecart, False


class CounterfactualTimingAgent:
    """Rejoue chaque entrée à d'autres instants. N'ajuste aucun paramètre."""

    def __init__(
        self,
        log_path: Optional[str] = None,
        offsets: Sequence[float] = DEFAULT_OFFSETS,
        max_gap: float = MAX_GAP_SECONDS,
    ):
        self.log_path = log_path
        self.offsets = tuple(offsets)
        self.max_gap = max_gap

    def replay(
        self,
        token_address: str,
        symbol: str,
        entry_price: float,
        entry_ts: float,
        snapshots: Sequence[Snapshot],
    ) -> TimingVerdict:
        """Écart entre le prix payé et le prix à chaque décalage.

        Le DELTA est exprimé comme un avantage : positif = on aurait acheté
        MOINS CHER à ce décalage. C'est le sens qui compte pour décider s'il
        faut avancer ou retarder l'entrée, et l'inverse se lit mal.
        """
        points: list[PricePoint] = []
        deltas: dict[str, Optional[float]] = {}

        for offset in self.offsets:
            prix, ecart, interpole = price_at(
                snapshots, entry_ts + offset, self.max_gap
            )
            points.append(PricePoint(offset, prix, ecart, interpole))
            if prix is None or prix <= 0 or entry_price <= 0:
                deltas[f"{offset:+.0f}s"] = None
                continue
            deltas[f"{offset:+.0f}s"] = round(
                100.0 * (entry_price - prix) / entry_price, 2
            )

        connus = {k: v for k, v in deltas.items() if v is not None}
        if connus:
            meilleur_libelle = max(connus, key=lambda k: connus[k])
            meilleur_edge = connus[meilleur_libelle]
            meilleur_offset = next(
                p.offset_sec for p in points
                if f"{p.offset_sec:+.0f}s" == meilleur_libelle
            )
            raison = (
                f"{len(connus)}/{len(self.offsets)} décalages reconstitués — "
                f"meilleur {meilleur_libelle} à {meilleur_edge:+.2f} %"
            )
        else:
            meilleur_offset = None
            meilleur_edge = None
            raison = (
                f"aucun décalage reconstituable — historique trop clairsemé "
                f"(écart max toléré {self.max_gap:.0f} s)"
            )

        return TimingVerdict(
            token_address=token_address, symbol=symbol, entry_price=entry_price,
            entry_ts=entry_ts, points=tuple(points), deltas_pct=deltas,
            best_offset_sec=meilleur_offset, best_edge_pct=meilleur_edge,
            reason=raison,
        )

    def observe(
        self,
        token_address: str,
        symbol: str,
        entry_price: float,
        entry_ts: float,
        snapshots: Sequence[Snapshot],
    ) -> TimingVerdict:
        """Rejoue et journalise, y compris quand rien n'est reconstituable.

        Journaliser les échecs de reconstitution est ce qui permettra de savoir
        si l'historique de prix doit être conservé plus longtemps — sans eux,
        un journal vide se lirait « pas d'avantage de timing » au lieu de
        « pas de données ».
        """
        verdict = self.replay(token_address, symbol, entry_price, entry_ts, snapshots)
        if self.log_path:
            _journal.append(self.log_path, {"ts": time.time(), **verdict.as_dict()})
        return verdict


def summarise(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Agrège un journal de contrefactuels en une réponse actionnable.

    Rend, PAR DÉCALAGE, l'avantage médian et la part des entrées où il dépasse
    le coût de franchissement. La médiane et non la moyenne : un token qui a
    fait +400 % en dix secondes écraserait toute moyenne, et c'est exactement
    le régime de queue de ce marché.
    """
    par_offset: dict[str, list[float]] = {}
    for row in rows:
        for libelle, valeur in (row.get("deltas_pct") or {}).items():
            if valeur is not None:
                par_offset.setdefault(libelle, []).append(float(valeur))

    resume = {}
    for libelle, valeurs in sorted(par_offset.items()):
        valeurs.sort()
        mediane = valeurs[len(valeurs) // 2]
        gagnants = sum(1 for v in valeurs if v >= MEANINGFUL_EDGE_PCT)
        resume[libelle] = {
            "n": len(valeurs),
            "median_edge_pct": round(mediane, 2),
            "share_above_cost": round(100.0 * gagnants / len(valeurs), 1),
        }
    return {
        "offsets": resume,
        "cost_threshold_pct": MEANINGFUL_EDGE_PCT,
        "note": (
            "avantage positif = on aurait acheté moins cher à ce décalage. "
            "Borne SUPÉRIEURE : à t-30 s le signal n'existait pas encore, et "
            "ni le slippage ni l'impact de l'ordre ne sont comptés."
        ),
    }
