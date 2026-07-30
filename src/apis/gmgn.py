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
from typing import Any, Optional

from src.core.ratelimit import RateLimiter

COMMAND_TIMEOUT = 30
CACHE_TTL_SECONDS = 120  # les achats smart money bougent vite, mais pas à 20s
SMART_MONEY_WINDOW_MINUTES = 30

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

    @property
    def net_pressure(self) -> float:
        """1.0 = que des achats, 0.0 = que des ventes, 0.5 = équilibre."""
        total = self.buys + self.sells
        return self.buys / total if total else 0.5


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
                "[GMGN] clé absente — module désactivé. "
                "Configure-la avec : gmgn-cli config"
            )

    def _has_key(self) -> bool:
        """`config --check` sort en 0 si la clé est configurée."""
        if os.getenv("GMGN_API_KEY"):
            return True
        try:
            result = subprocess.run(
                [self.binary, "config", "--check"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

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
                command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT
            )
            self.request_count += 1

            if result.returncode != 0:
                stderr = (result.stderr or "").strip()[:120]
                print(f"[GMGN] {' '.join(args[:2])} échec : {stderr}")
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
        self, limit: int = 200, window_minutes: int = SMART_MONEY_WINDOW_MINUTES
    ) -> dict[str, SmartMoneyActivity]:
        """Indexe le flux smart money par adresse de token."""
        cutoff = time.time() - window_minutes * 60
        grouped: dict[str, dict[str, Any]] = {}

        for trade in self.fetch_smart_money_trades(limit):
            address = _token_address(trade)
            if not address:
                continue
            timestamp = _timestamp(trade)
            if timestamp is not None and timestamp < cutoff:
                continue

            bucket = grouped.setdefault(
                address, {"buys": 0, "sells": 0, "wallets": set(), "volume": 0.0}
            )
            if _is_buy(trade):
                bucket["buys"] += 1
            else:
                bucket["sells"] += 1
            wallet = trade.get("wallet_address") or trade.get("address") or trade.get("maker")
            if wallet:
                bucket["wallets"].add(wallet)
            bucket["volume"] += _f(trade.get("volume_usd") or trade.get("amount_usd"))

        return {
            address: SmartMoneyActivity(
                buys=bucket["buys"],
                sells=bucket["sells"],
                unique_wallets=len(bucket["wallets"]),
                volume_usd=round(bucket["volume"], 2),
                window_minutes=window_minutes,
            )
            for address, bucket in grouped.items()
        }

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
    for key in ("token_address", "mint", "contract_address", "token", "address"):
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


def _is_buy(trade: dict[str, Any]) -> bool:
    for key in ("side", "event", "type", "direction"):
        value = str(trade.get(key, "")).lower()
        if "buy" in value:
            return True
        if "sell" in value:
            return False
    return True  # défaut prudent : compté comme achat, tracé par les tests
