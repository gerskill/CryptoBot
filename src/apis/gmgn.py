"""Module GMGN — smart money, sécurité et structure de détention.

⚠️ CE MODULE N'EXÉCUTE JAMAIS DE TRANSACTION.
`gmgn-cli` sait acheter, vendre et créer des tokens (`swap`, `multi-swap`,
`order`, `cooking`). Seules les commandes de LECTURE sont autorisées ici, et
la liste blanche `ALLOWED_COMMANDS` le garantit : toute commande hors liste
lève une exception avant même d'atteindre le sous-processus.

ACCÈS : passe par le binaire `gmgn-cli` (npm install -g gmgn-cli), pas par
HTTP. Le CLI lit `GMGN_API_KEY` et `GMGN_PRIVATE_KEY` depuis
`~/.config/gmgn/.env`. Sans clé, toutes les méthodes retournent None et le
pipeline continue en mode dégradé — même contrat que Helius et Birdeye.

CE QUE ÇA DÉBLOQUE : `smart_money_buys_30m`, le seul composant du score alpha
qui n'avait aucune source. Le filtre `min_smart_money_buys_30min` était
inactif depuis le début.
"""

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from src.core.ratelimit import RateLimiter

COMMAND_TIMEOUT = 30
CACHE_TTL_SECONDS = 120  # les achats smart money bougent vite, mais pas à 20s
SMART_MONEY_WINDOW_MINUTES = 30

# Tags GMGN à exclure : bots MEV / wash — pas des traders directionnels.
# Vérifié en live sur 6WJfN1... (arbitrager, ~80k trades/semaine, ~3% ROI).
DEFAULT_EXCLUDED_WALLET_TAGS = frozenset({"arbitrager", "wash_trader"})

# Sous ce seuil USD, on ignore le trade : l'arbitrage MEV passe par des
# micro-montants ($0.01–$20) que le bot ne peut ni copier ni interpréter.
DEFAULT_MIN_TRADE_USD = 25.0

# Liste blanche stricte : uniquement de la lecture. `swap`, `multi-swap`,
# `order` et `cooking` sont volontairement absents et le resteront.
ALLOWED_COMMANDS = frozenset(
    {
        ("track", "smartmoney"),
        ("track", "kol"),
        ("token", "info"),
        ("token", "security"),
        ("token", "holders"),
        ("token", "traders"),
        ("market", "trending"),
        ("market", "kline"),
    }
)


class ForbiddenCommand(RuntimeError):
    """Tentative d'appel d'une commande hors liste blanche."""


@dataclass(frozen=True)
class SmartMoneyActivity:
    """Activité smart money sur un token, sur la fenêtre observée."""

    buys: int
    sells: int
    unique_wallets: int
    volume_usd: float
    window_minutes: int = SMART_MONEY_WINDOW_MINUTES
    # Achats pondérés par leur FRAÎCHEUR. Un compte brut traite un achat
    # vieux de 29 minutes comme un achat d'il y a 30 secondes — or sur un
    # memecoin, une demi-heure est une éternité : le mouvement est fini, on
    # arriverait après. Le poids décroît linéairement sur la fenêtre.
    #
    # Linéaire et non exponentiel : les avances mesurées sur ce marché vont
    # de +0,4 à +3,6 min, très en deçà de la fenêtre de 30 min. Une décroissance
    # agressive du type 1/(1+minutes) écraserait tout à zéro et supprimerait
    # le signal au lieu de le hiérarchiser.
    weighted_buys: float = 0.0
    newest_buy_age_minutes: Optional[float] = None

    @property
    def net_pressure(self) -> float:
        """1.0 = que des achats, 0.0 = que des ventes, 0.5 = équilibre."""
        total = self.buys + self.sells
        return self.buys / total if total else 0.5

    @property
    def freshness(self) -> float:
        """0 à 1 : proportion du signal encore « chaude ».

        `weighted_buys / buys`. Vaut 1 si tous les achats viennent de se
        produire, tend vers 0 s'ils datent du bord de la fenêtre.
        """
        return self.weighted_buys / self.buys if self.buys else 0.0


class GmgnAPI:
    """Client GMGN en lecture seule, via le CLI officiel."""

    def __init__(self, binary: str = "gmgn-cli", chain: str = "sol", enabled: bool = True):
        self.binary = shutil.which(binary) or binary
        self.chain = chain
        self.available = bool(shutil.which(binary))
        # Le quota GMGN n'est pas documenté publiquement : 60/min, prudent.
        self.rate_limiter = RateLimiter(60, 60.0, name="gmgn")
        self.request_count = 0
        self._cache: dict[str, tuple[float, Any]] = {}
        self._smart_money_snapshot: Optional[tuple[float, list[dict[str, Any]]]] = None

        self.enabled = enabled and self.available and self._has_key()
        if enabled and not self.available:
            print("[GMGN] binaire gmgn-cli introuvable — module désactivé")
        elif enabled and not self.enabled:
            print(
                "[GMGN] aucune configuration exploitable — module désactivé. "
                "Applique ta clé avec : gmgn-cli config --apply <clé>"
            )

    def _has_key(self) -> bool:
        """Le CLI fait autorité sur sa propre configuration.

        `config --check` sort en 0 si `~/.config/gmgn/.env` contient une paire
        exploitable. Une paire COMPLÈTE dans l'environnement est acceptée en
        remplacement ; une paire incomplète ne compte pas — voir
        `_subprocess_env`.
        """
        if os.getenv("GMGN_API_KEY") and os.getenv("GMGN_PRIVATE_KEY"):
            return True
        try:
            result = subprocess.run(
                [self.binary, "config", "--check"], capture_output=True, timeout=10
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

    @staticmethod
    def _subprocess_env() -> dict[str, str]:
        """Environnement du CLI, purgé des clés GMGN incomplètes.

        Le `.env` du projet peut ne contenir que `GMGN_API_KEY`. Transmise
        seule, elle ÉCRASE la config du CLI et le laisse sans clé privée
        correspondante -> `AUTH_KEY_INVALID` alors que `~/.config/gmgn/.env`
        est parfaitement valide. On ne transmet la paire que si elle est
        complète, sinon on laisse le CLI lire sa propre configuration.
        """
        env = dict(os.environ)
        if not (env.get("GMGN_API_KEY") and env.get("GMGN_PRIVATE_KEY")):
            env.pop("GMGN_API_KEY", None)
            env.pop("GMGN_PRIVATE_KEY", None)
        return env

    # ------------------------------------------------------------- transport

    def _run(self, *args: str) -> Optional[Any]:
        """Exécute une commande de LECTURE et parse le JSON.

        Refuse toute commande absente de la liste blanche — le garde-fou est
        ici, pas dans l'appelant.
        """
        if len(args) < 2 or (args[0], args[1]) not in ALLOWED_COMMANDS:
            raise ForbiddenCommand(
                f"commande '{' '.join(args[:2])}' interdite — lecture seule uniquement"
            )
        if not self.enabled:
            return None

        self.rate_limiter.acquire()
        command = [self.binary, *args, "--chain", self.chain, "--raw"]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT,
                env=self._subprocess_env(),
            )
            self.request_count += 1

            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                combined = f"{stderr} {result.stdout or ''}"
                if "AUTH_KEY_INVALID" in combined or "401" in combined:
                    print(
                        "[GMGN] clé refusée (AUTH_KEY_INVALID) — module désactivé. "
                        "Vérifie GMGN_API_KEY."
                    )
                    self.enabled = False
                    return None
                print(f"[GMGN] {' '.join(args[:2])} échec : {stderr[:120]}")
                return None

            payload = json.loads(result.stdout)
            # Le CLI renvoie parfois exit 0 avec un code métier non nul.
            if isinstance(payload, dict) and payload.get("code") not in (0, None):
                print(f"[GMGN] {' '.join(args[:2])} code {payload.get('code')}")
                return None
            return payload.get("data", payload) if isinstance(payload, dict) else payload

        except subprocess.TimeoutExpired:
            print(f"[GMGN] {' '.join(args[:2])} timeout ({COMMAND_TIMEOUT}s)")
        except json.JSONDecodeError:
            print(f"[GMGN] {' '.join(args[:2])} sortie non-JSON")
        except (subprocess.SubprocessError, OSError) as exc:
            print(f"[GMGN] {' '.join(args[:2])} erreur : {exc}")
        return None

    def _cached(self, key: str, producer) -> Any:
        entry = self._cache.get(key)
        if entry and time.time() - entry[0] < CACHE_TTL_SECONDS:
            return entry[1]
        value = producer()
        if value is not None:
            self._cache[key] = (time.time(), value)
        return value

    # --------------------------------------------------------- smart money

    def fetch_smart_money_trades(self, limit: int = 200) -> list[dict[str, Any]]:
        """Derniers trades smart money de la chaîne, tous tokens confondus.

        UN SEUL appel couvre tous les candidats : le flux est global, on le
        récupère une fois par cycle et on l'indexe par token. Interroger
        token par token gaspillerait le quota pour la même information.
        """
        if not self.enabled:
            return []

        snapshot = self._smart_money_snapshot
        if snapshot and time.time() - snapshot[0] < CACHE_TTL_SECONDS:
            return snapshot[1]

        data = self._run("track", "smartmoney", "--limit", str(limit))
        trades = _as_list(data)
        self._smart_money_snapshot = (time.time(), trades)
        if trades:
            print(f"[GMGN] {len(trades)} trades smart money récupérés (1 requête)")
        return trades

    def activity_by_token(
        self,
        limit: int = 200,
        window_minutes: int = SMART_MONEY_WINDOW_MINUTES,
        *,
        exclude_wallet_tags: Optional[list[str]] = None,
        require_wallet_tags: Optional[list[str]] = None,
        min_trade_usd: float = DEFAULT_MIN_TRADE_USD,
        buys_only: bool = False,
    ) -> dict[str, SmartMoneyActivity]:
        """Indexe le flux smart money par adresse de token.

        Filtre le bruit avant agrégation :
        - tags exclus (ex. ``arbitrager``) via ``maker_info.tags`` ;
        - taille minimale en USD (micro-trades MEV) ;
        - tags requis optionnels (ex. ``smart_degen`` pour forcer le profil GMGN).
        """
        excluded = DEFAULT_EXCLUDED_WALLET_TAGS | {
            t.lower() for t in (exclude_wallet_tags or [])
        }
        required = {t.lower() for t in (require_wallet_tags or [])}
        cutoff = time.time() - window_minutes * 60
        grouped: dict[str, dict[str, Any]] = {}
        skipped = 0

        for trade in self.fetch_smart_money_trades(limit):
            address = _token_address(trade)
            if not address:
                continue
            timestamp = _timestamp(trade)
            if timestamp is not None and timestamp < cutoff:
                continue
            if not _is_directional_smart_money_trade(
                trade,
                excluded_tags=excluded,
                required_tags=required,
                min_trade_usd=min_trade_usd,
            ):
                skipped += 1
                continue
            is_buy = _is_buy(trade)
            if is_buy is None:
                # Sens du trade non reconnu : l'exclure plutôt que le
                # compter à tort comme un achat (ou une vente).
                skipped += 1
                continue
            if buys_only and not is_buy:
                skipped += 1
                continue

            bucket = grouped.setdefault(
                address,
                {"buys": 0, "sells": 0, "wallets": set(), "volume": 0.0,
                 "weighted": 0.0, "newest": None},
            )
            if is_buy:
                bucket["buys"] += 1
                # Poids linéaire sur la fenêtre : 1.0 à l'instant, 0 au bord.
                # Un horodatage manquant vaut 0.5 — ni rejeté, ni privilégié.
                if timestamp is None:
                    bucket["weighted"] += 0.5
                else:
                    age_min = max(0.0, (time.time() - timestamp) / 60)
                    bucket["weighted"] += max(0.0, 1 - age_min / window_minutes)
                    if bucket["newest"] is None or age_min < bucket["newest"]:
                        bucket["newest"] = round(age_min, 2)
            else:
                bucket["sells"] += 1
            wallet = _wallet_address(trade)
            if wallet:
                bucket["wallets"].add(wallet)
            bucket["volume"] += _trade_usd(trade)

        if skipped:
            print(f"[GMGN] {skipped} trades ignorés (bots / micro-montants / tags)")

        return {
            address: SmartMoneyActivity(
                buys=bucket["buys"],
                sells=bucket["sells"],
                unique_wallets=len(bucket["wallets"]),
                volume_usd=round(bucket["volume"], 2),
                window_minutes=window_minutes,
                weighted_buys=round(bucket["weighted"], 3),
                newest_buy_age_minutes=bucket["newest"],
            )
            for address, bucket in grouped.items()
        }

    # --------------------------------------------------------- découverte

    def market_trending(
        self,
        interval: str = "5m",
        limit: int = 100,
        order_by: str = "volume",
        min_liquidity: Optional[float] = None,
        min_volume: Optional[float] = None,
        min_holder_count: Optional[int] = None,
        min_created: Optional[str] = None,
        max_created: Optional[str] = None,
        max_top10_holder_rate: Optional[float] = None,
        min_swaps: Optional[int] = None,
        min_marketcap: Optional[float] = None,
        min_smart_degen_count: Optional[int] = None,
        min_renowned_count: Optional[int] = None,
        max_bundler_rate: Optional[float] = None,
        max_insider_rate: Optional[float] = None,
        filters: Optional[Iterable[str]] = None,
    ) -> list[dict[str, Any]]:
        """Classement de marché, FILTRÉ CÔTÉ SERVEUR.

        La source de découverte historique (`/token-profiles/latest` de
        DexScreener) est un flux de PROMOTIONS PAYANTES : mesuré sur 38 tokens,
        liquidité médiane 1 634 $, et 1 seul passait les filtres du bot. On
        payait l'enrichissement de 37 déchets pour 1 candidat.

        Ici les seuils partent avec la requête : le service ne renvoie que ce
        qui est déjà éligible. Un appel rend aussi la liquidité, les holders,
        `top_10_holder_rate`, `smart_degen_count`, `renounced_mint/freeze` et
        l'âge — de quoi filtrer sans dépenser un appel Birdeye ou RugCheck.

        `min_created` / `max_created` sont des DURÉES avec suffixe (`90m`,
        `6h`), pas des nombres nus, et désignent un ÂGE : `max_created=6h`
        exclut les tokens plus vieux que 6 h.
        """
        args = [
            "market", "trending",
            "--interval", interval,
            "--limit", str(min(limit, 100)),
            "--order-by", order_by,
        ]
        for flag, value in (
            ("--min-liquidity", min_liquidity),
            ("--min-volume", min_volume),
            ("--min-holder-count", min_holder_count),
            ("--min-created", min_created),
            ("--max-created", max_created),
            ("--max-top10-holder-rate", max_top10_holder_rate),
            ("--min-swaps", min_swaps),
            ("--min-marketcap", min_marketcap),
            ("--min-smart-degen-count", min_smart_degen_count),
            ("--min-renowned-count", min_renowned_count),
            ("--max-bundler-rate", max_bundler_rate),
            ("--max-insider-rate", max_insider_rate),
        ):
            if value is not None:
                args += [flag, str(value)]
        for tag in filters or ():
            args += ["--filter", tag]

        key = "trend:" + "|".join(args)
        data = self._cached(key, lambda: self._run(*args))
        if isinstance(data, dict):
            data = data.get("rank", data)
        return _as_list(data)

    def kline(
        self,
        token_address: str,
        resolution: str = "1m",
        lookback_seconds: int = 3600,
    ) -> list[dict[str, Any]]:
        """Bougies OHLCV — le REMPLAÇANT GRATUIT de Birdeye.

        Birdeye est le goulot du bot (1 req/s) et son quota mensuel gratuit
        s'épuise : mesuré, `token_overview` et `get_ohlcv` renvoient
        « Compute units usage limit exceeded » alors que l'abonnement coûte
        39 $/mois. Sans bougies, l'analyse technique tourne sur des snapshots
        reconstruits — beaucoup moins fiables qu'un vrai OHLCV.

        GMGN rend les mêmes bougies sans quota mensuel connu. `time` est en
        MILLISECONDES et les prix sont des CHAÎNES : deux pièges que le
        format Birdeye n'a pas.
        """
        now = int(time.time())
        data = self._cached(
            f"kline:{token_address}:{resolution}:{lookback_seconds}",
            lambda: self._run(
                "market", "kline",
                "--address", token_address,
                "--resolution", resolution,
                "--from", str(now - lookback_seconds),
                "--to", str(now),
            ),
        )
        if isinstance(data, dict):
            data = data.get("list", data)
        return _as_list(data)

    # ------------------------------------------------------------- token

    def token_security(self, token_address: str) -> Optional[dict[str, Any]]:
        """Métriques de sécurité GMGN — complète RugCheck, ne le remplace pas."""
        return self._cached(
            f"sec:{token_address}",
            lambda: self._run("token", "security", "--address", token_address),
        )

    def token_holders(self, token_address: str, limit: int = 20) -> list[dict[str, Any]]:
        """Top holders — remplace le BubbleMap abandonné (trop cher)."""
        data = self._cached(
            f"holders:{token_address}",
            lambda: self._run("token", "holders", "--address", token_address, "--limit", str(limit)),
        )
        return _as_list(data)

    def token_info(self, token_address: str) -> Optional[dict[str, Any]]:
        return self._cached(
            f"info:{token_address}",
            lambda: self._run("token", "info", "--address", token_address),
        )


# ----------------------------------------------------------------- parsing
# Le format exact du CLI n'est pas figé entre versions : on accepte plusieurs
# noms de champs plutôt que de casser au premier renommage.


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _as_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("list", "trades", "rank", "holders", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _token_address(trade: dict[str, Any]) -> Optional[str]:
    """Adresse du TOKEN échangé, pas celle du wallet.

    ⚠️ `address` seul est ambigu : c'est le wallet dans le flux smart money et
    le token ailleurs. Il passe donc en dernier, après tous les noms
    explicites — sinon les trades sont regroupés par trader au lieu du token
    et l'activité par token devient fausse sans lever d'erreur.
    """
    # `base_address` est le nom réel dans /v1/user/smartmoney (vérifié en live) ;
    # les autres couvrent les variantes des endpoints token/market.
    for key in ("base_address", "base_token", "token_address", "mint",
                "contract_address", "token", "address"):
        value = trade.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            nested = value.get("address") or value.get("mint")
            if nested:
                return nested
    return None


def _timestamp(trade: dict[str, Any]) -> Optional[float]:
    for key in ("timestamp", "time", "created_at", "block_time", "trade_time"):
        value = trade.get(key)
        if value is None:
            continue
        seconds = _f(value)
        if seconds > 1e12:  # millisecondes
            seconds /= 1000
        if seconds > 0:
            return seconds
    return None


def _is_buy(trade: dict[str, Any]) -> Optional[bool]:
    for key in ("side", "event", "type", "direction"):
        value = str(trade.get(key, "")).lower()
        if "buy" in value:
            return True
        if "sell" in value:
            return False
    return None  # forme non reconnue : ni achat ni vente, exclu par l'appelant


def _wallet_address(trade: dict[str, Any]) -> Optional[str]:
    for key in ("maker", "wallet_address", "wallet"):
        value = trade.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _wallet_tags(trade: dict[str, Any]) -> frozenset[str]:
    """Tags GMGN du wallet (`maker_info.tags` dans le flux smartmoney)."""
    maker_info = trade.get("maker_info")
    if isinstance(maker_info, dict):
        tags = maker_info.get("tags")
        if isinstance(tags, list):
            return frozenset(str(tag).lower() for tag in tags if tag)
    tags = trade.get("tags")
    if isinstance(tags, list):
        return frozenset(str(tag).lower() for tag in tags if tag)
    return frozenset()


def _trade_usd(trade: dict[str, Any]) -> float:
    for key in ("amount_usd", "volume_usd", "cost_usd"):
        value = trade.get(key)
        if value is not None:
            return _f(value)
    return 0.0


def _is_directional_smart_money_trade(
    trade: dict[str, Any],
    *,
    excluded_tags: frozenset[str],
    required_tags: frozenset[str],
    min_trade_usd: float,
) -> bool:
    """True si le trade ressemble à un achat/vente directionnel copiable.

    Heuristiques (dans l'ordre) :
    1. Tag ``arbitrager`` / ``wash_trader`` → bot, exclu.
    2. Montant USD < seuil → micro-arbitrage MEV, exclu.
    3. Tags requis absents → profil GMGN non confirmé, exclu si configuré.
    """
    tags = _wallet_tags(trade)
    if tags & excluded_tags:
        return False
    if required_tags and not (tags & required_tags):
        return False
    if min_trade_usd > 0 and _trade_usd(trade) < min_trade_usd:
        return False
    return True
