"""Microstructure — ce qu'on peut mesurer, et ce qu'on ne peut PAS.

CE QUE LA SPEC DEMANDAIT : net USD inflow, bid-ask spread, depth.

CE QUE LE DÉPÔT PERMET RÉELLEMENT. Solana n'a pas de carnet d'ordres central :
les memecoins se traitent sur des AMM (pools à produit constant). Il n'existe
donc littéralement pas de bid-ask ni de profondeur au sens d'un carnet, et
`Snapshot` ne porte que `(ts, price, volume_5m, liquidity)`. Inventer un
spread depuis ces champs produirait un nombre plausible et faux — exactement
le genre de chiffre que ce dépôt passe son temps à débusquer.

Les trois grandeurs sont donc remplacées par leurs équivalents AMM, mesurés :

  spread     -> `round_trip_cost_pct` de Jupiter, un devis d'aller-retour réel.
                C'est LE coût de franchissement, l'analogue exact du spread
                pour l'exécution. Mesuré de 1,85 % à 23,62 % sur 16 tokens,
                sans corrélation avec la liquidité affichée.
  depth      -> la liquidité du pool, seule profondeur qui existe ici.
  net inflow -> la DÉRIVE DE LIQUIDITÉ. Sur un AMM, la liquidité monte quand
                on ajoute au pool et suit le prix ; sa variation nette sur une
                fenêtre est le meilleur substitut disponible au flux acheteur.

CE QU'IL FAUT SAVOIR AVANT DE S'EN SERVIR. La dérive de liquidité mélange deux
causes : des dépôts/retraits de LP, et le rééquilibrage automatique du pool
quand le prix bouge. Une hausse de liquidité concomitante d'une hausse de prix
n'est donc PAS une preuve d'afflux acheteur. C'est pour ça que l'agent rend
aussi `price_drift_pct` : lire les deux ensemble est la seule lecture honnête,
et l'agent refuse de conclure à leur place.
"""

import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from src.agents import _journal
from src.analysis.technical import Snapshot

# Au-delà, l'aller-retour rend la position ingérable quelle que soit la
# stratégie. Importée plutôt que recopiée : deux seuils qui divergent
# silencieusement feraient dire à l'agent l'inverse de la garde économique.
from src.core.economics import MAX_ACCEPTABLE_ROUND_TRIP_PCT

DEFAULT_WINDOW_SECONDS = 300.0
MIN_SNAPSHOTS = 3


@dataclass(frozen=True)
class MicrostructureReading:
    """État du carnet — au sens AMM, seul sens qui existe sur Solana."""

    liquidity_drift_pct: Optional[float]
    price_drift_pct: Optional[float]
    depth_usd: Optional[float]
    round_trip_pct: Optional[float]
    volume_over_liquidity: Optional[float]
    window_seconds: float
    samples: int
    reason: str

    @property
    def tradable(self) -> Optional[bool]:
        """`None` = pas de devis. Une donnée absente ne rejette jamais."""
        if self.round_trip_pct is None:
            return None
        return self.round_trip_pct <= MAX_ACCEPTABLE_ROUND_TRIP_PCT

    @property
    def impulse(self) -> Optional[bool]:
        """Liquidité ET prix qui montent, sur un carnet franchissable.

        Les deux conditions de dérive sont exigées ENSEMBLE parce qu'aucune
        ne suffit : la liquidité seule peut monter par simple rééquilibrage,
        et le prix seul peut monter sur un pool qui se vide — ce qui est le
        profil d'un rug en cours, pas d'une impulsion.
        """
        if self.liquidity_drift_pct is None or self.price_drift_pct is None:
            return None
        if self.tradable is False:
            return False
        return self.liquidity_drift_pct > 0 and self.price_drift_pct > 0

    def as_dict(self) -> dict[str, Any]:
        def arrondi(value: Optional[float], n: int = 2) -> Optional[float]:
            return round(value, n) if value is not None else None

        return {
            "liquidity_drift_pct": arrondi(self.liquidity_drift_pct),
            "price_drift_pct": arrondi(self.price_drift_pct),
            "depth_usd": arrondi(self.depth_usd),
            "round_trip_pct": arrondi(self.round_trip_pct),
            "volume_over_liquidity": arrondi(self.volume_over_liquidity, 3),
            "window_seconds": self.window_seconds,
            "samples": self.samples,
            "tradable": self.tradable,
            "impulse": self.impulse,
            "reason": self.reason,
        }


def _drift_pct(avant: float, apres: float) -> Optional[float]:
    if avant <= 0:
        return None
    return 100.0 * (apres - avant) / avant


class MicrostructureAgent:
    """Lit l'état du pool. Ne décide ni entrée ni sortie."""

    def __init__(
        self,
        jupiter: Any = None,
        log_path: Optional[str] = None,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
    ):
        self.jupiter = jupiter
        self.log_path = log_path
        self.window_seconds = window_seconds

    def _round_trip(
        self, token_address: str, size_usd: float, sol_price_usd: float
    ) -> Optional[float]:
        """Devis d'aller-retour. Toute panne rend `None`, jamais une valeur.

        `round_trip_cost_pct` fait deux appels réseau. Une exception ici ne doit
        pas remonter : l'invariant « la boucle ne meurt jamais » vaut aussi pour
        un agent de mesure, et une microstructure inconnue vaut mieux qu'un
        cycle perdu.
        """
        if self.jupiter is None or not getattr(self.jupiter, "enabled", False):
            return None
        try:
            return self.jupiter.round_trip_cost_pct(
                token_address, size_usd, sol_price_usd
            )
        except Exception:
            return None

    def read(
        self,
        snapshots: Sequence[Snapshot],
        token_address: Optional[str] = None,
        size_usd: float = 20.0,
        sol_price_usd: float = 0.0,
    ) -> MicrostructureReading:
        recents = sorted(snapshots, key=lambda s: s.ts)
        if len(recents) >= MIN_SNAPSHOTS:
            limite = recents[-1].ts - self.window_seconds
            fenetre = [s for s in recents if s.ts >= limite] or recents[-MIN_SNAPSHOTS:]
        else:
            fenetre = recents

        if len(fenetre) < MIN_SNAPSHOTS:
            return MicrostructureReading(
                liquidity_drift_pct=None, price_drift_pct=None, depth_usd=None,
                round_trip_pct=None, volume_over_liquidity=None,
                window_seconds=self.window_seconds, samples=len(fenetre),
                reason=(
                    f"{len(fenetre)} snapshots sur la fenêtre, il en faut "
                    f"{MIN_SNAPSHOTS}"
                ),
            )

        premier, dernier = fenetre[0], fenetre[-1]
        depth = dernier.liquidity
        round_trip = (
            self._round_trip(token_address, size_usd, sol_price_usd)
            if token_address else None
        )

        return MicrostructureReading(
            liquidity_drift_pct=_drift_pct(premier.liquidity, dernier.liquidity),
            price_drift_pct=_drift_pct(premier.price, dernier.price),
            depth_usd=depth,
            round_trip_pct=round_trip,
            volume_over_liquidity=(
                dernier.volume_5m / depth if depth > 0 else None
            ),
            window_seconds=dernier.ts - premier.ts,
            samples=len(fenetre),
            reason=(
                f"{len(fenetre)} snapshots sur {dernier.ts - premier.ts:.0f} s"
                + ("" if round_trip is not None else " — devis indisponible")
            ),
        )

    def observe(
        self,
        token_address: str,
        symbol: str,
        snapshots: Sequence[Snapshot],
        size_usd: float = 20.0,
        sol_price_usd: float = 0.0,
    ) -> MicrostructureReading:
        reading = self.read(snapshots, token_address, size_usd, sol_price_usd)
        if self.log_path:
            _journal.append(self.log_path, {
                "ts": time.time(),
                "token_address": token_address,
                "symbol": symbol,
                **reading.as_dict(),
            })
        return reading
