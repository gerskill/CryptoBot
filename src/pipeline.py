"""Pipeline de scan : découverte -> enrichissement -> filtres -> scoring.

DEUX PHASES SÉPARÉES, et la séparation est structurante :

  `collect()`  — tout ce qui coûte un appel API. Une fois par cycle.
  `evaluate()` — filtres + scoring. CPU pur, aucune I/O, aucun effet de bord.

Le budget API est la contrainte du bot ; le filtrage de 25 candidats ne coûte
rien. Découpés ainsi, N stratégies peuvent juger le MÊME lot enrichi pour le
prix d'une seule collecte. `run_cycle()` reste le chemin mono-stratégie.

Corollaire à ne pas oublier : `evaluate()` ne touche plus au cache. Les effets
(surveiller, dé-surveiller, blacklister) sont retournés et appliqués par
l'appelant sur l'UNION des stratégies — sinon la dernière évaluée écrase les
décisions des autres.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Optional

from src.analysis.technical import PriceHistory
from src.apis.birdeye import BirdeyeAPI
from src.apis.dexscreener import DexScreenerAPI
from src.apis.gmgn import GmgnAPI
from src.apis.helius import HeliusAPI
from src.apis.rugcheck import RugCheckAPI
from src.apis.twitter import TwitterAPI
from src.core.cache import RUGPULL_BLACKLIST_HOURS, TokenCache
from src.core.journal import TradeJournal
from src.core.models import Candidate, ScanResult
from src.core.params import ParamsStore
from src.core.scoring import score_candidates
from src.core.shadow import ShadowTracker
from src.core.wallet_scoreboard import score_wallets
from src.core.wallets import WalletRegistry

MAX_ENRICH_WORKERS = 5
AUDIT_TTL_SECONDS = 900  # un audit sécurité ne change pas toutes les 90s


@dataclass(frozen=True)
class CollectedBatch:
    """Le lot enrichi d'un cycle, partagé par toutes les stratégies."""

    enriched: tuple[Candidate, ...] = ()
    scanned_count: int = 0
    cached_skipped: int = 0
    duration_sec: float = 0.0
    # Verdicts de SÉCURITÉ (RugCheck), pas de stratégie : un honeypot l'est
    # pour tout le monde. À bannir une fois, sans qu'aucune stratégie ne
    # puisse débannir.
    blacklist: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ArmEvaluation:
    """Le verdict d'UNE stratégie sur un lot. Aucun effet de bord appliqué."""

    result: ScanResult
    kept_addresses: frozenset[str] = frozenset()
    rejected_addresses: frozenset[str] = frozenset()
    # Adresses méritant une mesure sociale, par ordre de préférence.
    wishlist: tuple[str, ...] = ()
    social_served: int = 0


def _opt_float(value: Any) -> Optional[float]:
    """None reste None : une donnée absente ne doit jamais rejeter."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _opt_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _opt_bool(value: Any) -> Optional[bool]:
    return bool(value) if value is not None else None


def _duration(hours: float) -> str:
    """Durée pour GMGN : suffixe obligatoire, un nombre nu est refusé."""
    if hours < 1:
        return f"{max(1, int(round(hours * 60)))}m"
    return f"{hours:g}h"


def gmgn_to_candidate(
    row: dict[str, Any], interval: str = "1h", chain: str = "solana"
) -> Optional[Candidate]:
    """Une ligne de `market trending` -> Candidate, sans appel supplémentaire.

    Ce classement porte déjà liquidité, holders, concentration top-10, autorités
    révoquées et compte de smart money. Un token qui échoue ici est écarté sans
    avoir coûté un seul appel Birdeye ou RugCheck.

    `rugcheck_score` reste None : c'est RugCheck qui fait autorité, et laisser
    None fait que le filtre ne rejette pas (invariant du pipeline) au lieu
    d'inventer une note.
    """
    address = row.get("address")
    if not address:
        return None

    created = row.get("creation_timestamp") or row.get("open_timestamp") or 0
    age_hours = (time.time() - created) / 3600 if created else 999.0
    liquidity = float(row.get("liquidity") or 0)
    volume = float(row.get("volume") or 0)
    top10 = row.get("top_10_holder_rate")
    smart = row.get("smart_degen_count")

    # `volume` porte l'intervalle DEMANDÉ, pas une fenêtre fixe. Le ranger
    # dans `volume_1h` quel que soit l'intervalle ferait comparer un volume
    # 5 min à un seuil horaire. Exception assumée : pour un token plus jeune
    # qu'une heure, son volume 5 min EST sa vie entière, donc c'est une borne
    # inférieure légitime de son volume 1 h.
    volumes: dict[str, float] = {}
    if interval in ("1m", "5m"):
        volumes["volume_5m"] = volume
        if age_hours < 1:
            volumes["volume_1h"] = volume
    elif interval == "24h":
        volumes["volume_24h"] = volume
    else:
        volumes["volume_1h"] = volume

    return Candidate(
        token_address=address,
        symbol=row.get("symbol") or "?",
        name=row.get("name") or "",
        chain=chain,
        url=f"https://gmgn.ai/sol/token/{address}",
        price_usd=float(row.get("price") or 0),
        liquidity_usd=liquidity,
        market_cap=float(row.get("market_cap") or 0),
        **volumes,
        age_hours=age_hours,
        volume_liquidity_ratio=volume / liquidity if liquidity else 0.0,
        price_change_5m=float(row.get("price_change_percent5m") or 0),
        price_change_1h=float(row.get("price_change_percent1h") or 0),
        buys_5m=int(row.get("buys") or 0),
        sells_5m=int(row.get("sells") or 0),
        holders=int(row["holder_count"]) if row.get("holder_count") is not None else None,
        holders_is_exact=row.get("holder_count") is not None,
        top10_holder_pct=float(top10) * 100 if top10 is not None else None,
        mint_authority_revoked=bool(row["renounced_mint"])
        if row.get("renounced_mint") is not None
        else None,
        freeze_authority_revoked=bool(row["renounced_freeze_account"])
        if row.get("renounced_freeze_account") is not None
        else None,
        smart_money_buys_30m=int(smart) if smart is not None else None,
        # --- Manipulation et réputation : gratuits, ils viennent du même appel
        swaps=_opt_int(row.get("swaps")),
        sniper_count=_opt_int(row.get("sniper_count")),
        bot_degen_count=_opt_int(row.get("bot_degen_count")),
        renowned_count=_opt_int(row.get("renowned_count")),
        bundler_rate=_opt_float(row.get("bundler_rate")),
        insider_rate=_opt_float(row.get("insider_rate")),
        entrapment_ratio=_opt_float(row.get("entrapment_ratio")),
        rug_ratio=_opt_float(row.get("rug_ratio")),
        is_wash_trading=_opt_bool(row.get("is_wash_trading")),
        bluechip_owner_pct=_opt_float(row.get("bluechip_owner_percentage")),
        burn_ratio=_opt_float(row.get("burn_ratio")),
        dev_token_burn_ratio=_opt_float(row.get("dev_token_burn_ratio")),
        creator_token_status=row.get("creator_token_status") or None,
        twitter_rename_count=_opt_int(row.get("twitter_rename_count")),
        twitter_create_token_count=_opt_int(row.get("twitter_create_token_count")),
        has_social=bool(
            row.get("twitter_username") or row.get("website") or row.get("telegram")
        ),
        launchpad=row.get("launchpad_platform") or row.get("launchpad") or None,
        initial_liquidity=_opt_float(row.get("initial_liquidity")),
        history_highest_market_cap=_opt_float(row.get("history_highest_market_cap")),
        price_change_1m=_opt_float(row.get("price_change_percent1m")),
        dexscreener_paid=bool(row.get("dexscr_ad") or row.get("dexscr_boost_fee")),
        raw=row,
    )


def discovery_envelope(filter_sets: list[dict[str, Any]]) -> dict[str, Any]:
    """Filtres de DÉCOUVERTE couvrant toutes les stratégies.

    `scan_new_meme_coins` filtre côté DexScreener : ce qu'il ne renvoie pas
    n'existe pour personne. L'enveloppe prend donc le plus laxiste de chaque
    borne — sauf `min_holders`, qui n'est PAS un filtre mais la profondeur de
    pagination Helius (`_enrich_one`). Une valeur trop basse rend un compte de
    holders sous-évalué, qu'une stratégie stricte rejetterait à tort : il faut
    le MAXIMUM. L'inversion est le piège de cette fonction.
    """
    sets = [f for f in filter_sets if f] or [{}]

    def loosest(key: str, default: Any, mode: str) -> Any:
        values = [f[key] for f in sets if f.get(key) is not None]
        if not values:
            return default
        return min(values) if mode == "min" else max(values)

    return {
        "min_liquidity_usd": min(f.get("min_liquidity_usd", 15000) for f in sets),
        "min_volume_1h": min(f.get("min_volume_1h", 8000) for f in sets),
        "max_age_hours": max(f.get("max_age_hours", 6) for f in sets),
        "min_age_hours": min(f.get("min_age_hours", 0.5) for f in sets),
        "min_holders": max(f.get("min_holders", 75) for f in sets),
        # Bornes envoyées au filtrage SERVEUR de GMGN. Toujours la plus lâche :
        # ce que le service ne renvoie pas n'existe pour aucun bras. Un `None`
        # partout signifie « aucun bras ne filtre là-dessus », donc pas de
        # contrainte envoyée.
        "min_swaps": loosest("min_swaps", None, "min"),
        "min_market_cap": loosest("min_market_cap", None, "min"),
        "max_bundler_rate": loosest("max_bundler_rate", None, "max"),
        "max_insider_rate": loosest("max_insider_rate", None, "max"),
        "max_top10_holder_rate": (
            max(f["max_top_wallet_concentration"] for f in sets
                if f.get("max_top_wallet_concentration") is not None) / 100
            if any(f.get("max_top_wallet_concentration") is not None for f in sets)
            else None
        ),
    }


class ScanPipeline:
    def __init__(
        self,
        params: ParamsStore,
        cache: TokenCache,
        dex: DexScreenerAPI,
        helius: HeliusAPI,
        rugcheck: RugCheckAPI,
        birdeye: Optional[BirdeyeAPI] = None,
        twitter: Optional[TwitterAPI] = None,
        gmgn: Optional[GmgnAPI] = None,
        history: Optional[PriceHistory] = None,
        wallets: Optional[WalletRegistry] = None,
        journal: Optional[TradeJournal] = None,
        shadow: Optional[ShadowTracker] = None,
    ):
        self.params = params
        self.cache = cache
        self.dex = dex
        self.helius = helius
        self.rugcheck = rugcheck
        self.birdeye = birdeye
        self.twitter = twitter
        self.gmgn = gmgn
        self.history = history or PriceHistory()
        self.wallets = wallets
        self.journal = journal
        self.shadow = shadow
        self._audit_cache: dict[str, tuple[float, dict]] = {}

    # ------------------------------------------------------------ collecte

    def collect(self, envelope: Optional[dict[str, Any]] = None) -> CollectedBatch:
        """Découverte + enrichissement. TOUT le coût API du cycle est ici."""
        started = time.time()
        if envelope is None:
            envelope = discovery_envelope([self.params.get("filters", {})])
        scan_cfg = self.params.get("scan", {})
        watched = set(self.cache.watched())

        raw: list[Candidate] = []
        skipped_total = 0
        for chain in scan_cfg.get("chains", ["solana"]):
            candidates, skipped = self.dex.scan_new_meme_coins(
                chain=chain,
                min_liquidity=envelope["min_liquidity_usd"],
                min_volume_1h=envelope["min_volume_1h"],
                max_age_hours=envelope["max_age_hours"],
                min_age_hours=envelope["min_age_hours"],
                always_include=watched,
            )
            raw.extend(candidates)
            skipped_total += skipped

        raw = self._merge(raw, self._discover_gmgn(envelope, scan_cfg))

        scanned_count = len(raw)
        raw = self._trim(raw, watched, scan_cfg.get("max_candidates_enriched", 25))

        enriched = self._enrich_wallet_reliability(
            self._enrich_smart_money(self._enrich_all(raw, envelope))
        )

        # Historique de prix sur TOUS les enrichis, pas seulement les retenus :
        # ça ne coûte rien, et une stratégie qui relâche un filtre trouve son
        # historique déjà chaud au lieu d'attendre trois cycles.
        self.history.record_all(enriched)
        self._purge_audit_cache()

        blacklist = tuple(
            (c.token_address, c.rejected_reason)
            for c in enriched
            if c.rejected_reason and "risque critique" in c.rejected_reason
        )

        return CollectedBatch(
            enriched=tuple(enriched),
            scanned_count=scanned_count,
            cached_skipped=skipped_total,
            duration_sec=round(time.time() - started, 2),
            blacklist=blacklist,
        )

    # ---------------------------------------------------------- évaluation

    def evaluate(
        self,
        batch: CollectedBatch,
        params: Optional[ParamsStore] = None,
        social: Optional[dict[str, Any]] = None,
        label: str = "",
        verbose: bool = False,
    ) -> ArmEvaluation:
        """Filtres + scoring pour UNE stratégie. Idempotent, sans effet de bord.

        Appelée deux fois par cycle et par stratégie : une fois sans social
        pour produire la liste de souhaits, une fois avec. Le scoring de 25
        candidats est gratuit, et ça évite d'entrelacer l'arbitrage du quota
        Twitter avec le calcul des scores.
        """
        params = params or self.params
        filters = params.get("filters", {})
        weights = params.get("scoring_weights", {})

        candidates = list(batch.enriched)
        if social:
            candidates = [self._with_social(c, social.get(c.token_address)) for c in candidates]

        kept, rejected = self._apply_security_filters(
            candidates, filters, label=label, verbose=verbose
        )

        min_social = filters.get("min_social_mentions_1h", 0)
        social_kept = []
        for candidate in score_candidates(kept, weights):
            if (
                candidate.social_mentions_1h is not None
                and candidate.social_mentions_1h < min_social
            ):
                reason = f"mentions sociales {candidate.social_mentions_1h} < {min_social}"
                rejected.append(candidate.with_fields(rejected_reason=reason))
                if verbose:
                    print(f"    ❌{label} {candidate.symbol:>10} — {reason}")
                continue
            social_kept.append(candidate)
        scored = score_candidates(social_kept, weights)

        min_alpha = params.get("scan.social_lookup_min_alpha", 70)
        wishlist = tuple(
            c.token_address for c in scored if c.alpha_score_absolute >= min_alpha
        )
        served = sum(1 for c in scored if c.social_mentions_1h is not None)

        return ArmEvaluation(
            result=ScanResult(
                candidates=tuple(scored),
                rejected=tuple(rejected),
                scanned_count=batch.scanned_count,
                cached_skipped=batch.cached_skipped,
                duration_sec=batch.duration_sec,
            ),
            kept_addresses=frozenset(c.token_address for c in scored),
            rejected_addresses=frozenset(c.token_address for c in rejected),
            wishlist=wishlist,
            social_served=served,
        )

    @staticmethod
    def _with_social(candidate: Candidate, stats: Any) -> Candidate:
        if stats is None:
            return candidate
        return candidate.with_fields(
            social_mentions_1h=stats.mentions_1h,
            social_unique_authors=stats.unique_authors,
            social_engagement=stats.engagement,
            social_velocity_15m=stats.velocity_15m,
            social_sample_size=stats.sample_size,
        )

    def apply_cache_effects(
        self, kept: set[str], rejected: set[str], blacklist: tuple[tuple[str, str], ...] = ()
    ) -> None:
        """Effets de bord du cache, sur l'UNION des stratégies.

        Surveillé dès qu'UNE stratégie garde le token ; dé-surveillé seulement
        si AUCUNE ne le garde. Sans cette union, une stratégie qui rejette
        sortirait de la watchlist un token qu'une autre détient, l'historique
        de prix cesserait de se remplir et son analyse technique casserait en
        silence.
        """
        for address, reason in blacklist:
            self.cache.blacklist(address, reason, RUGPULL_BLACKLIST_HOURS)
        for address in kept:
            self.cache.watch(address)
        for address in rejected - kept:
            self.cache.unwatch(address)

    # -------------------------------------------------------- chemin simple

    def run_cycle(self) -> ScanResult:
        """Cycle complet mono-stratégie : collecte, social, évaluation."""
        batch = self.collect()
        pre = self.evaluate(batch)
        social = self.enrich_social(batch.enriched, pre.wishlist)
        evaluation = self.evaluate(batch, social=social, verbose=True)
        self.apply_cache_effects(
            set(evaluation.kept_addresses), set(evaluation.rejected_addresses), batch.blacklist
        )
        return evaluation.result

    def _discover_gmgn(self, envelope: dict[str, Any], scan_cfg: dict) -> list[Candidate]:
        """Découverte GMGN, filtrée côté serveur sur la fenêtre de l'enveloppe.

        Un seul appel par intervalle demandé. Les intervalles viennent de
        `scan.gmgn_intervals` : chaque bras a sa propre fenêtre d'âge, et un
        bras qui regarde les 5 dernières minutes n'a rien à faire du classement
        sur 6 h. L'union couvre tous les bras à la fois.
        """
        if not (self.gmgn and self.gmgn.enabled):
            return []

        intervals = scan_cfg.get("gmgn_intervals") or ["5m", "1h"]
        tags = scan_cfg.get("gmgn_filters") or ["not_wash_trading"]
        seen: dict[str, Candidate] = {}
        for interval in intervals:
            for row in self.gmgn.market_trending(
                interval=interval,
                limit=scan_cfg.get("gmgn_limit", 100),
                order_by=scan_cfg.get("gmgn_order_by", "volume"),
                min_liquidity=envelope["min_liquidity_usd"],
                min_created=_duration(envelope["min_age_hours"]),
                max_created=_duration(envelope["max_age_hours"]),
                min_swaps=envelope.get("min_swaps"),
                min_marketcap=envelope.get("min_market_cap"),
                max_bundler_rate=envelope.get("max_bundler_rate"),
                max_insider_rate=envelope.get("max_insider_rate"),
                max_top10_holder_rate=envelope.get("max_top10_holder_rate"),
                filters=tags,
            ):
                candidate = gmgn_to_candidate(row, interval=interval)
                if candidate is None:
                    continue
                # Un même token peut sortir sur plusieurs intervalles : on
                # fusionne pour cumuler volume_5m / volume_1h / volume_24h.
                existing = seen.get(candidate.token_address)
                if existing is None:
                    seen[candidate.token_address] = candidate
                    continue
                updates = {
                    field: getattr(candidate, field)
                    for field in ("volume_5m", "volume_1h", "volume_24h")
                    if getattr(candidate, field) and not getattr(existing, field)
                }
                if updates:
                    seen[candidate.token_address] = existing.with_fields(**updates)

        candidates = list(seen.values())
        if candidates:
            print(
                f"[GMGN] {len(candidates)} tokens depuis market trending "
                f"({'/'.join(intervals)}, liq ≥ {envelope['min_liquidity_usd']:.0f}$, "
                f"âge {_duration(envelope['min_age_hours'])}-"
                f"{_duration(envelope['max_age_hours'])})"
            )
        return candidates

    def _merge(self, primary: list[Candidate], extra: list[Candidate]) -> list[Candidate]:
        """Union par adresse. La source la plus riche gagne sur les doublons.

        DexScreener porte `pair_address`, indispensable au monitoring de prix ;
        GMGN porte holders, concentration et smart money. Sur un doublon on
        garde DexScreener et on lui greffe ce que GMGN sait en plus.
        """
        by_address = {c.token_address: c for c in primary}
        for candidate in extra:
            existing = by_address.get(candidate.token_address)
            if existing is None:
                by_address[candidate.token_address] = candidate
                continue
            updates = {
                field: getattr(candidate, field)
                for field in (
                    "holders", "holders_is_exact", "top10_holder_pct",
                    "mint_authority_revoked", "freeze_authority_revoked",
                    "smart_money_buys_30m",
                )
                if getattr(existing, field) is None
                and getattr(candidate, field) is not None
            }
            if updates:
                by_address[candidate.token_address] = existing.with_fields(**updates)
        return list(by_address.values())

    def _purge_audit_cache(self) -> None:
        """Évite la croissance mémoire du cache d'audit sur un run long."""
        now = time.time()
        for address in [
            a for a, (ts, _) in self._audit_cache.items() if now - ts >= AUDIT_TTL_SECONDS
        ]:
            del self._audit_cache[address]

    @staticmethod
    def _trim(candidates: list[Candidate], watched: set[str], limit: int) -> list[Candidate]:
        """Limite le nombre d'enrichissements sans jamais lâcher un token suivi."""
        kept = [c for c in candidates if c.token_address in watched]
        for candidate in candidates:
            if len(kept) >= limit:
                break
            if candidate.token_address not in watched:
                kept.append(candidate)
        return kept

    def _enrich_all(self, candidates: list[Candidate], filters: dict) -> list[Candidate]:
        if not candidates:
            return []
        with ThreadPoolExecutor(max_workers=MAX_ENRICH_WORKERS) as pool:
            return list(pool.map(lambda c: self._enrich_one(c, filters), candidates))

    def _enrich_one(self, candidate: Candidate, filters: dict) -> Candidate:
        """RugCheck d'abord (léger) : si risque critique, on épargne les autres.

        Les résultats d'audit sont mémorisés `AUDIT_TTL_SECONDS` : un token
        surveillé est re-scanné toutes les 90s pour son PRIX, pas pour son
        audit sécurité qui, lui, bouge lentement.
        """
        cached = self._audit_cache.get(candidate.token_address)
        if cached and time.time() - cached[0] < AUDIT_TTL_SECONDS:
            return candidate.with_fields(**cached[1])

        updates: dict = {}

        report = self.rugcheck.get_report(candidate.token_address)
        if report is not None:
            updates.update(
                rugcheck_score=report.safety_score,
                rugcheck_risks=report.risks,
                lp_locked_pct=report.lp_locked_pct,
                mint_authority_revoked=report.mint_authority_revoked,
                freeze_authority_revoked=report.freeze_authority_revoked,
            )
            if report.top_holder_pct is not None:
                updates["top_holder_pct"] = report.top_holder_pct
            if report.dev_wallet_pct is not None:
                updates["dev_wallet_pct"] = report.dev_wallet_pct
            if report.has_critical_risk:
                updates["rejected_reason"] = f"risque critique RugCheck : {report.risks}"
                self._audit_cache[candidate.token_address] = (time.time(), updates)
                return candidate.with_fields(**updates)

        # Birdeye donne le compte EXACT en 1 appel ; Helius demande jusqu'à
        # 3 appels et ne rend qu'une borne inférieure. Helius reste le repli.
        holders_done = False
        if self.birdeye and self.birdeye.enabled:
            overview = self.birdeye.get_overview(candidate.token_address)
            if overview and overview.holder_count is not None:
                updates["holders"] = overview.holder_count
                updates["holders_is_exact"] = True
                holders_done = True

        if not holders_done and self.helius.enabled:
            stats = self.helius.get_holder_stats(
                candidate.token_address, min_required=filters.get("min_holders", 75)
            )
            if stats is not None:
                updates["holders"] = stats.holder_count
                updates["holders_is_exact"] = stats.is_exact
                if stats.top_holder_pct is not None:
                    updates["top_holder_pct"] = stats.top_holder_pct
                updates["top10_holder_pct"] = stats.top10_holder_pct

        self._audit_cache[candidate.token_address] = (time.time(), updates)
        return candidate.with_fields(**updates)

    def _enrich_smart_money(self, candidates: list[Candidate]) -> list[Candidate]:
        """Achats smart money par token — UN seul appel couvre tout le lot.

        Le flux GMGN est global à la chaîne : on le récupère une fois et on
        l'indexe. Interroger token par token gaspillerait le quota.
        """
        if not (self.gmgn and self.gmgn.enabled) or not candidates:
            return candidates

        activity = self.gmgn.activity_by_token(
            **self._gmgn_activity_kwargs(),
        )
        if not activity:
            return candidates

        enriched = []
        touched = 0
        for candidate in candidates:
            stats = activity.get(candidate.token_address)
            if stats is None:
                # Absent du flux = zéro achat smart money observé, pas
                # « donnée manquante » : le filtre doit pouvoir s'appliquer.
                enriched.append(candidate.with_fields(smart_money_buys_30m=0))
                continue
            touched += 1
            enriched.append(
                candidate.with_fields(
                    smart_money_buys_30m=stats.buys,
                    smart_money_sells_30m=stats.sells,
                    smart_money_wallets_30m=stats.unique_wallets,
                    smart_money_volume_usd=stats.volume_usd,
                    smart_money_weighted_buys=stats.weighted_buys,
                    smart_money_newest_age_min=stats.newest_buy_age_minutes,
                )
            )
        if touched:
            print(f"[GMGN] {touched}/{len(candidates)} candidats avec activité smart money")
        return enriched

    def _enrich_wallet_reliability(self, candidates: list[Candidate]) -> list[Candidate]:
        """Meilleur wallet PROUVÉ fiable en avance sur chaque candidat.

        Recalculé à chaque cycle, sans appel API (voir wallet_scoreboard.py,
        coût zéro documenté dans wallets.py) : le journal et le shadow log
        s'enrichissent en continu, un wallet pas encore jugeable hier peut
        l'être aujourd'hui.
        """
        if not (self.wallets and self.journal and self.shadow) or not candidates:
            return candidates

        scores = {
            s.wallet: s
            for s in score_wallets(self.wallets, self.journal, self.shadow)
            if s.actionable
        }
        if not scores:
            return candidates

        enriched = []
        for candidate in candidates:
            best = None
            for row in self.wallets.wallets_for(candidate.token_address):
                score = scores.get(row.get("wallet"))
                if score is None:
                    continue
                if best is None or (score.hit_rate or 0) > (best.hit_rate or 0):
                    best = score
            if best is None:
                enriched.append(candidate)
            else:
                enriched.append(candidate.with_fields(wallet_reliability_score=best.hit_rate))
        return enriched

    def _gmgn_activity_kwargs(self) -> dict:
        """Paramètres de filtrage smart money depuis params.json."""
        cfg = self.params.get("gmgn", {})
        kwargs: dict = {}
        if "exclude_wallet_tags" in cfg:
            kwargs["exclude_wallet_tags"] = cfg["exclude_wallet_tags"]
        if "require_wallet_tags" in cfg:
            kwargs["require_wallet_tags"] = cfg["require_wallet_tags"]
        if "min_trade_usd" in cfg:
            kwargs["min_trade_usd"] = cfg["min_trade_usd"]
        if cfg.get("buys_only"):
            kwargs["buys_only"] = True
        return kwargs

    def enrich_social(
        self, candidates: tuple[Candidate, ...], addresses: tuple[str, ...]
    ) -> dict[str, Any]:
        """Mesure sociale des adresses demandées. Ne décide plus de la liste.

        Le quota Twitter est MENSUEL et partagé : l'arbitrage entre stratégies
        se fait chez l'appelant, qui passe ici une liste déjà ordonnée par
        priorité. `TwitterAPI.enrich` applique le plafond par cycle.
        """
        if not (self.twitter and self.twitter.enabled) or not addresses:
            return {}
        by_address = {c.token_address: c for c in candidates}
        worth_it = [by_address[a] for a in addresses if a in by_address]
        if not worth_it:
            return {}
        return self.twitter.enrich(worth_it)

    def _enrich_social(self, candidates: list[Candidate]) -> list[Candidate]:
        """Ancien chemin, conservé pour les appelants existants et les tests."""
        min_alpha = self.params.get("scan.social_lookup_min_alpha", 70)
        addresses = tuple(
            c.token_address for c in candidates if c.alpha_score_absolute >= min_alpha
        )
        stats = self.enrich_social(tuple(candidates), addresses)
        return [self._with_social(c, stats.get(c.token_address)) for c in candidates]

    def _apply_security_filters(
        self, candidates: list[Candidate], filters: dict, label: str = "", verbose: bool = True
    ) -> tuple[list[Candidate], list[Candidate]]:
        """Tri accepté / rejeté. AUCUN effet de bord : voir `apply_cache_effects`."""
        kept: list[Candidate] = []
        rejected: list[Candidate] = []

        for candidate in candidates:
            reason = candidate.rejected_reason or self._rejection_reason(candidate, filters)
            if reason:
                rejected.append(candidate.with_fields(rejected_reason=reason))
                continue
            kept.append(candidate)

        if verbose:
            print(
                f"[Pipeline]{label} {len(kept)} retenus / {len(rejected)} rejetés "
                f"après filtres sécurité"
            )
            for candidate in rejected[:5]:
                print(f"    ❌{label} {candidate.symbol:>10} — {candidate.rejected_reason}")
        return kept, rejected

    def _rejection_reason(self, c: Candidate, f: dict) -> Optional[str]:
        """None = candidat accepté. Les données absentes ne rejettent pas.

        Liquidité, volume et âge sont RÉAPPLIQUÉS ici alors que la découverte
        les filtre déjà. Ce n'est pas redondant : avec plusieurs stratégies,
        l'enveloppe de découverte prend le seuil le plus laxiste, et sans ce
        contrôle une stratégie stricte hériterait silencieusement du seuil
        d'une autre — y compris celui que son propre apprentissage ajuste.
        """
        min_liq = f.get("min_liquidity_usd")
        if min_liq is not None and c.liquidity_usd is not None and c.liquidity_usd < min_liq:
            return f"liquidité {c.liquidity_usd:.0f}$ < {min_liq}$"

        min_vol = f.get("min_volume_1h")
        if min_vol is not None and c.volume_1h is not None and c.volume_1h < min_vol:
            return f"volume 1h {c.volume_1h:.0f}$ < {min_vol}$"

        if c.age_hours is not None:
            min_age = f.get("min_age_hours")
            if min_age is not None and c.age_hours < min_age:
                return f"âge {c.age_hours:.1f}h < {min_age}h"
            max_age = f.get("max_age_hours")
            if max_age is not None and c.age_hours > max_age:
                return f"âge {c.age_hours:.1f}h > {max_age}h"

        if c.rugcheck_score is not None and c.rugcheck_score < f.get("min_rugcheck_score", 70):
            return f"rugcheck {c.rugcheck_score} < {f.get('min_rugcheck_score')}"

        if c.holders is not None and c.holders < f.get("min_holders", 75):
            return f"holders {c.holders} < {f.get('min_holders')}"

        max_conc = f.get("max_top_wallet_concentration", 20)
        if c.top_holder_pct is not None and c.top_holder_pct > max_conc:
            return f"top wallet {c.top_holder_pct:.1f}% > {max_conc}%"

        max_dev = f.get("max_dev_wallet_pct", 10)
        if c.dev_wallet_pct is not None and c.dev_wallet_pct > max_dev:
            return f"dev wallet {c.dev_wallet_pct}% > {max_dev}%"

        # LP non verrouillée = le dev peut retirer la liquidité à tout
        # moment, effondrant le prix en un tick. Récupéré depuis RugCheck
        # depuis le début mais jamais comparé à rien : rugcheck_score,
        # top_holder_pct et dev_wallet_pct filtraient déjà, pas ce vecteur-là
        # — le plus classique du rug pull. Repéré en creusant pourquoi 37%
        # des stops tombent en moins de 5 min (2026-08-17).
        min_lp_locked = f.get("min_lp_locked_pct", 50)
        if c.lp_locked_pct is not None and c.lp_locked_pct < min_lp_locked:
            return f"LP verrouillée {c.lp_locked_pct:.0f}% < {min_lp_locked}%"

        if c.mint_authority_revoked is False:
            return "mint authority active"
        if c.freeze_authority_revoked is False:
            return "freeze authority active"

        min_smart = f.get("min_smart_money_buys_30min", 0)
        if c.smart_money_buys_30m is not None and c.smart_money_buys_30m < min_smart:
            return f"smart money {c.smart_money_buys_30m} < {min_smart}"

        return self._manipulation_reason(c, f) or self._context_reason(c, f)

    @staticmethod
    def _manipulation_reason(c: Candidate, f: dict) -> Optional[str]:
        """Signaux de manipulation, tous rendus par l'appel de découverte.

        Les filtrer ne coûte aucune requête : c'est le meilleur rapport
        qualité/prix du pipeline. Chaque seuil est facultatif — absent du
        document d'un bras, il ne s'applique pas.
        """
        if f.get("reject_wash_trading") and c.is_wash_trading:
            return "wash trading détecté"

        checks_max = (
            ("max_bundler_rate", c.bundler_rate, "bundlers", 100),
            ("max_insider_rate", c.insider_rate, "insiders", 100),
            ("max_entrapment_ratio", c.entrapment_ratio, "entrapment", 100),
            ("max_rug_ratio", c.rug_ratio, "rug ratio", 100),
        )
        for key, value, label, scale in checks_max:
            limit = f.get(key)
            if limit is not None and value is not None and value > limit:
                return f"{label} {value * scale:.0f}% > {limit * scale:.0f}%"

        limit = f.get("max_sniper_count")
        if limit is not None and c.sniper_count is not None and c.sniper_count > limit:
            return f"snipers {c.sniper_count} > {limit}"

        limit = f.get("max_bot_degen_count")
        if limit is not None and c.bot_degen_count is not None and c.bot_degen_count > limit:
            return f"bots degen {c.bot_degen_count} > {limit}"

        floor = f.get("min_renowned_count")
        if floor is not None and c.renowned_count is not None and c.renowned_count < floor:
            return f"KOL {c.renowned_count} < {floor}"

        floor = f.get("min_bluechip_owner_pct")
        if (
            floor is not None
            and c.bluechip_owner_pct is not None
            and c.bluechip_owner_pct < floor
        ):
            return f"bluechip {c.bluechip_owner_pct:.1f}% < {floor}%"

        return None

    @staticmethod
    def _context_reason(c: Candidate, f: dict) -> Optional[str]:
        """Contexte de marché, réputation du créateur, momentum."""
        floor = f.get("min_swaps")
        if floor is not None and c.swaps is not None and c.swaps < floor:
            return f"swaps {c.swaps} < {floor}"

        floor = f.get("min_market_cap")
        if floor is not None and c.market_cap and c.market_cap < floor:
            return f"market cap {c.market_cap:.0f}$ < {floor}$"
        ceiling = f.get("max_market_cap")
        if ceiling is not None and c.market_cap and c.market_cap > ceiling:
            return f"market cap {c.market_cap:.0f}$ > {ceiling}$"

        floor = f.get("min_volume_liquidity_ratio")
        if (
            floor is not None
            and c.volume_liquidity_ratio
            and c.volume_liquidity_ratio < floor
        ):
            return f"volume/liquidité {c.volume_liquidity_ratio:.2f} < {floor}"

        floor = f.get("min_buy_sell_ratio")
        if floor is not None and (c.buys_5m or c.sells_5m) and c.buy_sell_ratio_5m < floor:
            return f"pression acheteuse {c.buy_sell_ratio_5m:.2f} < {floor}"

        # « Le score achète le haut de la bougie » : plafonner la hausse déjà
        # faite est le seul garde-fou direct contre cette hypothèse.
        ceiling = f.get("max_price_change_1h")
        if ceiling is not None and c.price_change_1h is not None and c.price_change_1h > ceiling:
            return f"déjà {c.price_change_1h:+.0f}% sur 1h > {ceiling}%"
        floor = f.get("min_price_change_5m")
        if floor is not None and c.price_change_5m is not None and c.price_change_5m < floor:
            return f"momentum 5m {c.price_change_5m:+.1f}% < {floor}%"

        ceiling = f.get("max_drawdown_from_ath_pct")
        drawdown = c.drawdown_from_ath_pct
        if ceiling is not None and drawdown is not None and drawdown > ceiling:
            return f"{drawdown:.0f}% sous son plus haut > {ceiling}%"

        floor = f.get("min_liquidity_growth_ratio")
        growth = c.liquidity_growth_ratio
        if floor is not None and growth is not None and growth < floor:
            return f"liquidité {growth:.2f}x l'initiale < {floor}x"

        if f.get("require_social") and c.has_social is False:
            return "aucun réseau social"

        ceiling = f.get("max_dev_tokens_launched")
        launched = c.twitter_create_token_count
        if ceiling is not None and launched is not None and launched > ceiling:
            return f"le compte a lancé {launched} tokens > {ceiling}"

        ceiling = f.get("max_twitter_renames")
        renames = c.twitter_rename_count
        if ceiling is not None and renames is not None and renames > ceiling:
            return f"compte X renommé {renames} fois > {ceiling}"

        if f.get("reject_creator_sold") and c.creator_token_status == "creator_sell":
            return "le créateur a vendu"

        if f.get("reject_dexscreener_paid") and c.dexscreener_paid:
            return "promotion DexScreener payée"

        return None
