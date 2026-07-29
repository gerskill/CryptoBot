"""Module Twitter/X v2 — buzz social.

QUOTAS RÉELS, mesurés sur les en-têtes `x-rate-limit-limit` de ce compte :
    /2/tweets/counts/recent  -> 300 req / 15 min
    /2/tweets/search/recent  -> 450 req / 15 min
Soit 750 requêtes / 15 min au total, largement au-dessus des 60 supposés au
départ. Le budget n'est donc PAS la contrainte à ce palier : la contrainte est
la qualité du signal.

DEUX ENDPOINTS, DEUX RÔLES
--------------------------
`counts/recent` donne le VOLUME EXACT (`meta.total_tweet_count`) sans plafond,
plus une courbe minute par minute qui donne la vélocité réelle. C'est la source
de vérité du volume.

`search/recent` plafonne à 100 résultats par appel — inutilisable pour compter,
mais c'est le seul à renvoyer les auteurs et l'engagement. Il sert donc
d'ÉCHANTILLON de qualité, pas de compteur.

Un token = 2 requêtes. Avec 8 tokens par cycle de 90 s : 16 req/cycle,
160 req/15 min sur 750 disponibles -> 21% du budget. `max_lookups_per_cycle`
plafonne la casse si le scan remonte 30 candidats d'un coup.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

from src.core.ratelimit import RateLimiter

SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"
COUNTS_URL = "https://api.twitter.com/2/tweets/counts/recent"
REQUEST_TIMEOUT = 15
CACHE_TTL_SECONDS = 600
MIN_SYMBOL_LEN = 4
SAMPLE_SIZE = 100
VELOCITY_WINDOW_MINUTES = 15

# Mots trop courants pour servir de terme de recherche : ils ramèneraient des
# tweets sans aucun rapport avec le token.
AMBIGUOUS_SYMBOLS = {
    "MOON", "PUMP", "GOOD", "BEST", "LOVE", "HOPE", "CASH", "GOLD", "COIN",
    "TEST", "REAL", "TRUE", "FREE", "KING", "BABY", "DOGE", "TRUMP", "SOL",
    "PEPE", "WIF", "BONK", "CAT", "DOG", "MEME", "ELON", "MUSK",
}


@dataclass(frozen=True)
class SocialStats:
    """Signal social d'un token. `mentions_1h` est un compte EXACT."""

    mentions_1h: int
    velocity_15m: int
    unique_authors: int
    engagement: int
    term_used: str
    sample_size: int = 0

    @property
    def author_diversity(self) -> float:
        """1.0 = un auteur par tweet. Proche de 0 = spam d'un seul compte."""
        return self.unique_authors / self.sample_size if self.sample_size else 0.0

    @property
    def acceleration(self) -> float:
        """>1 = le rythme des 15 dernières minutes dépasse celui de l'heure."""
        if self.mentions_1h <= 0:
            return 0.0
        return self.velocity_15m * 4 / self.mentions_1h


class TwitterAPI:
    def __init__(self, bearer_token: Optional[str], max_lookups_per_cycle: int = 8):
        self.bearer_token = bearer_token
        self.enabled = bool(bearer_token)
        self.max_lookups_per_cycle = max_lookups_per_cycle
        self.session = requests.Session()
        # Limiteurs calés sur les quotas réels, marge de 10% incluse.
        self.counts_limiter = RateLimiter(300, 900.0, name="twitter/counts")
        self.search_limiter = RateLimiter(450, 900.0, name="twitter/search")
        self.request_count = 0
        self._cache: dict[str, tuple[float, SocialStats]] = {}

    # ---------------------------------------------------------------- terme

    def search_term(self, symbol: str, token_address: str) -> str:
        """Symbole si discriminant, sinon adresse de contrat.

        Une mention d'adresse est rare mais quasi incontestable ; une mention
        de "MOON" ne prouve rien.
        """
        clean = (symbol or "").strip()
        if (
            len(clean) >= MIN_SYMBOL_LEN
            and clean.isalnum()
            and clean.upper() not in AMBIGUOUS_SYMBOLS
        ):
            return clean
        return token_address

    # ------------------------------------------------------------- transport

    def _get(self, url: str, params: dict[str, Any], limiter: RateLimiter) -> Optional[dict]:
        limiter.acquire()
        try:
            response = self.session.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self.bearer_token}"},
                timeout=REQUEST_TIMEOUT,
            )
            self.request_count += 1

            if response.status_code == 429:
                reset = response.headers.get("x-rate-limit-reset")
                print(f"[Twitter] 429 sur {url.rsplit('/', 1)[-1]} (reset {reset})")
                return None
            if response.status_code in (401, 403):
                print(f"[Twitter] {response.status_code} : bearer refusé ou plan insuffisant")
                self.enabled = False
                return None
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as exc:
            print(f"[Twitter] erreur réseau : {exc}")
        except ValueError as exc:
            print(f"[Twitter] JSON invalide : {exc}")
        return None

    @staticmethod
    def _start_time(minutes: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    # ------------------------------------------------------------- mesures

    def get_volume(self, term: str, minutes: int = 60) -> Optional[tuple[int, int]]:
        """(mentions sur la fenêtre, mentions des 15 dernières minutes).

        Compte EXACT : `counts/recent` n'a pas le plafond de 100 de `search`.
        """
        body = self._get(
            COUNTS_URL,
            {
                "query": f'"{term}" -is:retweet',
                "granularity": "minute",
                "start_time": self._start_time(minutes),
            },
            self.counts_limiter,
        )
        if body is None:
            return None
        buckets = body.get("data") or []
        total = (body.get("meta") or {}).get("total_tweet_count")
        if total is None:
            total = sum(b.get("tweet_count", 0) for b in buckets)
        recent = sum(b.get("tweet_count", 0) for b in buckets[-VELOCITY_WINDOW_MINUTES:])
        return int(total), int(recent)

    def get_quality(self, term: str, minutes: int = 60) -> tuple[int, int, int]:
        """(auteurs uniques, engagement, taille d'échantillon).

        Échantillon plafonné à 100 tweets : à lire comme un taux, pas un total.
        """
        body = self._get(
            SEARCH_URL,
            {
                "query": f'"{term}" -is:retweet',
                "start_time": self._start_time(minutes),
                "max_results": SAMPLE_SIZE,
                "tweet.fields": "author_id,public_metrics",
            },
            self.search_limiter,
        )
        tweets = (body or {}).get("data") or []
        engagement = 0
        for tweet in tweets:
            metrics = tweet.get("public_metrics") or {}
            engagement += (
                int(metrics.get("like_count") or 0)
                + int(metrics.get("retweet_count") or 0)
                + int(metrics.get("reply_count") or 0)
                + int(metrics.get("quote_count") or 0)
            )
        authors = len({t.get("author_id") for t in tweets if t.get("author_id")})
        return authors, engagement, len(tweets)

    def get_social_stats(
        self, symbol: str, token_address: str, minutes: int = 60
    ) -> Optional[SocialStats]:
        """Volume exact + qualité. 2 requêtes, ~0.27% du budget 15 min."""
        if not self.enabled:
            return None

        cached = self._cache.get(token_address)
        if cached and time.time() - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]

        term = self.search_term(symbol, token_address)
        volume = self.get_volume(term, minutes)
        if volume is None:
            return None
        mentions, velocity = volume

        # Pas une seule mention : inutile de dépenser la requête de qualité.
        if mentions == 0:
            stats = SocialStats(0, 0, 0, 0, term, 0)
        else:
            authors, engagement, sample = self.get_quality(term, minutes)
            stats = SocialStats(mentions, velocity, authors, engagement, term, sample)

        self._cache[token_address] = (time.time(), stats)
        return stats

    # ------------------------------------------------------- enrichissement

    def enrich(self, candidates: list[Any]) -> dict[str, SocialStats]:
        """Social des `max_lookups_per_cycle` premiers candidats. Clé : adresse."""
        if not self.enabled or not candidates:
            return {}

        results: dict[str, SocialStats] = {}
        for candidate in candidates[: self.max_lookups_per_cycle]:
            stats = self.get_social_stats(candidate.symbol, candidate.token_address)
            if stats is not None:
                results[candidate.token_address] = stats

        if results:
            print(
                f"[Twitter] {len(results)} tokens mesurés | {self.request_count} req cumulées "
                f"| quota restant counts {self.counts_limiter.available}/15min, "
                f"search {self.search_limiter.available}/15min"
            )
        self.purge_cache()
        return results

    def purge_cache(self) -> None:
        now = time.time()
        for address in [a for a, (ts, _) in self._cache.items() if now - ts >= CACHE_TTL_SECONDS]:
            del self._cache[address]
