"""Capacités et chaînes de repli : le bot doit survivre à la mort d'une API.

LE PROBLÈME RÉEL, pas théorique. Deux sources sont mortes en cours de route :

  Twitter  402  quota gratuit épuisé
  Birdeye  400  « Compute units usage limit exceeded »

Et la dégradation était SILENCIEUSE. Le bot a brûlé 489 requêtes Birdeye sur
un quota déjà mort, avec 3 essais et un backoff par appel, soit ~23 s de
chaque cycle de 90 s. L'analyse technique est passée sur des bougies
reconstruites sans que rien ne le dise, les holders sur une borne inférieure
Helius au lieu du compte exact.

LE PRINCIPE. On ne raisonne plus en « API » mais en CAPACITÉ : le pipeline a
besoin de bougies, de holders, d'un prix, d'un audit sécurité. Chaque capacité
a une CHAÎNE de fournisseurs classés par préférence. Si le premier meurt, le
suivant prend — et la dégradation est ANNONCÉE, jamais subie.

CE QUE ÇA CHANGE CONCRÈTEMENT, sans dépenser un euro :

  bougies   Birdeye (payant, mort) -> GMGN market kline (gratuit)
  holders   Birdeye (mort) -> GMGN market trending (déjà collecté) -> Helius
  prix      Jupiter par lot (50 mints/appel) -> DexScreener -> Birdeye
  social    Twitter (mort) -> présence de réseaux via GMGN (signal binaire)

La colonne de droite ne coûte rien. Le bot n'a jamais eu besoin d'un
abonnement — il avait besoin de savoir quoi faire quand une source tombe.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Capacités dont le pipeline dépend. Nommer la CAPACITÉ et non l'API est ce
# qui permet de substituer sans réécrire les appelants.
CANDLES = "bougies"
HOLDERS = "holders"
PRICE = "prix"
SECURITY = "securite"
SOCIAL = "social"
SMART_MONEY = "smart_money"


@dataclass
class Provider:
    """Un fournisseur pour une capacité, avec son coût et sa disponibilité."""

    name: str
    #  Vrai si la source répond encore. Évalué à CHAQUE appel : un module se
    #  coupe en cours de route (402, quota CU), l'état ne peut pas être figé.
    available: Callable[[], bool]
    call: Callable[..., Any]
    # Coût indicatif en requêtes. Sert à préférer le gratuit à budget égal.
    cost: int = 1
    free: bool = True
    note: str = ""


@dataclass
class Capability:
    """Une capacité et sa chaîne de repli, du plus préféré au dernier recours."""

    name: str
    providers: list[Provider] = field(default_factory=list)
    # Dernier fournisseur utilisé — sert au rapport de dégradation.
    last_used: Optional[str] = None
    degraded: bool = False

    def resolve(self, *args: Any, **kwargs: Any) -> tuple[Any, Optional[str]]:
        """Premier fournisseur disponible qui rend un résultat non vide.

        Retourne (résultat, nom du fournisseur). Un fournisseur disponible
        mais qui rend vide n'est PAS une panne : on passe au suivant sans le
        marquer indisponible — un token peut simplement ne pas avoir de
        bougies.
        """
        for index, provider in enumerate(self.providers):
            try:
                if not provider.available():
                    continue
                result = provider.call(*args, **kwargs)
            except Exception as exc:
                print(f"[{self.name}] {provider.name} a échoué : {type(exc).__name__} — {exc}")
                continue
            if result:
                self.last_used = provider.name
                self.degraded = index > 0
                return result, provider.name
        self.last_used = None
        self.degraded = True
        return None, None

    @property
    def status(self) -> dict[str, Any]:
        vivants = [p.name for p in self.providers if _safe(p.available)]
        morts = [p.name for p in self.providers if not _safe(p.available)]
        return {
            "capability": self.name,
            "using": self.last_used,
            "degraded": self.degraded,
            "available": vivants,
            "down": morts,
            # Une capacité sans aucun fournisseur vivant est un TROU, pas une
            # dégradation : le pipeline doit continuer, mais il faut le dire.
            "blind": not vivants,
        }


def _safe(check: Callable[[], bool]) -> bool:
    try:
        return bool(check())
    except Exception:
        return False


class CapabilityRegistry:
    """Toutes les capacités du bot et leur état de santé."""

    def __init__(self) -> None:
        self.capabilities: dict[str, Capability] = {}

    def register(self, name: str, providers: list[Provider]) -> Capability:
        capability = Capability(name=name, providers=providers)
        self.capabilities[name] = capability
        return capability

    def resolve(self, name: str, *args: Any, **kwargs: Any) -> tuple[Any, Optional[str]]:
        capability = self.capabilities.get(name)
        if capability is None:
            return None, None
        return capability.resolve(*args, **kwargs)

    @property
    def report(self) -> list[dict[str, Any]]:
        return [c.status for c in self.capabilities.values()]

    @property
    def blind_spots(self) -> list[str]:
        """Capacités sans AUCUN fournisseur. À afficher, pas à masquer."""
        return [c.name for c in self.capabilities.values() if c.status["blind"]]

    def summary(self) -> str:
        lignes = []
        for status in self.report:
            if status["blind"]:
                lignes.append(f"  ✗ {status['capability']} : aucune source")
            elif status["down"]:
                lignes.append(
                    f"  ~ {status['capability']} : {', '.join(status['available'])} "
                    f"(mort : {', '.join(status['down'])})"
                )
            else:
                lignes.append(f"  ✓ {status['capability']} : {status['available'][0]}")
        return "\n".join(lignes)


def build_registry(
    birdeye: Any = None,
    gmgn: Any = None,
    helius: Any = None,
    jupiter: Any = None,
    dex: Any = None,
) -> CapabilityRegistry:
    """Chaînes de repli du bot. L'ordre encode une préférence justifiée.

    Pour les BOUGIES, GMGN passe AVANT Birdeye alors que Birdeye est la
    source « officielle » : Birdeye est plafonné à 1 req/s et son quota
    gratuit s'épuise en quelques jours, GMGN n'a pas de plafond mensuel connu.
    Préférer le gratuit illimité au payant contraint n'est pas un pis-aller,
    c'est le bon choix par défaut.
    """
    registry = CapabilityRegistry()

    registry.register(CANDLES, [
        Provider(
            "gmgn/kline",
            available=lambda: bool(gmgn and gmgn.enabled),
            call=lambda addr, **kw: gmgn.kline(addr, **kw),
            note="gratuit, pas de plafond mensuel connu",
        ),
        Provider(
            "birdeye/ohlcv",
            available=lambda: bool(birdeye and birdeye.enabled),
            call=lambda addr, **kw: birdeye.get_ohlcv(addr, **kw),
            free=False,
            note="1 req/s, quota CU mensuel",
        ),
    ])

    registry.register(HOLDERS, [
        Provider(
            "birdeye/overview",
            available=lambda: bool(birdeye and birdeye.enabled),
            call=lambda addr: birdeye.get_overview(addr),
            free=False,
            note="compte EXACT en 1 appel",
        ),
        Provider(
            "helius",
            available=lambda: bool(helius and helius.enabled),
            call=lambda addr, **kw: helius.get_holder_stats(addr, **kw),
            cost=3,
            note="borne INFÉRIEURE, pagination plafonnée",
        ),
    ])

    registry.register(PRICE, [
        Provider(
            "jupiter/price",
            available=lambda: bool(jupiter and jupiter.enabled),
            call=lambda mints: jupiter.prices(mints),
            note="50 mints par appel — coût O(1) quel que soit le nombre",
        ),
        Provider(
            "dexscreener",
            available=lambda: dex is not None,
            call=lambda mints: dex.get_tokens_data(list(mints)),
            cost=1,
        ),
    ])

    return registry
