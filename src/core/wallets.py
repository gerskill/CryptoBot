"""Registre des wallets observés — ÉTAPE 1 : mesurer, ne rien décider.

POURQUOI PAS DU COPY-TRADING. Copier un achat veut dire entrer APRÈS, et
payer le pump que le wallet vient de créer. Pire : on copie l'achat et on rate
la vente — il sort sans prévenir, on garde le sac. Un wallet n'est pas un
ordre à recopier, c'est un TÉMOIN dont il faut d'abord établir la crédibilité.

CE QUE CE MODULE MESURE, et pourquoi c'est la bonne mesure :

  L'AVANCE (`lead_minutes`), pas le gain. Un wallet qui gagne mais achète en
  même temps que tout le monde n'apporte aucune information : son gain vient
  du marché, pas de sa connaissance. Un wallet qui achète 12 minutes avant que
  le token n'apparaisse dans nos scans a vu quelque chose. C'est cette avance,
  et elle seule, qui distingue l'informé du chanceux.

CE QUE CE MODULE NE FAIT PAS : voter, pondérer, déclencher une entrée. Aucun
signal ne sort d'ici tant que `AgentScoreboard` n'a pas jugé ces wallets sur
assez de cas — même garde que pour les agents (50 trades, 20 signaux). Un
wallet « rentable » sur 10 observations, dans une population de milliers, est
du bruit qu'on prendrait pour un edge.

DANGER SPÉCIFIQUE, à garder en tête pour l'étape 3 : un wallet régulièrement
en avance est peut-être l'insider ou le développeur. `is_wash_trading`,
`bundler_rate` et `creator_token_status` arrivent gratuitement dans le flux de
découverte — les croiser avant d'accorder le moindre poids.

COÛT : zéro appel. Le flux smart money est déjà récupéré une fois par cycle
pour `smart_money_buys_30m` ; on lit ce qui est là.
"""

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

# Un wallet vu sur un seul token n'est pas un profil, c'est une coïncidence.
# 2 et non 3 : au démarrage du registre aucun wallet n'atteint 3 tokens, et
# un seuil qui ne laisse RIEN voir empêche de juger si la piste vaut la peine.
# À remonter quand l'échantillon le permettra — le seuil qui protège vraiment
# est MIN_TRADES_FOR_WEIGHTS, côté scoreboard, avant d'AGIR sur ces profils.
MIN_TOKENS_FOR_PROFILE = 2
# Tags GMGN qui disqualifient d'emblée : ce ne sont pas des directionnels.
EXCLUDED_TAGS = frozenset({"arbitrager", "wash_trader", "bot"})


@dataclass(frozen=True)
class WalletObservation:
    """Un wallet a touché un token, et de combien de temps il nous précédait."""

    wallet: str
    token_address: str
    symbol: str
    side: str
    wallet_ts: float
    amount_usd: float
    tags: tuple[str, ...] = ()
    bot_first_seen_ts: Optional[float] = None

    @property
    def lead_minutes(self) -> Optional[float]:
        """Minutes d'avance sur NOTRE découverte. Négatif = il est en retard."""
        if self.bot_first_seen_ts is None:
            return None
        return round((self.bot_first_seen_ts - self.wallet_ts) / 60, 2)

    @property
    def is_directional(self) -> bool:
        return not (set(self.tags) & EXCLUDED_TAGS)

    def as_row(self) -> dict[str, Any]:
        return {
            "wallet": self.wallet,
            "token_address": self.token_address,
            "symbol": self.symbol,
            "side": self.side,
            "wallet_ts": self.wallet_ts,
            "amount_usd": round(self.amount_usd, 2),
            "tags": list(self.tags),
            "bot_first_seen_ts": self.bot_first_seen_ts,
            "lead_minutes": self.lead_minutes,
            "recorded_at": round(time.time(), 3),
        }


@dataclass(frozen=True)
class WalletProfile:
    """Ce qu'on sait d'un wallet. Aucun jugement, que des comptes."""

    wallet: str
    tokens: int
    buys: int
    sells: int
    volume_usd: float
    tags: tuple[str, ...]
    median_lead_minutes: Optional[float]
    first_seen: float
    last_seen: float

    @property
    def profiled(self) -> bool:
        """Assez d'observations pour que le mot « profil » ait un sens."""
        return self.tokens >= MIN_TOKENS_FOR_PROFILE

    def as_dict(self) -> dict[str, Any]:
        return {
            "wallet": self.wallet,
            "tokens": self.tokens,
            "buys": self.buys,
            "sells": self.sells,
            "volume_usd": round(self.volume_usd, 2),
            "tags": list(self.tags),
            "median_lead_minutes": self.median_lead_minutes,
            "profiled": self.profiled,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


class WalletRegistry:
    """JSONL append-only des couples (wallet, token) vus pour la première fois.

    Déduplication sur (wallet, token) : un wallet qui accumule sur le même
    token sur dix cycles ne doit compter qu'une fois, sinon les gros
    accumulateurs écrasent la statistique et le « nombre de tokens touchés »
    ne veut plus rien dire.
    """

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._seen: set[tuple[str, str]] = set()
        for row in self.read_all():
            self._seen.add((row.get("wallet", ""), row.get("token_address", "")))
        # Première vue PERSISTÉE. En mémoire seule, un redémarrage ferait
        # passer tous les tokens pour « découverts à l'instant » et gonflerait
        # chaque avance de plusieurs heures. `TokenCache._seen` ne convient
        # pas : c'est une DERNIÈRE vue, rafraîchie à chaque scan et purgée au
        # TTL.
        self.first_seen_path = f"{os.path.splitext(path)[0]}_first_seen.json"
        self._first_seen: dict[str, float] = self._load_first_seen()

    def _load_first_seen(self) -> dict[str, float]:
        if not os.path.exists(self.first_seen_path):
            return {}
        try:
            with open(self.first_seen_path, encoding="utf-8") as fh:
                return {k: float(v) for k, v in json.load(fh).items()}
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            print("[Wallets] premières vues illisibles — repart à vide")
            return {}

    def mark_first_seen(self, addresses: Iterable[str], now: Optional[float] = None) -> None:
        """Horodate la découverte. Ne remonte JAMAIS une date déjà posée."""
        now = now if now is not None else time.time()
        nouveaux = [a for a in addresses if a and a not in self._first_seen]
        if not nouveaux:
            return
        for address in nouveaux:
            self._first_seen[address] = now
        self._write_first_seen()

    def _write_first_seen(self) -> None:
        """Écriture atomique : un fichier tronqué ferait repartir les dates."""
        import tempfile

        directory = os.path.dirname(os.path.abspath(self.first_seen_path))
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._first_seen, fh)
            os.replace(tmp, self.first_seen_path)
        except Exception as exc:
            if os.path.exists(tmp):
                os.unlink(tmp)
            print(f"[Wallets] premières vues non sauvegardées : {exc}")

    @property
    def first_seen(self) -> dict[str, float]:
        return dict(self._first_seen)

    def observe(
        self,
        trades: Iterable[dict[str, Any]],
        first_seen_by_token: Optional[dict[str, float]] = None,
        known_tokens: Optional[set[str]] = None,
    ) -> int:
        """Enregistre les couples inédits. Retourne le nombre de lignes écrites.

        `known_tokens` restreint aux tokens que le bot a effectivement vus :
        sans ce filtre le registre avalerait tout le flux de la chaîne, dont
        l'écrasante majorité ne croisera jamais une décision, et l'avance
        deviendrait incalculable faute de référence.
        """
        first_seen_by_token = first_seen_by_token or {}
        rows: list[dict[str, Any]] = []

        for trade in trades:
            wallet = trade.get("maker") or trade.get("wallet") or trade.get("address")
            token = _token_address(trade)
            if not wallet or not token:
                continue
            if known_tokens is not None and token not in known_tokens:
                continue
            key = (wallet, token)
            if key in self._seen:
                continue

            observation = WalletObservation(
                wallet=wallet,
                token_address=token,
                symbol=(trade.get("base_token") or {}).get("symbol") or "?",
                side=str(trade.get("side") or "?"),
                wallet_ts=_timestamp(trade) or time.time(),
                amount_usd=_float(trade.get("amount_usd")),
                tags=tuple((trade.get("maker_info") or {}).get("tags") or ()),
                bot_first_seen_ts=first_seen_by_token.get(token),
            )
            self._seen.add(key)
            rows.append(observation.as_row())

        if rows:
            with open(self.path, "a", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return len(rows)

    def read_all(self) -> list[dict[str, Any]]:
        """Ligne corrompue ignorée plutôt que de tout perdre."""
        if not os.path.exists(self.path):
            return []
        rows = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def profiles(self, directional_only: bool = True) -> list[WalletProfile]:
        """Un profil par wallet, trié par avance médiane décroissante."""
        import statistics

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in self.read_all():
            tags = set(row.get("tags") or ())
            if directional_only and tags & EXCLUDED_TAGS:
                continue
            grouped.setdefault(row.get("wallet", "?"), []).append(row)

        profiles = []
        for wallet, rows in grouped.items():
            leads = [
                r["lead_minutes"] for r in rows
                if r.get("lead_minutes") is not None
            ]
            timestamps = [r.get("wallet_ts") or 0 for r in rows]
            tags: set[str] = set()
            for r in rows:
                tags |= set(r.get("tags") or ())
            profiles.append(
                WalletProfile(
                    wallet=wallet,
                    tokens=len({r.get("token_address") for r in rows}),
                    buys=sum(1 for r in rows if r.get("side") == "buy"),
                    sells=sum(1 for r in rows if r.get("side") == "sell"),
                    volume_usd=sum(_float(r.get("amount_usd")) for r in rows),
                    tags=tuple(sorted(tags)),
                    median_lead_minutes=(
                        round(statistics.median(leads), 2) if leads else None
                    ),
                    first_seen=min(timestamps) if timestamps else 0,
                    last_seen=max(timestamps) if timestamps else 0,
                )
            )

        return sorted(
            profiles,
            key=lambda p: (
                not p.profiled,
                -(p.median_lead_minutes if p.median_lead_minutes is not None else -1e9),
            ),
        )

    def wallets_for(self, token_address: str) -> list[dict[str, Any]]:
        """Wallets ayant touché ce token, du plus en avance au plus tardif.

        C'est ce que l'étape 2 journalisera à l'entrée : sans trace de QUI
        avait acheté avant nous, aucun score de wallet n'est calculable après
        coup.
        """
        rows = [r for r in self.read_all() if r.get("token_address") == token_address]
        return sorted(rows, key=lambda r: r.get("wallet_ts") or 0)

    @property
    def stats(self) -> dict[str, Any]:
        rows = self.read_all()
        profiles = self.profiles()
        profiled = [p for p in profiles if p.profiled]
        leads = [p.median_lead_minutes for p in profiled
                 if p.median_lead_minutes is not None]
        return {
            "observations": len(rows),
            "wallets": len(profiles),
            "profiled": len(profiled),
            "tokens": len({r.get("token_address") for r in rows}),
            "median_lead_minutes": (
                round(sorted(leads)[len(leads) // 2], 2) if leads else None
            ),
        }


def _token_address(trade: dict[str, Any]) -> Optional[str]:
    """`base_address` d'abord : c'est le champ réel de /v1/user/smartmoney."""
    for key in ("base_address", "token_address", "address", "mint"):
        value = trade.get(key)
        if value:
            return str(value)
    return None


def _timestamp(trade: dict[str, Any]) -> Optional[float]:
    for key in ("timestamp", "time", "created_at", "block_time", "trade_time"):
        value = trade.get(key)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        # Certaines versions du CLI rendent des millisecondes.
        return number / 1000 if number > 1e11 else number
    return None


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
