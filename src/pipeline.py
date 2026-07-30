"""Pipeline de scan : découverte -> enrichissement -> filtres -> scoring."""

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from src.analysis.technical import PriceHistory
from src.apis.birdeye import BirdeyeAPI
from src.apis.dexscreener import DexScreenerAPI
from src.apis.gmgn import GmgnAPI
from src.apis.helius import HeliusAPI
from src.apis.rugcheck import RugCheckAPI
from src.apis.twitter import TwitterAPI
from src.core.cache import RUGPULL_BLACKLIST_HOURS, TokenCache
from src.core.models import Candidate, ScanResult
from src.core.params import ParamsStore
from src.core.scoring import score_candidates

MAX_ENRICH_WORKERS = 5
AUDIT_TTL_SECONDS = 900  # un audit sécurité ne change pas toutes les 90s


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
        self._audit_cache: dict[str, tuple[float, dict]] = {}

    def run_cycle(self) -> ScanResult:
        started = time.time()
        filters = self.params.get("filters", {})
        scan_cfg = self.params.get("scan", {})
        watched = set(self.cache.watched())

        raw: list[Candidate] = []
        skipped_total = 0
        for chain in scan_cfg.get("chains", ["solana"]):
            candidates, skipped = self.dex.scan_new_meme_coins(
                chain=chain,
                min_liquidity=filters.get("min_liquidity_usd", 15000),
                min_volume_1h=filters.get("min_volume_1h", 8000),
                max_age_hours=filters.get("max_age_hours", 6),
                min_age_hours=filters.get("min_age_hours", 0.5),
                always_include=watched,
            )
            raw.extend(candidates)
            skipped_total += skipped

        scanned_count = len(raw)
        raw = self._trim(raw, watched, scan_cfg.get("max_candidates_enriched", 25))

        enriched = self._enrich_smart_money(self._enrich_all(raw, filters))
        kept, rejected = self._apply_security_filters(enriched, filters)

        self.history.record_all(kept)
        self._purge_audit_cache()

        weights = self.params.get("scoring_weights", {})
        # Le social coûte des requêtes : on ne le mesure que sur les meilleurs,
        # donc après un premier classement sans lui.
        scored = self._enrich_social(score_candidates(kept, weights))

        min_social = filters.get("min_social_mentions_1h", 0)
        social_kept = []
        for candidate in scored:
            if (
                candidate.social_mentions_1h is not None
                and candidate.social_mentions_1h < min_social
            ):
                reason = f"mentions sociales {candidate.social_mentions_1h} < {min_social}"
                rejected.append(candidate.with_fields(rejected_reason=reason))
                self.cache.unwatch(candidate.token_address)
                print(f"    ❌ {candidate.symbol:>10} — {reason}")
                continue
            social_kept.append(candidate)
        scored = score_candidates(social_kept, weights)

        return ScanResult(
            candidates=tuple(scored),
            rejected=tuple(rejected),
            scanned_count=scanned_count,
            cached_skipped=skipped_total,
            duration_sec=round(time.time() - started, 2),
        )

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

        activity = self.gmgn.activity_by_token()
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
                )
            )
        if touched:
            print(f"[GMGN] {touched}/{len(candidates)} candidats avec activité smart money")
        return enriched

    def _enrich_social(self, candidates: list[Candidate]) -> list[Candidate]:
        """Social pour les meilleurs candidats (quota Twitter géré en interne)."""
        if not (self.twitter and self.twitter.enabled) or not candidates:
            return candidates

        # Le quota Twitter est MENSUEL : on ne le dépense que sur les
        # candidats susceptibles d'être achetés, pas sur tout le lot.
        min_alpha = self.params.get("scan.social_lookup_min_alpha", 70)
        worth_it = [c for c in candidates if c.alpha_score_absolute >= min_alpha]
        if not worth_it:
            return candidates

        stats_by_token = self.twitter.enrich(worth_it)
        enriched = []
        for candidate in candidates:
            stats = stats_by_token.get(candidate.token_address)
            if stats is None:
                enriched.append(candidate)
                continue
            enriched.append(
                candidate.with_fields(
                    social_mentions_1h=stats.mentions_1h,
                    social_unique_authors=stats.unique_authors,
                    social_engagement=stats.engagement,
                    social_velocity_15m=stats.velocity_15m,
                    social_sample_size=stats.sample_size,
                )
            )
        return enriched

    def _apply_security_filters(
        self, candidates: list[Candidate], filters: dict
    ) -> tuple[list[Candidate], list[Candidate]]:
        kept: list[Candidate] = []
        rejected: list[Candidate] = []

        for candidate in candidates:
            reason = candidate.rejected_reason or self._rejection_reason(candidate, filters)
            if reason:
                rejected.append(candidate.with_fields(rejected_reason=reason))
                self.cache.unwatch(candidate.token_address)
                if "risque critique" in reason:
                    self.cache.blacklist(
                        candidate.token_address, reason, RUGPULL_BLACKLIST_HOURS
                    )
                continue
            # Token qualifié : sous surveillance -> rafraîchi à chaque cycle
            # pour alimenter l'historique de prix de l'analyse technique.
            self.cache.watch(candidate.token_address)
            kept.append(candidate)

        print(f"[Pipeline] {len(kept)} retenus / {len(rejected)} rejetés après filtres sécurité")
        for candidate in rejected[:5]:
            print(f"    ❌ {candidate.symbol:>10} — {candidate.rejected_reason}")
        return kept, rejected

    def _rejection_reason(self, c: Candidate, f: dict) -> Optional[str]:
        """None = candidat accepté. Les données absentes ne rejettent pas."""
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

        if c.mint_authority_revoked is False:
            return "mint authority active"
        if c.freeze_authority_revoked is False:
            return "freeze authority active"

        min_smart = f.get("min_smart_money_buys_30min", 0)
        if c.smart_money_buys_30m is not None and c.smart_money_buys_30m < min_smart:
            return f"smart money {c.smart_money_buys_30m} < {min_smart}"

        return None
