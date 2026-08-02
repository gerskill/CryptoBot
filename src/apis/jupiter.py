"""Client Jupiter — prix par lot et impact de prix réel. LECTURE SEULE.

CE QUE ÇA DÉBLOQUE, et pourquoi c'est le meilleur rapport qualité/quota du bot :

1. PRIX PAR LOT. `/price/v3` accepte 50 mints par appel. Le monitoring coûte
   aujourd'hui une requête DexScreener PAR POSITION et par tick (12 ticks/min) ;
   ici c'est un appel, quel que soit le nombre de positions. Le coût du
   monitoring passe de O(positions) à O(1).

2. IMPACT DE PRIX. `/swap/v2/order` rend le prix RÉELLEMENT exécutable pour
   une taille donnée, routage compris. Aucune autre source du bot ne le donne :
   DexScreener et Birdeye rendent un prix moyen. C'est la pièce qui manquait
   pour que le P&L papier cesse d'être optimiste par construction
   (cf. SLIPPAGE_NOTE dans src/core/portfolio.py).

SÉCURITÉ — même discipline que src/apis/gmgn.py : une liste blanche de chemins,
vérifiée AVANT la requête. `/swap/v2/execute` et `/swap/v2/build` en sont
volontairement absents et le resteront. Demander un devis n'engage rien ; ce
client ne peut pas signer, ni soumettre, ni construire une transaction.

La clé vient de l'environnement, n'est jamais loggée, et son absence dégrade
proprement — même contrat que Helius, Birdeye et GMGN.
"""

import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import requests

from src.core.budget import MonthlyBudget
from src.core.ratelimit import RateLimiter

BASE_URL = "https://api.jup.ag"
TIMEOUT = 10

# Palier Free = 1 requête/seconde, fenêtre glissante de 60 s.
DEFAULT_RPS = 1
MAX_MINTS_PER_CALL = 50
PRICE_CACHE_SECONDS = 3  # le monitoring tourne à 5 s : pas de double appel

# Liste blanche STRICTE. Toute exécution est absente par construction.
ALLOWED_PATHS = frozenset({"/price/v3", "/swap/v2/order", "/tokens/v2/search"})

# Wrapped SOL — unité de compte des devis.
# Un honeypot coûte cher à la SORTIE. En dessous de ce seuil, le ratio
# entrée/sortie n'est que du bruit de routage, pas un piège.
HONEYPOT_MIN_EXIT_COST_PCT = 5.0
# Sous cet impact à l'achat, le ratio n'a pas de sens : diviser par ~0 rend
# n'importe quel coût de sortie « asymétrique ».
MIN_IMPACT_FOR_RATIO = 0.1

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class ForbiddenEndpoint(RuntimeError):
    """Tentative d'appel hors liste blanche — l'exécution reste impossible."""


@dataclass(frozen=True)
class SwapQuote:
    """Devis pour une taille donnée. Ce qu'on obtiendrait VRAIMENT."""

    input_mint: str
    output_mint: str
    in_amount: int
    out_amount: int
    price_impact_pct: float
    slippage_bps: Optional[int] = None
    route_count: int = 0

    @property
    def total_cost_pct(self) -> float:
        """Coût aller simple, en points de pourcentage (positif = on perd)."""
        return abs(self.price_impact_pct)


class JupiterAPI:
    """Prix et devis Jupiter. Ne peut pas exécuter de transaction."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        rps: int = DEFAULT_RPS,
        budget: Optional[MonthlyBudget] = None,
    ):
        self.api_key = api_key
        self.enabled = api_key is not None
        self.budget = budget
        # Fenêtre d'UNE seconde, pas de 60 : le palier Free est à 1 req/s et
        # un quota exprimé sur 60 s autorise une rafale de 60 appels qui prend
        # un 429 immédiat. Mesuré : 11 requêtes d'affilée -> 429 au 11e.
        self.rate_limiter = RateLimiter(max(1, rps), 1.0, name="jupiter")
        self.request_count = 0
        self.skipped_no_budget = 0
        self.session = requests.Session()
        self._price_cache: dict[str, tuple[float, float]] = {}

        if not self.enabled:
            print("[Jupiter] aucune clé — module désactivé, le pipeline continue")

    # ------------------------------------------------------------- transport

    def _get(self, path: str, params: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Le garde-fou est ICI, pas chez l'appelant."""
        if path not in ALLOWED_PATHS:
            raise ForbiddenEndpoint(
                f"chemin '{path}' interdit — ce client est en lecture seule"
            )
        if not self.enabled:
            return None
        if self.budget is not None and not self.budget.spend(1):
            self.skipped_no_budget += 1
            return None

        self.rate_limiter.acquire()
        try:
            response = self.session.get(
                f"{BASE_URL}{path}",
                params=params,
                headers={"x-api-key": self.api_key, "Accept": "application/json"},
                timeout=TIMEOUT,
            )
            self.request_count += 1

            if response.status_code == 401:
                # Ne jamais logger la clé, seulement le fait qu'elle est refusée.
                print("[Jupiter] clé refusée (401) — module désactivé")
                self.enabled = False
                return None
            if response.status_code == 429:
                print("[Jupiter] 429 — quota de débit atteint, appel abandonné")
                return None
            if response.status_code >= 400:
                print(f"[Jupiter] {path} HTTP {response.status_code}")
                return None
            return response.json()

        except requests.RequestException as exc:
            print(f"[Jupiter] {path} erreur réseau : {type(exc).__name__}")
            return None
        except ValueError:
            print(f"[Jupiter] {path} réponse non-JSON")
            return None

    # ------------------------------------------------------------ prix

    def prices(self, mints: Iterable[str]) -> dict[str, float]:
        """Prix USD de plusieurs tokens. 50 par appel, cache 3 s.

        Le cache court est délibéré : le monitoring tourne à 5 s et plusieurs
        stratégies peuvent détenir le même token. Sans lui, N bras sur un même
        token feraient N appels pour la même valeur au même instant.
        """
        wanted = [m for m in dict.fromkeys(mints) if m]
        if not wanted or not self.enabled:
            return {}

        now = time.time()
        found: dict[str, float] = {}
        missing: list[str] = []
        for mint in wanted:
            cached = self._price_cache.get(mint)
            if cached and now - cached[0] < PRICE_CACHE_SECONDS:
                found[mint] = cached[1]
            else:
                missing.append(mint)

        for start in range(0, len(missing), MAX_MINTS_PER_CALL):
            batch = missing[start : start + MAX_MINTS_PER_CALL]
            payload = self._get("/price/v3", {"ids": ",".join(batch)})
            if not payload:
                continue
            for mint, entry in payload.items():
                price = _price_of(entry)
                if price is not None:
                    found[mint] = price
                    self._price_cache[mint] = (now, price)

        return found

    def price(self, mint: str) -> Optional[float]:
        return self.prices([mint]).get(mint)

    # ------------------------------------------------------------ devis

    def quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 300,
    ) -> Optional[SwapQuote]:
        """Devis pour `amount` unités de `input_mint`. N'exécute RIEN.

        `amount` est en unités atomiques (lamports pour SOL). Le champ qui
        compte est `priceImpactPct` : c'est ce que la taille coûte au carnet,
        et donc ce qui manque au P&L papier.
        """
        payload = self._get(
            "/swap/v2/order",
            {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(int(amount)),
                "slippageBps": str(int(slippage_bps)),
                # Devis seul : pas de compte payeur, donc rien de signable.
                "onlyDirectRoutes": "false",
            },
        )
        if not payload:
            return None

        try:
            impact = float(payload.get("priceImpactPct") or 0) * 100
        except (TypeError, ValueError):
            impact = 0.0
        routes = payload.get("routePlan") or []

        return SwapQuote(
            input_mint=input_mint,
            output_mint=output_mint,
            in_amount=int(payload.get("inAmount") or amount),
            out_amount=int(payload.get("outAmount") or 0),
            price_impact_pct=impact,
            slippage_bps=_opt_int(payload.get("slippageBps")),
            route_count=len(routes),
        )

    def round_trip_cost_pct(
        self, token_mint: str, size_usd: float, sol_price_usd: float = 0.0
    ) -> Optional[float]:
        """Coût aller-retour estimé, en %. Deux devis, aucune transaction.

        C'est le nombre qui rend le P&L papier honnête : acheter puis revendre
        un memecoin à 20K de liquidité coûte plusieurs points, à l'aller comme
        au retour, et le journal actuel n'en tient aucun compte.
        """
        if not self.enabled or size_usd <= 0:
            return None
        sol_price = sol_price_usd or self.price(SOL_MINT) or 0.0
        if sol_price <= 0:
            return None

        lamports = int(size_usd / sol_price * 1e9)
        buy = self.quote(SOL_MINT, token_mint, lamports)
        if buy is None or buy.out_amount <= 0:
            return None
        sell = self.quote(token_mint, SOL_MINT, buy.out_amount)
        if sell is None:
            return None
        return round(buy.total_cost_pct + sell.total_cost_pct, 3)

    def sellable(
        self, token_mint: str, size_usd: float = 20.0, sol_price_usd: float = 0.0
    ) -> tuple[Optional[bool], str]:
        """Peut-on RESSORTIR de ce token ? Détection de honeypot sans wallet.

        Un honeypot laisse acheter et empêche de vendre. Le test ne demande
        donc ni wallet, ni signature, ni transaction : il suffit de demander
        un devis dans le sens VENTE. S'il n'existe aucune route, ou si sortir
        coûte un multiple de ce qu'a coûté l'entrée, le token est un piège.

        C'est la correction d'une erreur de conception : le dashboard
        affichait « test honeypot impossible en mode papier ». Faux — c'est
        `/swap/v2/execute` qui demande un wallet, pas `/swap/v2/order`.

        Retourne (True vendable, False piège, None indéterminé). `None` ne
        rejette pas : même invariant que le reste du pipeline.
        """
        if not self.enabled or size_usd <= 0:
            return None, "Jupiter indisponible"
        sol_price = sol_price_usd or self.price(SOL_MINT) or 0.0
        if sol_price <= 0:
            return None, "prix SOL indisponible"

        lamports = int(size_usd / sol_price * 1e9)
        buy = self.quote(SOL_MINT, token_mint, lamports)
        if buy is None or buy.out_amount <= 0:
            return None, "aucun devis d'achat — token illiquide ou inconnu"

        sell = self.quote(token_mint, SOL_MINT, buy.out_amount)
        if sell is None:
            return False, "AUCUNE ROUTE DE SORTIE — honeypot probable"
        if sell.out_amount <= 0:
            return False, "la vente rend 0 — honeypot probable"

        # Asymétrie : sortir coûte bien plus que rentrer. Signature classique
        # d'une taxe de vente ou d'un carnet à sens unique.
        #
        # PLANCHER OBLIGATOIRE sur le coût de sortie. Sans lui, un impact
        # d'achat nul — cas courant d'une petite position sur un carnet
        # profond — rend le ratio infini et déclenche sur n'importe quoi.
        # Faux positif observé : « sortie 1,3% contre entrée 0,0% », un token
        # parfaitement sain rejeté comme honeypot.
        if (
            buy.total_cost_pct >= MIN_IMPACT_FOR_RATIO
            and sell.total_cost_pct >= HONEYPOT_MIN_EXIT_COST_PCT
            and sell.total_cost_pct > buy.total_cost_pct * 5
        ):
            return False, (
                f"sortie {sell.total_cost_pct:.1f}% contre entrée "
                f"{buy.total_cost_pct:.1f}% — asymétrie de honeypot"
            )
        retour = sell.out_amount / lamports if lamports else 0
        if retour < 0.5:
            return False, f"la vente ne rend que {retour:.0%} de la mise — piège"
        return True, f"sortie possible, {sell.total_cost_pct:.1f}% de coût"

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "requests": self.request_count,
            "cached_mints": len(self._price_cache),
            "skipped_no_budget": self.skipped_no_budget,
        }


def _price_of(entry: Any) -> Optional[float]:
    """Le schéma a bougé entre v2 et v3 : tolérer les deux formes."""
    if entry is None:
        return None
    if isinstance(entry, (int, float)):
        return float(entry) or None
    if isinstance(entry, dict):
        for key in ("usdPrice", "price", "value"):
            value = entry.get(key)
            if value is not None:
                try:
                    return float(value) or None
                except (TypeError, ValueError):
                    continue
    return None


def _opt_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
