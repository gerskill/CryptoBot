"""Surveillance du créateur PENDANT que la position est ouverte — anti-slow-rug.

LE TROU QUE ÇA BOUCHE. Le dépôt a deux gardes contre le rug, et toutes deux
regardent le mauvais moment ou la mauvaise chose :

  `DevHistoryAgent`   note le créateur À L'ENTRÉE, sur six signaux figés
                      (`creator_token_status`, `bundler_rate`…). Après
                      l'ouverture, plus rien n'est jamais remesuré.
  `RUG_LIQUIDITY_DROP_PCT`  détecte le HARD rug : la liquidité s'effondre de
                      50 % en quelques secondes et la position sort.

Entre les deux vit le SLOW RUG : le créateur envoie 2 % de la supply à des
wallets frais toutes les dix minutes, ceux-ci vendent dans le carnet. La
liquidité ne s'effondre jamais d'un coup — elle s'érode. Le prix descend
lentement, le trailing stop suit la descente, et les bras à horizon long
(`runner`, `quality`, `max_hold_time_minutes` à 240) encaissent la totalité.

CE QUE CE MODULE MESURE, ET POURQUOI C'EST LA BONNE MESURE. Le solde du
CRÉATEUR, en % de la supply, comparé à ce qu'il était à l'ouverture de la
position. Cette mesure attrape la variante « wallets frais » À SA SOURCE :
peu importe que le créateur vende lui-même ou qu'il distribue à dix wallets
neufs qui vendront pour lui, dans les deux cas SON solde baisse. Suivre les
wallets frais un par un demanderait de remonter les flux de fonds, donc
`getSignaturesForAddress` et `getTransaction` — absents d'`ALLOWED_RPC_METHODS`
(voir `src/apis/helius.py`) et coûteux en appels. Surveiller la source coûte
deux appels et voit la même chose plus tôt.

CE QUE CE MODULE NE VOIT PAS, ET LE DIT. Le créateur n'est mesurable que s'il
figure dans les 20 plus gros comptes rendus par `getTokenLargestAccounts`. Un
créateur qui a réparti sa mise sur cinq wallets AVANT l'ouverture est déjà
invisible au moment de la ligne de base — l'agent rend alors « non mesuré » et
ne déclenche jamais. « Non mesuré » et « ne distribue pas » sont deux états
distincts, comme partout ailleurs dans le dépôt.

PIÈGE ÉVITÉ : `HeliusAPI.get_dev_wallet_pct` rend `0.0` quand le créateur
n'est PAS dans le top 20. Pour un filtre d'entrée c'est acceptable (un
créateur invisible ne détient pas grand-chose) ; ici ce serait faux et
dangereux — une ligne de base à `0.0` rendrait toute baisse impossible à
détecter, et un passage de « visible » à « 0.0 » ressemblerait à une vente
totale alors qu'il n'a peut-être bougé que d'un rang. Ce module fait donc sa
propre lecture et distingue explicitement « absent du top » de « détient 0 ».

COÛT. Deux appels RPC (`getTokenLargestAccounts` + `getTokenSupply`) par token
surveillé et par contrôle, plus UN appel `getAsset` par token, mis en cache
définitivement — l'adresse du créateur ne change pas. Le contrôle est cadencé
(`CHECK_INTERVAL_SECONDS`) et fait PAR TOKEN, pas par position : sept bras sur
le même token partagent la même mesure, exactement comme ils partagent déjà
le prix dans `_monitor_positions`.

CE MODULE DÉCIDE, et c'est pourquoi il vit dans `src/core/` et non dans
`src/agents/` — ce paquet-là déclare en tête que ses agents ne décident rien.
"""

import time
from dataclasses import dataclass
from typing import Any, Optional

# Cadence des contrôles, par token. Un slow rug se joue sur des dizaines de
# minutes (le schéma décrit est « 2 % toutes les 10 minutes ») : contrôler
# toutes les 3 minutes laisse 3 à 5 mesures avant qu'une distribution
# sérieuse soit consommée, sans transformer le monitoring 5 s en robinet RPC.
CHECK_INTERVAL_SECONDS = 180

# DEUX CONDITIONS CUMULATIVES POUR DÉCLENCHER, et aucune ne suffit seule.
#
#   relative   30 % de la mise du créateur partie. C'est la mesure qui a du
#              sens : vendre 1,5 point de supply quand on en détient 3 est une
#              liquidation, la même vente sur 20 points est une réduction.
#   absolue    1 point de supply. Filtre le bruit — arrondis de `uiAmount`,
#              rotation de comptes, burn partiel — sur les créateurs à très
#              petite mise, où 30 % relatif peut ne rien représenter.
#
# NON CALIBRÉS SUR DES DONNÉES. Contrairement aux seuils de
# `src/core/correlation.py`, tirés de 1339 positions du journal, aucune
# trajectoire de solde de créateur n'a jamais été enregistrée ici : ce module
# est ce qui commence à les enregistrer. Les valeurs ci-dessous sont un point
# de départ prudent, à revoir sur le journal produit — et impérativement
# AVANT tout passage en LIVE.
CREATOR_DROP_RELATIVE = 0.30
CREATOR_DROP_ABSOLUTE_PTS = 1.0

# Sous cette part de supply, le créateur n'a plus rien à distribuer qui pèse
# sur le carnet : le surveiller produirait des alertes sans conséquence.
MIN_BASELINE_PCT = 0.5


@dataclass(frozen=True)
class CreatorSnapshot:
    """Ce que le créateur détenait à un instant donné.

    `pct is None` = NON MESURÉ (créateur inconnu, ou absent du top 20).
    `visible` distingue « absent du top 20 » de « présent avec 0 ».
    """

    creator: Optional[str]
    pct: Optional[float]
    visible: bool
    floor_pct: Optional[float]  # part du plus petit compte listé, borne haute
    measured_at: float

    @property
    def known(self) -> bool:
        return self.pct is not None


@dataclass(frozen=True)
class DumpVerdict:
    """Le créateur distribue-t-il depuis l'ouverture de la position ?"""

    dumping: bool
    reason: str
    baseline_pct: Optional[float] = None
    current_pct: Optional[float] = None
    drop_pts: Optional[float] = None
    drop_relative: Optional[float] = None
    # `True` quand la baisse est une BORNE INFÉRIEURE : le créateur est sorti
    # du top 20 et on ne sait que « au moins ça ».
    is_lower_bound: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "dumping": self.dumping,
            "reason": self.reason,
            "baseline_pct": self.baseline_pct,
            "current_pct": self.current_pct,
            "drop_pts": round(self.drop_pts, 3) if self.drop_pts is not None else None,
            "drop_relative": (
                round(self.drop_relative, 3) if self.drop_relative is not None else None
            ),
            "is_lower_bound": self.is_lower_bound,
        }


NOT_MEASURED = DumpVerdict(dumping=False, reason="non mesuré")


class DevWatchdog:
    """Suit le solde du créateur des tokens en position, et dit s'il distribue."""

    def __init__(
        self,
        helius: Any,
        interval_seconds: float = CHECK_INTERVAL_SECONDS,
        drop_relative: float = CREATOR_DROP_RELATIVE,
        drop_absolute_pts: float = CREATOR_DROP_ABSOLUTE_PTS,
        min_baseline_pct: float = MIN_BASELINE_PCT,
    ):
        self.helius = helius
        self.interval_seconds = interval_seconds
        self.drop_relative = drop_relative
        self.drop_absolute_pts = drop_absolute_pts
        self.min_baseline_pct = min_baseline_pct
        # L'adresse du créateur ne change jamais : un `getAsset` par token,
        # pour toute la vie du processus. Le `None` est mémorisé lui aussi,
        # sinon un token sans créateur exposé relancerait l'appel à chaque
        # contrôle.
        self._creators: dict[str, Optional[str]] = {}
        self._baseline: dict[str, CreatorSnapshot] = {}
        self._last_check: dict[str, float] = {}
        # Un token déjà signalé ne doit pas ré-alerter à chaque contrôle : le
        # verdict est rendu une fois, l'appelant en fait ce qu'il veut.
        self._flagged: set[str] = set()

    # --------------------------------------------------------------- lecture

    def _creator_of(self, token_address: str) -> Optional[str]:
        if token_address not in self._creators:
            try:
                self._creators[token_address] = self.helius.get_creator_address(token_address)
            except Exception:  # noqa: BLE001
                # Une panne de mesure ne doit jamais casser le monitoring.
                # Non mémorisée : le prochain contrôle réessaiera.
                return None
        return self._creators[token_address]

    def snapshot(self, token_address: str) -> CreatorSnapshot:
        """Solde courant du créateur, ou « non mesuré »."""
        now = time.time()
        creator = self._creator_of(token_address)
        if not creator:
            return CreatorSnapshot(None, None, False, None, now)

        try:
            holders = self.helius.get_top_holders(token_address)
        except Exception:  # noqa: BLE001
            return CreatorSnapshot(creator, None, False, None, now)
        if not holders:
            return CreatorSnapshot(creator, None, False, None, now)

        # Le plus petit compte listé borne ce que peut détenir quelqu'un
        # d'ABSENT de la liste : c'est ce qui permet de transformer une
        # disparition du top 20 en baisse minimale plutôt qu'en inconnue.
        floor_pct = min(float(h.get("pct") or 0.0) for h in holders)
        for holder in holders:
            if holder.get("address") == creator:
                return CreatorSnapshot(
                    creator, float(holder.get("pct") or 0.0), True, floor_pct, now
                )
        return CreatorSnapshot(creator, None, False, floor_pct, now)

    # ---------------------------------------------------------------- verdict

    def establish_baseline(self, token_address: str) -> CreatorSnapshot:
        """Fige la référence à l'ouverture. Idempotent : le premier gagne.

        Le premier gagne parce que sept bras peuvent ouvrir sur le même token
        à des instants différents ; une référence réécrite par le dernier
        entrant effacerait la distribution déjà survenue entre-temps.
        """
        if token_address not in self._baseline:
            self._baseline[token_address] = self.snapshot(token_address)
            self._last_check[token_address] = time.time()
        return self._baseline[token_address]

    def due(self, token_address: str, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        last = self._last_check.get(token_address)
        return last is None or (now - last) >= self.interval_seconds

    def check(self, token_address: str, force: bool = False) -> DumpVerdict:
        """Contrôle cadencé. Rend `NOT_MEASURED` tant qu'il n'y a rien à dire.

        `force` court-circuite la cadence — réservé aux tests et à un contrôle
        explicite ; la boucle, elle, respecte `CHECK_INTERVAL_SECONDS`.
        """
        if not force and not self.due(token_address):
            return NOT_MEASURED
        self._last_check[token_address] = time.time()

        baseline = self._baseline.get(token_address)
        if baseline is None:
            self._baseline[token_address] = self.snapshot(token_address)
            return NOT_MEASURED
        if not baseline.known:
            return DumpVerdict(
                dumping=False,
                reason="créateur absent du top 20 à l'ouverture — non mesurable",
            )
        if baseline.pct < self.min_baseline_pct:
            return DumpVerdict(
                dumping=False,
                reason=(
                    f"créateur à {baseline.pct:.2f} % de supply à l'ouverture, "
                    f"sous {self.min_baseline_pct:.2f} % — rien à distribuer"
                ),
                baseline_pct=baseline.pct,
            )

        result = self._compare(baseline, self.snapshot(token_address))
        if result.dumping:
            self._flagged.add(token_address)
        return result

    def _compare(self, baseline: CreatorSnapshot, current: CreatorSnapshot) -> DumpVerdict:
        lower_bound = False
        if current.visible:
            current_pct = current.pct or 0.0
        elif current.floor_pct is not None:
            # Sorti du top 20 : il détient AU PLUS la part du plus petit
            # compte encore listé. La baisse réelle est donc au MOINS celle-ci.
            current_pct = min(current.floor_pct, baseline.pct)
            lower_bound = True
        else:
            # Ni visible, ni liste exploitable : aucune mesure, aucun verdict.
            return DumpVerdict(
                dumping=False,
                reason="solde du créateur illisible à ce contrôle",
                baseline_pct=baseline.pct,
            )

        drop_pts = baseline.pct - current_pct
        drop_relative = drop_pts / baseline.pct if baseline.pct > 0 else 0.0

        if drop_pts <= 0:
            return DumpVerdict(
                dumping=False,
                reason="le créateur n'a pas réduit sa position",
                baseline_pct=baseline.pct,
                current_pct=current_pct,
                drop_pts=drop_pts,
                drop_relative=drop_relative,
            )

        declenche = (
            drop_relative >= self.drop_relative and drop_pts >= self.drop_absolute_pts
        )
        borne = " (au moins — créateur sorti du top 20)" if lower_bound else ""
        return DumpVerdict(
            dumping=declenche,
            reason=(
                f"le créateur a cédé {drop_pts:.2f} pt de supply "
                f"({drop_relative:.0%} de sa mise){borne} — "
                f"{baseline.pct:.2f} % → {current_pct:.2f} %"
            ),
            baseline_pct=baseline.pct,
            current_pct=current_pct,
            drop_pts=drop_pts,
            drop_relative=drop_relative,
            is_lower_bound=lower_bound,
        )

    def already_flagged(self, token_address: str) -> bool:
        return token_address in self._flagged

    def unflag(self, token_address: str) -> None:
        """Annule un signalement dont l'appelant n'a PAS pu tirer les
        conséquences — typiquement une fermeture reportée faute de prix. Sans
        ça, le token resterait « déjà signalé » et ne serait plus jamais
        recontrôlé : l'alerte serait partie, la position serait restée.
        """
        self._flagged.discard(token_address)

    def tracked_tokens(self) -> tuple[str, ...]:
        """Tokens pour lesquels une ligne de base existe encore."""
        return tuple(self._baseline)

    def forget(self, token_address: str) -> None:
        """Oublie un token dont plus aucune position n'est ouverte.

        Sans ça, les trois dictionnaires grossissent indéfiniment sur un
        processus qui tourne des semaines — même défaut que celui corrigé sur
        `funnel_log.jsonl`, une couche plus haut.
        """
        self._baseline.pop(token_address, None)
        self._last_check.pop(token_address, None)
        self._flagged.discard(token_address)
