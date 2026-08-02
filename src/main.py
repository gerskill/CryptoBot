"""MemeCoin Alpha Loop V2 — boucle principale.

Boucle complète en PAPER : scan -> scoring -> analyse technique -> ouverture
de position simulée -> monitoring SL/TP/trailing -> journal -> apprentissage.

AUCUNE TRANSACTION RÉELLE. Le passage en LIVE demande un module d'exécution
(swap Jupiter, signature, wallet) qui n'existe pas ici, et il est verrouillé
par `LearningEngine.live_mode_allowed()` : 20 trades, WR > 40%, PF > 1.5.

Lancement : python -m src.main
Arrêt     : Ctrl-C, ou /STOP depuis Telegram (ferme toutes les positions).
"""

import signal
import time
from datetime import datetime, timezone
from typing import Optional

from src import settings
from src.analysis.technical import (
    PriceHistory,
    TechnicalVerdict,
    analyze,
    from_gmgn_kline,
    from_ohlcv,
)
from src.apis.birdeye import BirdeyeAPI
from src.apis.dexscreener import DexScreenerAPI
from src.apis.gmgn import GmgnAPI
from src.apis.helius import HeliusAPI
from src.apis.rugcheck import RugCheckAPI
from src.apis.twitter import TwitterAPI
from src.agents import (
    CounterfactualTimingAgent,
    DevHistoryAgent,
    MicrostructureAgent,
    RSIAgent,
    VolatilityAgent,
)
from src.core.arm import CONSENSUS, attach_portfolios, bootstrap_arms
from src.apis.jupiter import SOL_MINT, JupiterAPI
from src.core import economics
from src.core.budget import load_budgets
from src.core.capabilities import build_registry
from src.core.economics import size_for_cost
from src.core.cache import TokenCache
from src.core.confluence import (
    arm_signals,
    build_confluence,
    merge_social_wishlists,
    top_confluence,
)
from src.core.funnel import FunnelRecorder
from src.core.signals import aggregate
from src.core.journal import TradeJournal
from src.core.learning import LearningEngine
from src.core.lock import InstanceLock
from src.core.models import Candidate, ScanResult
from src.core.params import ParamsStore
from src.core.positions import RUG_LIQUIDITY_DROP_PCT
from src.core.pricefeed import (
    MONITOR_BUDGET_PER_MIN,
    CycleMarketCache,
    effective_monitor_interval,
)
from src.core.shadow import ShadowTracker
from src.core.state import StateWriter, candidate_to_dict, position_to_dict
from src.core.wallets import WalletRegistry
from src.notify.telegram import TelegramNotifier
from src.pipeline import ScanPipeline, discovery_envelope

PAPER_DEFAULT_CAPITAL = 1000.0
DASHBOARD_EVERY_SECONDS = 300
SUMMARY_EVERY_SECONDS = 4 * 3600
# Délai avant le TOUT PREMIER digest après un démarrage. Assez court pour voir
# rapidement ce que font les stratégies muettes, assez long pour que les bras
# aient eu le temps d'évaluer quelques cycles.
FIRST_SUMMARY_DELAY = 15 * 60
# Messages conservés par bras dans un digest. 5 était trop bas depuis que les
# bras tradent : à 8-32 trades par jour et par bras, une fenêtre de 4 h en
# accumule facilement le double.
DIGEST_MAX_LINES = 12
RUG_WINDOW_SECONDS = 120
# Seuil d'OBSERVATION, pas de sortie : on journalise les chutes bien avant le
# seuil de rug pour pouvoir juger après coup si -50% laisse partir trop tard.
LIQUIDITY_ALERT_PCT = -25.0
# Proportion de OUI exigée parmi les bras qui ont un AVIS. Une proportion et
# non un compte : « 2 sur 5 » se casse dès qu'un bras s'abstient, « 60% des
# présents » survit à des fenêtres disjointes en gardant la même exigence.
CONSENSUS_MIN_RATIO = 0.6
# PALIERS DE GAIN LATENT annoncés en direct, une fois par position.
#
# Tirés des taux d'atteinte mesurés sur 26 positions instrumentées : +50 % est
# franchi par 23 % des trades, +100 % par 15 %, +150 % par 4 %. Ce sont donc
# des événements du quart supérieur, pas des étapes de routine. Le pic médian
# étant +4,9 %, un palier plus bas sonnerait à chaque trade.
GAIN_MILESTONES = (50.0, 100.0, 150.0)
# Cadence du contrôle d'inactivité, en cycles. Il faut `INACTIVITY_CYCLES` =
# 300 cycles sans entrée pour qu'un relâchement se déclenche : contrôler tous
# les 50 cycles (~1 h 15 à 90 s) ne peut donc rien manquer, et évite de lire la
# queue de l'entonnoir pour 7 bras à chaque tour.
INACTIVITY_CHECK_EVERY = 50

_running = True


def _handle_sigint(*_: object) -> None:
    global _running
    print("\n[Loop] Arrêt demandé — fin du cycle en cours...")
    _running = False


class AlphaLoop:
    def __init__(self) -> None:
        self.params = ParamsStore(settings.PARAMS_PATH)
        self.cache = TokenCache(
            settings.CACHE_PATH, ttl_minutes=self.params.get("scan.cache_ttl_minutes", 30)
        )
        self.history = PriceHistory()
        self.dex = DexScreenerAPI(cache=self.cache)
        self.birdeye = BirdeyeAPI(settings.BIRDEYE_API_KEY)
        self.gmgn = GmgnAPI(chain="sol")
        # Le seul module qui sait ce qu'une taille coûte VRAIMENT : DexScreener
        # et Birdeye rendent un prix moyen, un devis rend le prix exécutable.
        self.jupiter = JupiterAPI(settings.JUPITER_API_KEY)

        # AGENTS DE MESURE. Ils calculent et journalisent ; aucun n'écrit de
        # paramètre ni ne refuse une entrée. Avec 93 trades clôturés, brancher
        # une décision sur un indicateur neuf serait du surapprentissage — ils
        # produisent d'abord l'échantillon que la couche d'apprentissage lira.
        self.dev_history = DevHistoryAgent(log_path=settings.DEV_HISTORY_LOG_PATH)
        self.rsi = RSIAgent(log_path=settings.RSI_LOG_PATH)
        self.volatility = VolatilityAgent(log_path=settings.VOLATILITY_LOG_PATH)
        self.microstructure = MicrostructureAgent(
            jupiter=self.jupiter, log_path=settings.MICROSTRUCTURE_LOG_PATH
        )
        self.counterfactual = CounterfactualTimingAgent(
            log_path=settings.COUNTERFACTUAL_LOG_PATH
        )
        # Santé par CAPACITÉ, pas par API : ce qui compte n'est pas « Birdeye
        # est mort » mais « peut-on encore obtenir des bougies ». Publié dans
        # state.json pour que la dégradation soit visible au lieu d'être subie.
        self.capabilities = build_registry(
            birdeye=self.birdeye, gmgn=self.gmgn, jupiter=self.jupiter, dex=self.dex,
            helius=HeliusAPI(settings.HELIUS_API_KEY),
        )
        self.budgets = load_budgets(
            settings.BUDGET_PATH,
            {k: v for k, v in (self.params.get("api_budgets", {}) or {}).items()
             if isinstance(v, int)},
        )
        self.pipeline = ScanPipeline(
            params=self.params,
            cache=self.cache,
            dex=self.dex,
            helius=HeliusAPI(settings.HELIUS_API_KEY),
            rugcheck=RugCheckAPI(),
            birdeye=self.birdeye,
            twitter=TwitterAPI(
                settings.TWITTER_BEARER_TOKEN,
                max_lookups_per_cycle=self.params.get("scan.social_max_lookups_per_cycle", 2),
                budget=self.budgets.get("twitter"),
            ),
            gmgn=self.gmgn,
            history=self.history,
        )

        self.journal = TradeJournal(settings.TRADES_LOG_PATH)
        # Remplacé plus bas par celui du bras témoin, comme `learning` : les
        # deux instances viseraient le MÊME fichier (`arm_paths("baseline")`
        # rend `SHADOW_LOG_PATH`) avec deux `_tracked` séparés, donc deux
        # verdicts écrits pour un seul rejet.
        self.shadow: Optional[ShadowTracker] = None
        # Étape 1 du suivi de wallets : OBSERVER, sans jamais décider. Coût
        # nul — le flux smart money est déjà récupéré pour le scoring.
        self.wallets = WalletRegistry(settings.WALLETS_LOG_PATH)
        # « Pourquoi ce bras ne trade pas ? » : marché vide, seuil trop
        # strict, technique qui refuse, ou carnet trop mince ? Sans trace par
        # porte, ces quatre causes sont indiscernables et appellent des
        # corrections opposées.
        self.funnel = FunnelRecorder(settings.FUNNEL_LOG_PATH)
        self.state = StateWriter(settings.STATE_PATH)
        self.telegram = TelegramNotifier(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)
        self.telegram.drain_pending()
        # Remplacé plus bas par celui du bras témoin : deux LearningEngine sur
        # le même journal écriraient deux historiques d'ajustement.
        self.learning: Optional[LearningEngine] = None

        self.mode = self.params.get("mode", "PAPER")
        cooldown = self.params.get("risk_rules.cooldown_hours", 2)
        if self.mode == "LIVE" and cooldown <= 0:
            print(
                "⚠️ Mode LIVE avec cooldown désactivé — après 3 pertes consécutives "
                "le bot continuera d'ouvrir des positions. Remets "
                "risk_rules.cooldown_hours à 2 dans params.json."
            )

        # Chaque stratégie a SON portefeuille, SON journal, SA part de capital.
        # Elles jugent toutes le même lot enrichi — la collecte est partagée,
        # les décisions ne le sont pas.
        self.arms = bootstrap_arms(base_params=self.params)
        self.baseline = next(arm for arm in self.arms if arm.is_baseline)
        self.voters = {arm.name for arm in self.arms if arm.is_voter}
        attach_portfolios(
            self.arms,
            capital_total=self._starting_capital(),
            notifier=self.telegram,
            mode=self.mode,
            cooldown_hours=cooldown,
            losses_trigger=self.params.get("risk_rules.consecutive_losses_trigger", 3),
            max_drawdown_stop_pct=self.params.get(
                "risk_rules.max_drawdown_stop_pct", 0.0
            ),
        )
        # `self.portfolio` reste celui du témoin : le dashboard, l'API et les
        # 36 trades historiques continuent de le lire sans migration.
        self.portfolio = self.baseline.portfolio
        self.learning = self.baseline.learning
        self.shadow = self.baseline.shadow
        self.market = CycleMarketCache()

        self.cycle_count = 0
        self.paused = False
        self._last_prices: dict[str, float] = {}
        self._next_scan_at = 0.0
        self._last_scan: Optional[ScanResult] = None
        self._last_evaluations: dict = {}
        self._last_confluence: dict = {}
        self._last_dashboard = 0.0
        # PREMIER DIGEST PEU APRÈS LE DÉMARRAGE, pas quatre heures après.
        # `time.time()` remettait le compteur à zéro à chaque lancement : avec
        # plusieurs redémarrages dans la journée, le digest des stratégies
        # silencieuses n'était jamais parti une seule fois.
        self._last_summary = time.time() - SUMMARY_EVERY_SECONDS + FIRST_SUMMARY_DELAY
        # Paliers de gain déjà annoncés, par position. Volontairement hors de
        # `Position`, qui est immuable et persistée : voir `_announce_milestones`.
        self._milestones_sent: dict[str, set[float]] = {}

    def _starting_capital(self) -> float:
        capital = float(self.params.get("capital_total", 0) or 0)
        if capital > 0:
            return capital
        print(
            f"⚠️ capital_total = 0 dans params.json — capital papier fictif de "
            f"{PAPER_DEFAULT_CAPITAL:.0f} $ utilisé pour que la simulation tourne."
        )
        return PAPER_DEFAULT_CAPITAL

    def run(self) -> None:
        interval = self.params.get("scan.interval_seconds", 90)
        self._print_banner(interval)
        self.telegram.send(f"🤖 <b>Alpha Loop démarré</b> — mode {self.mode}")

        while _running:
            started = time.monotonic()
            self.cycle_count += 1
            try:
                self._cycle()
            except Exception as exc:  # la boucle ne meurt jamais sur un cycle
                print(f"[Loop] ⚠️ Erreur cycle {self.cycle_count} : {type(exc).__name__} — {exc}")

            elapsed = time.monotonic() - started
            if elapsed > interval:
                print(f"[Loop] ⏱️ Cycle {elapsed:.0f}s > {interval}s — enchaînement direct")
            self._sleep_monitoring(max(0.0, interval - elapsed))

        self.cache.save()
        stats = self.portfolio.stats()
        print(
            f"[Loop] {self.cycle_count} cycles | {stats['total_trades']} trades | "
            f"P&L {stats['total_pnl_usd']:+.2f} $"
        )

    def _sleep_monitoring(self, duration: float) -> None:
        """Entre deux scans, surveille les positions à cadence rapprochée.

        Découplage volontaire : la découverte n'a rien à gagner à tourner plus
        vite que le rafraîchissement de DexScreener (~30-60s), mais un stop
        loss échantillonné toutes les 90s sort très loin sous son seuil sur un
        memecoin. Coût : 1 requête par position et par tick, sur un quota de
        300/min — négligeable.
        """
        base = self.params.get("scan.monitor_interval_seconds", 20)
        # Garde EXPLICITE : le limiteur DexScreener bloque au lieu d'échouer,
        # donc un dépassement ne se voit nulle part — il ressort plus tard en
        # stops qui sortent trop bas. Mieux vaut allonger le tick sciemment.
        pairs = len({
            self.market.key(p.chain, p.pair_address, p.token_address)
            for p in self._open_positions()
        })
        monitor_interval = effective_monitor_interval(base, pairs)
        if monitor_interval > base:
            print(
                f"[Monitor] {pairs} paires — tick allongé à {monitor_interval}s "
                f"(budget {MONITOR_BUDGET_PER_MIN} req/min)"
            )
        deadline = time.monotonic() + duration
        next_monitor = time.monotonic() + monitor_interval

        while _running and time.monotonic() < deadline:
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
            if self._open_positions() and time.monotonic() >= next_monitor:
                try:
                    self._monitor_positions(verbose=False)
                    self._publish_state()
                except Exception as exc:  # ne jamais casser la boucle
                    print(f"[Monitor] ⚠️ {type(exc).__name__} — {exc}")
                next_monitor = time.monotonic() + monitor_interval

    def _cycle(self) -> None:
        # Horodater la prochaine passe dès maintenant : `_publish_state` est
        # appelé en fin de cycle, avant `_sleep_monitoring`, et publierait
        # sinon l'échéance périmée du cycle précédent (compte à rebours figé).
        self._next_scan_at = time.time() + self.params.get("scan.interval_seconds", 90)
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"\n{'=' * 78}\n⏱️  CYCLE {self.cycle_count} — {timestamp} UTC\n{'=' * 78}")

        self._handle_commands()
        # Le monitoring passe AVANT le scan : une position en cours prime
        # toujours sur la recherche d'une nouvelle entrée.
        self._monitor_positions()

        self.market.reset_cycle()
        result = self._scan_all_arms()
        self._print_result(result)
        self._track_rejections(result)

        if not self.paused:
            # Ordre du manifeste, consensus en DERNIER : il lit le décompte
            # des votes, qui n'est complet qu'une fois les votants passés.
            for arm in self.arms:
                evaluation = self._last_evaluations.get(arm.name)
                if evaluation is not None:
                    self.funnel.record_evaluation(
                        arm.name, evaluation, arm.entry_threshold
                    )
                    self._try_entries(evaluation, arm, self._last_confluence)
        self.funnel.flush()

        # APRÈS le flush : le relâchement lit l'entonnoir, y compris les lignes
        # de ce cycle-ci.
        self._relax_inactive_arms()
        self.cache.purge()
        self._publish_state(result)
        self._periodic_reports()

    def _relax_inactive_arms(self) -> None:
        """Desserre les bras qui n'entrent plus, depuis le CYCLE.

        POURQUOI PAS DEPUIS `_after_trade_closed` COMME LE RESTE. `learning.run`
        n'est appelé qu'à la fermeture d'une position. Un bras qui n'ouvre
        jamais rien n'en ferme jamais, donc `run` n'est jamais appelé, donc le
        relâchement sur inactivité serait inatteignable **exactement pour les
        bras qu'il doit débloquer**. `narrative` : 0 entrée sur 927 cycles.

        C'est la même poule-et-œuf que celles corrigées dans `economics` et
        dans la porte des 15 trades, un cran plus haut : cette fois ce n'est
        pas le seuil qui bloque, c'est le point d'appel.

        Cadencé, pas à chaque cycle : la mesure lit la queue de l'entonnoir
        pour chacun des 7 bras. À 90 s par cycle, `INACTIVITY_CHECK_EVERY`
        cadence le contrôle à un peu plus d'une heure — très en dessous des
        `INACTIVITY_CYCLES` = 300 cycles qu'il faut pour déclencher, donc aucun
        déclenchement n'est manqué.
        """
        if self.paused or self.cycle_count % INACTIVITY_CHECK_EVERY != 0:
            return
        for arm in self.arms:
            if arm.learning is None:
                continue
            for change in arm.learning._relax_from_inactivity():
                print(f"🧠{arm.label} {change}")

    def _scan_all_arms(self) -> ScanResult:
        """Une collecte, N évaluations. Retourne celle du bras témoin.

        Le coût API est entièrement dans `collect()` ; `evaluate()` est du CPU
        pur sur au plus 25 candidats, donc ajouter un bras ne coûte rien en
        requêtes. Le social est arbitré une seule fois entre les listes de
        souhaits : le plafond par cycle et le budget mensuel restent globaux.
        """
        envelope = discovery_envelope([arm.filters() for arm in self.arms])
        batch = self.pipeline.collect(envelope)
        self._observe_wallets(batch)

        pre = {
            arm.name: self.pipeline.evaluate(batch, params=arm.params, label=arm.label)
            for arm in self.arms
        }
        addresses = merge_social_wishlists(
            {name: evaluation.wishlist for name, evaluation in pre.items()},
            limit=self.params.get("scan.social_max_lookups_per_cycle", 2),
        )
        social = self.pipeline.enrich_social(batch.enriched, addresses)

        evaluations = {
            arm.name: self.pipeline.evaluate(
                batch,
                params=arm.params,
                social=social,
                label=arm.label,
                verbose=arm.is_baseline,
            )
            for arm in self.arms
        }

        # Effets de cache sur l'UNION : surveillé dès qu'un bras le garde,
        # dé-surveillé seulement si aucun ne le garde. Sans ça le dernier bras
        # évalué sortirait de la watchlist un token qu'un autre détient, et
        # son historique de prix cesserait de se remplir.
        kept: set[str] = set()
        rejected: set[str] = set()
        for evaluation in evaluations.values():
            kept |= evaluation.kept_addresses
            rejected |= evaluation.rejected_addresses
        self.pipeline.apply_cache_effects(kept, rejected, batch.blacklist)

        self._last_evaluations = evaluations
        self._last_confluence = build_confluence(
            evaluations,
            voters=self.voters,
            thresholds={arm.name: arm.entry_threshold for arm in self.arms},
        )
        self._print_arms(evaluations, pre)
        return evaluations[self.baseline.name].result

    def _observe_wallets(self, batch) -> None:
        """Qui a acheté ces tokens, et combien de temps avant nous.

        Aucun appel réseau : `fetch_smart_money_trades` est mémoïsé et vient
        d'être consommé par l'enrichissement. On relit le même instantané.

        Le premier passage d'un token fixe `bot_first_seen` — la référence de
        l'avance. Il doit être posé AVANT d'observer, sinon un wallet qui
        achète pendant notre propre cycle paraîtrait en avance de zéro.
        """
        if not (self.gmgn and self.gmgn.enabled) or not batch.enriched:
            return
        try:
            addresses = {c.token_address for c in batch.enriched}
            self.wallets.mark_first_seen(addresses)

            added = self.wallets.observe(
                self.gmgn.fetch_smart_money_trades(),
                first_seen_by_token=self.wallets.first_seen,
                known_tokens=addresses,
            )
            if added:
                stats = self.wallets.stats
                lead = stats["median_lead_minutes"]
                avance = f" | avance médiane {lead:+.0f} min" if lead is not None else ""
                print(
                    f"[Wallets] +{added} observations | {stats['wallets']} wallets "
                    f"({stats['profiled']} profilés){avance}"
                )
        except Exception as exc:  # l'observation ne doit jamais casser la boucle
            print(f"[Wallets] ⚠️ {type(exc).__name__} — {exc}")

    def _print_arms(self, evaluations: dict, pre: dict) -> None:
        """Ce que chaque bras aurait pris, et sur quoi les bras s'accordent."""
        if len(self.arms) < 2:
            return
        print("\n🧬 BRAS")
        for arm in self.arms:
            evaluation = evaluations[arm.name]
            qualified = [
                c for c in evaluation.result.candidates
                if c.alpha_score_absolute >= arm.entry_threshold
            ]
            wanted = len(pre[arm.name].wishlist)
            served = evaluation.social_served
            social = f" | social {served}/{wanted}" if wanted else ""
            print(
                f"  {arm.name:>10} | {len(evaluation.result.candidates):>2} retenus "
                f"| {len(qualified):>2} ≥ seuil {arm.entry_threshold:g}{social}"
            )

        agreed = [row for row in self._last_confluence.values() if row.above_threshold_count >= 2]
        if agreed:
            for row in sorted(agreed, key=lambda r: -r.above_threshold_count)[:5]:
                print(
                    f"  🤝 {row.symbol:>10} — {row.above_threshold_count} bras d'accord "
                    f"({', '.join(row.above_threshold_by)})"
                )
        else:
            print("  🤝 aucune confluence ce cycle")

    def _publish_state(self, result: Optional[ScanResult] = None) -> None:
        """Écrit l'état courant pour le dashboard. Ne doit jamais casser la boucle."""
        try:
            if result is not None:
                self._last_scan = result
            scan = self._last_scan

            # TOUTES les positions, étiquetées par bras. Ne publier que celles
            # du témoin affichait « 1 position » alors que deux stratégies en
            # détenaient une chacune : le panneau mentait sur l'exposition
            # réelle, qui est justement ce qu'un tableau de bord doit montrer.
            positions = []
            for arm in self.arms:
                for position in arm.portfolio.positions.values():
                    price = self._last_prices.get(position.token_address)
                    payload = position_to_dict(position, price)
                    payload["arm"] = arm.name
                    positions.append(payload)

            self.state.write(
                {
                    "mode": self.mode,
                    "paused": self.paused,
                    "cycle": self.cycle_count,
                    "interval_seconds": self.params.get("scan.interval_seconds", 90),
                    "next_scan_at": self._next_scan_at,
                    # `stats` reste celui du TÉMOIN : l'API, les 36 trades
                    # historiques et la courbe d'équité s'y appuient.
                    # `aggregate` donne la vue d'ensemble sans casser ça.
                    "stats": self.portfolio.stats(),
                    "aggregate": self._aggregate_stats(),
                    "positions": positions,
                    "candidates": [
                        candidate_to_dict(c) for c in (scan.candidates if scan else ())
                    ],
                    "rejected": [candidate_to_dict(c) for c in (scan.rejected if scan else ())],
                    "scan": {
                        "scanned": scan.scanned_count if scan else 0,
                        "cached_skipped": scan.cached_skipped if scan else 0,
                        "duration_sec": scan.duration_sec if scan else 0,
                    },
                    "params": self.params.data,
                    "shadow": self.shadow.stats,
                    "budgets": {n: b.stats for n, b in self.budgets.items()},
                    "shadow_by_family": self.shadow.missed_rate_by_family(min_sample=1),
                    "entry_threshold": self.params.get("scan.alpha_score_entry_threshold", 75),
                    "live_allowed": self.learning.live_mode_allowed(),
                    # Additif : les clés ci-dessus restent celles du bras
                    # témoin, le dashboard existant ne bouge pas. Pas de
                    # listes de candidats par bras ici — `state.json` est
                    # resérialisé sur le WebSocket toutes les secondes.
                    "arms": self._arms_payload(),
                    "confluence": top_confluence(self._last_confluence),
                    # Dégradation VISIBLE. Deux sources sont mortes en cours
                    # de route sans que rien ne le dise ; le dashboard doit
                    # montrer sur quoi le bot tourne réellement.
                    "capabilities": self.capabilities.report,
                    "blind_spots": self.capabilities.blind_spots,
                    # État RÉEL au moment du cycle, pas juste « la clé
                    # existe » : Twitter se coupe tout seul sur un 402, le
                    # dashboard doit le refléter au lieu d'afficher « actif ».
                    "api_status": {
                        **settings.key_status(),
                        "twitter": self.pipeline.twitter.enabled
                        if self.pipeline.twitter
                        else False,
                        "birdeye": self.birdeye.enabled,
                        "gmgn": self.gmgn.enabled,
                    },
                }
            )
        except Exception as exc:  # le dashboard ne doit jamais faire tomber le bot
            print(f"[State] ⚠️ écriture impossible : {type(exc).__name__} — {exc}")

    def _aggregate_stats(self) -> dict:
        """Vue d'ensemble des 7 stratégies. Complète `stats`, ne le remplace pas.

        L'exposition réelle est la somme des bras, pas celle du témoin. Le
        drawdown agrégé sera PIRE que celui de chaque bras isolé : plusieurs
        peuvent détenir le même token, donc la diversification est moindre
        que ne le suggère le nombre de stratégies.
        """
        stats = [arm.portfolio.stats() for arm in self.arms]
        engages = [
            p.size_usd * p.remaining_fraction
            for arm in self.arms
            for p in arm.portfolio.positions.values()
        ]

        # WIN RATE ET PROFIT FACTOR DE LA FLOTTE, recalculés sur les journaux
        # et non moyennés depuis les bras : une moyenne de taux pondère chaque
        # stratégie à égalité quel que soit son nombre de trades, ce qui
        # donnerait le même poids aux 2 trades de `quality` qu'aux 39 du
        # témoin.
        #
        # POURQUOI CE BLOC EXISTE. Le panneau n'affichait que `stats`, c'est-à-
        # dire le TÉMOIN — gelé, sans trade depuis 16 h. Il montrait « 4
        # gagnants sur 39 » pendant que la flotte en comptait 27 sur 93. Les
        # gains étaient bien enregistrés ; c'est la lecture qui regardait le
        # mauvais portefeuille.
        positions = [
            row
            for arm in self.arms
            if arm.journal is not None
            for row in arm.journal.read_positions()
        ]
        gagnants = [r for r in positions if (r.get("pnl_usd") or 0) > 0]
        gains = sum(r.get("pnl_usd") or 0 for r in gagnants)
        pertes = abs(
            sum(r.get("pnl_usd") or 0 for r in positions if (r.get("pnl_usd") or 0) <= 0)
        )

        return {
            "arms": len(self.arms),
            "open_positions": sum(s["open_positions"] for s in stats),
            "exposure_usd": round(sum(engages), 2),
            "total_trades": sum(s["total_trades"] for s in stats),
            "total_pnl_usd": round(sum(s["total_pnl_usd"] for s in stats), 2),
            "equity": round(sum(s["equity"] for s in stats), 2),
            "arms_trading": sum(1 for s in stats if s["open_positions"]),
            "wins": len(gagnants),
            "closed_trades": len(positions),
            "win_rate": (
                round(100 * len(gagnants) / len(positions), 1) if positions else 0.0
            ),
            "profit_factor": round(gains / pertes, 2) if pertes else 0.0,
            # Le drawdown ne s'additionne pas : on rend le PIRE des bras, et on
            # le dit dans le libellé plutôt que d'inventer une somme.
            "worst_arm_drawdown_pct": (
                max((s["max_drawdown_pct"] for s in stats), default=0.0)
            ),
        }

    def _arms_payload(self) -> list[dict]:
        """Résumé par bras pour le dashboard. Compteurs, jamais les candidats.

        Chaque bras porte son VERDICT contre le témoin. Sans lui, le tableau
        classe sept stratégies par P&L et le lecteur retient la première —
        alors qu'à 2 ou 5 trades ce classement est du bruit.
        """
        from src.core.stats import verdict_vs_reference

        reference = (
            self.baseline.journal.read_positions()
            if self.baseline.journal is not None else []
        )
        payload = []
        for arm in self.arms:
            row = arm.summary()
            evaluation = self._last_evaluations.get(arm.name)
            if evaluation is not None:
                qualified = [
                    c for c in evaluation.result.candidates
                    if c.alpha_score_absolute >= arm.entry_threshold
                ]
                row.update(
                    kept=len(evaluation.result.candidates),
                    rejected=len(evaluation.result.rejected),
                    qualified=len(qualified),
                    social_served=evaluation.social_served,
                )
            if arm.portfolio is not None:
                row["stats"] = arm.portfolio.stats()
            elif arm.journal is not None:
                # En observation : le bras n'a pas de portefeuille, mais son
                # journal peut déjà exister (redémarrage après la phase 4).
                row["trades"] = len(arm.journal.read_positions())
            if arm.journal is not None and not arm.is_baseline:
                row["vs_baseline"] = verdict_vs_reference(
                    arm.journal.read_positions(), reference
                )
            payload.append(row)
        return payload

    def _track_rejections(self, result: ScanResult) -> None:
        """Met les rejets sous observation et juge ceux arrivés à terme.

        Sans ça, le bot n'apprend que de ses propres trades et ne peut jamais
        découvrir qu'un filtre est trop strict.

        CHAQUE BRAS SUIT SES PROPRES REJETS. `bootstrap_arms` donne un
        `ShadowTracker` à chacun et le passe à son `LearningEngine`, mais seul
        le témoin était alimenté : les six autres logs restaient vides, donc
        `_relax_from_shadow` — le seul contrepoids au resserrage — ne rendait
        jamais rien pour eux. Un bras rejette sur SES seuils ; le shadow du
        témoin ne dit rien de ce que `quality` ou `narrative` ont écarté.

        LE PRIX EST RAFRAÎCHI UNE FOIS, sur l'UNION des adresses en attente.
        Les bras jugent le même lot, donc leurs listes se recouvrent largement.
        Interroger DexScreener par bras multiplierait par sept un coût que la
        collecte partagée existe justement pour éviter.
        """
        ajouts: dict[str, int] = {}
        for arm in self.arms:
            evaluation = self._last_evaluations.get(arm.name)
            rejected = (
                evaluation.result.rejected if evaluation is not None
                else (result.rejected if arm.is_baseline else [])
            )
            ajouts[arm.name] = arm.shadow.record_rejections(rejected)

        attente = {a for arm in self.arms for a in arm.shadow.pending_review()}
        if attente:
            for address, pairs in self.dex.get_tokens_data(sorted(attente)).items():
                if not pairs:
                    continue
                price = float(pairs[0].get("priceUsd") or 0)
                for arm in self.arms:
                    arm.shadow.update_price(address, price)

        for arm in self.arms:
            for verdict in arm.shadow.expire():
                icon = "💸" if verdict.would_have_won else "  "
                print(
                    f"{icon} SHADOW{arm.label} {verdict.symbol} rejeté "
                    f"({verdict.reason_family}) → pic {verdict.peak_gain_pct:+.0f}% "
                    f"en {verdict.minutes_tracked:.0f} min"
                )

        if any(ajouts.values()) or attente:
            stats = self.shadow.stats
            detail = " ".join(
                f"{arm.name}+{ajouts[arm.name]}"
                for arm in self.arms
                if ajouts[arm.name]
            )
            print(
                f"[Shadow] {stats['tracked']} suivis | {stats['judged']} jugés "
                f"(témoin) | {detail or 'aucun nouveau rejet'}"
            )

    def _handle_commands(self) -> None:
        for command in self.telegram.poll_commands():
            if command == "/STOP":
                self._panic_exit()
            elif command == "/RESUME":
                self.paused = False
                self.telegram.send("▶️ Reprise des entrées.")
            elif command == "/STATUS":
                self.telegram.send_summary(self.portfolio.stats())

    def _panic_exit(self) -> None:
        """Panic button : ferme TOUT, sur toutes les stratégies, puis veille.

        `/STOP` veut dire stop, partout. Les prix passent par le cache du
        tick : neuf positions à fermer coûtent neuf requêtes, pas neuf par
        stratégie.
        """
        print("🛑 PANIC EXIT — fermeture de toutes les positions")
        self.telegram.send("🛑 <b>/STOP reçu</b> — fermeture de toutes les positions.")
        self.market.reset()
        for arm in self.arms:
            for position_id in list(arm.portfolio.positions):
                position = arm.portfolio.positions[position_id]
                price = self.market.price(position.token_address) or self._current_price(
                    position.token_address, position.chain, position.pair_address
                )
                if price is None:
                    print(
                        f"  ⚠️{arm.label} {position.symbol} : prix indisponible, "
                        f"position NON fermée"
                    )
                    continue
                self.market.put(
                    position.chain, position.pair_address, position.token_address,
                    price, None,
                )
                row = arm.portfolio.force_close(position_id, price, "PANIC_STOP")
                if row:
                    print(f"  💰{arm.label} {position.symbol} fermé à {row['pnl_pct']:+.1f}%")
                    self._after_trade_closed(arm)
        self.paused = True

    def _open_positions(self) -> list:
        return [p for arm in self.arms for p in arm.portfolio.positions.values()]

    def _monitor_positions(self, verbose: bool = True) -> None:
        """Un prix par PAIRE distincte, partagé par toutes les stratégies.

        Sept bras sur le même token demandaient sept fois le même prix par
        tick. Pire, ils empilaient sept snapshots identiques dans la deque que
        lit le détecteur de rug : la fenêtre de détection en sortait faussée.
        """
        positions = self._open_positions()
        if not positions:
            return

        self.market.reset()
        for position in positions:
            key = self.market.key(
                position.chain, position.pair_address, position.token_address
            )
            if key in self.market.market:
                continue
            price, liquidity = self._current_market(position)
            self.market.put(
                position.chain, position.pair_address, position.token_address,
                price, liquidity,
            )

        # UN enregistrement par token par tick, pas un par position.
        for token_address, price, liquidity in self.market.by_token():
            self.history.record_raw(token_address, price=price, liquidity=liquidity or 0)
            self._last_prices[token_address] = price

        for arm in self.arms:
            self._monitor_arm(arm, verbose=verbose)

    def _monitor_arm(self, arm, verbose: bool = True) -> None:
        portfolio = arm.portfolio
        for position_id in list(portfolio.positions):
            position = portfolio.positions.get(position_id)
            if position is None:
                continue

            price, _ = self.market.get(
                position.chain, position.pair_address, position.token_address
            )
            if price is None:
                if verbose:
                    print(
                        f"[Monitor]{arm.label} {position.symbol} : prix indisponible, "
                        f"position gardée"
                    )
                continue

            drop = self.history.liquidity_drop_pct(
                position.token_address, window_seconds=RUG_WINDOW_SECONDS
            )
            # Une chute qui n'a PAS déclenché de sortie est la seule façon de
            # savoir si le seuil de rug (-50%) est trop permissif : une
            # position qui a vu -40% puis est morte aurait été sauvée plus bas.
            if drop is not None and drop <= LIQUIDITY_ALERT_PCT:
                self.funnel.record_liquidity_alert(
                    arm.name, position, drop, acted=drop <= RUG_LIQUIDITY_DROP_PCT
                )
            self._announce_milestones(arm, position, price)

            if verbose:
                print(
                    f"[Monitor]{arm.label} {position.symbol:>10} | ${price:.8f} "
                    f"| P&L {position.pnl_pct(price):+.1f}% "
                    f"| {position.duration_minutes():.0f} min | reste "
                    f"{position.remaining_fraction * 100:.0f}%"
                )

            for row in portfolio.update(position_id, price, drop):
                self.funnel.record_exit(
                    arm.name, position, price, row["exit_reason"],
                    row["pnl_pct"], drop,
                )
                laisse = (position.high_water_pct or 0) - row["pnl_pct"]
                reste = f" | laissé {laisse:.0f} pts" if laisse > 10 else ""
                print(
                    f"  💰{arm.label} {row['exit_reason']} | {row['pnl_pct']:+.1f}% "
                    f"| {row['pnl_usd']:+.2f} ${reste}"
                )
                if row["is_final_exit"]:
                    self._measure_hold(arm, position, row)
                    self._after_trade_closed(arm)
        portfolio.flush()

    def _announce_milestones(self, arm, position, price: float) -> None:
        """Annonce les paliers de gain LATENT franchis par une position ouverte.

        LE TROU QUE ÇA BOUCHE. `send_entry` et `send_exit` couvrent les deux
        extrémités du trade. Entre les deux, rien : une position pouvait monter
        à +83 % sans qu'aucune notification ne parte, et sur un bras en
        `notify=none` même sa sortie n'aurait été vue qu'au digest, quatre
        heures plus tard. C'est le cas signalé sur `Meowt` (runner).

        L'ÉTAT VIT DANS LA BOUCLE, pas dans la position : `Position` est
        immuable et sérialisée sur disque, y ajouter un champ obligerait à
        migrer les positions déjà ouvertes. Le coût est qu'un redémarrage
        réannonce une fois un palier déjà franchi — préférable à une migration
        de format pour une notification.

        Le plus-haut sert de référence et pas le prix courant : un palier
        franchi puis reperdu a bien été franchi, et c'est justement le moment
        où l'information avait de la valeur.
        """
        atteint = max(position.pnl_pct(price), position.high_water_pct or 0.0)
        deja = self._milestones_sent.setdefault(position.id, set())
        notifier = getattr(arm.portfolio, "notifier", None)
        if notifier is None:
            return

        for palier in GAIN_MILESTONES:
            if atteint >= palier and palier not in deja:
                deja.add(palier)
                notifier.send_milestone(position, atteint, palier)

    def _current_market(self, position) -> tuple[Optional[float], Optional[float]]:
        """Prix + liquidité. DexScreener d'abord (quota large), Birdeye en repli."""
        if position.pair_address:
            data = self.dex.get_pair_data(position.chain, position.pair_address)
            pairs = data.get("pairs") or []
            if pairs:
                pair = pairs[0]
                price = float(pair.get("priceUsd") or 0)
                liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
                if price > 0:
                    return price, liquidity
        return self.birdeye.get_price(position.token_address), None

    def _current_price(self, token_address: str, chain: str, pair_address: Optional[str]):
        if pair_address:
            price = self.dex.get_price(chain, pair_address)
            if price:
                return price
        return self.birdeye.get_price(token_address)

    def _after_trade_closed(self, arm) -> None:
        """Réapprentissage APRÈS chaque trade, sur le journal DE CE BRAS.

        Chaque stratégie apprend de ses propres trades, avec ses propres
        bornes. Un plancher de `MIN_TRADES_PER_ARM` empêche d'ajuster sur un
        échantillon que le multi-bras a déjà divisé par sept.
        """
        changes = arm.learning.run(max_drawdown_pct=arm.portfolio.max_drawdown_pct)
        for change in changes:
            print(f"🧠{arm.label} {change}")
        # Seules les vraies décisions partent en notification : « en attente »
        # est une information de log, pas une alerte.
        decisions = [c for c in changes if not c.startswith("(en attente)")]
        if decisions and arm.is_baseline:
            self.telegram.send("🧠 <b>Paramètres ajustés</b>\n" + "\n".join(decisions))

    def _try_entries(self, evaluation, arm, confluence: dict) -> None:
        threshold = arm.entry_threshold
        max_positions = arm.max_positions

        for candidate in evaluation.result.candidates:
            # Le seuil porte sur le score ABSOLU : le score classant contient
            # 40% de rang dans le batch, et le meilleur d'un lot médiocre le
            # franchirait mécaniquement.
            if candidate.alpha_score_absolute < threshold:
                break

            trace = (arm.name, candidate.token_address, candidate.symbol)

            if arm.role == CONSENSUS:
                # Quorum sur les VOTANTS PRÉSENTS. Un bras dont la fenêtre
                # d'âge ne couvre pas ce token s'abstient — le compter comme
                # un NON rendait le quorum inatteignable dès que les fenêtres
                # divergent, ce qui est le cas par construction : 2 accords
                # sur 81 évaluations avec le décompte brut.
                signals = arm_signals(
                    self._last_evaluations,
                    candidate.token_address,
                    thresholds={a.name: a.entry_threshold for a in self.arms},
                    voters=self.voters,
                )
                verdict = aggregate(
                    signals, min_ratio=CONSENSUS_MIN_RATIO,
                    min_voters=arm.min_confluence,
                )
                self.funnel.record(
                    *trace, "confluence", verdict.passed, verdict.reason,
                    {
                        "yes": verdict.yes, "no": verdict.no,
                        "abstained": verdict.abstained, "voters": verdict.voters,
                    },
                )
                if not verdict.passed:
                    continue

            blocker = arm.portfolio.can_open(candidate, max_positions)
            self.funnel.record(*trace, "portefeuille", not blocker, blocker or "")
            if blocker:
                print(f"⏸️ {arm.label} {candidate.symbol} qualifié mais bloqué : {blocker}")
                break

            verdict = self._technical_verdict(candidate)
            self.funnel.record(
                *trace, "technique", verdict.passed,
                "" if verdict.passed else " / ".join(verdict.reasons)[:160],
            )
            print(
                f"\n🎯{arm.label} {candidate.symbol} absolu "
                f"{candidate.alpha_score_absolute} ≥ {threshold} "
                f"(classement {candidate.alpha_score}) → technique : {verdict.label}"
            )
            for reason in verdict.reasons:
                print(f"     · {reason}")
            if verdict.passed:
                self._open_position(candidate, arm)

    def _economics_guard(
        self, candidate: Candidate, size_usd: float, arm
    ) -> tuple[float, Optional[dict]]:
        """Le carnet peut-il porter cette taille, et le TP rembourse-t-il l'entrée ?

        Deux verdicts distincts, dans cet ordre :
          1. la TAILLE — on la réduit tant que le coût dépasse le budget ;
          2. l'ÉCONOMIE — mesuré, un aller-retour va de 1,85 % à 23,62 %
             selon le token, et à 23,6 % il faut +233 % de TP1 pour rentrer
             dans ses frais. Aucun signal ne rattrape cette géométrie.

        Jupiter absent ou devis indisponible n'entre PAS : même invariant que
        le reste du pipeline, une donnée manquante ne rejette jamais.
        """
        if self.jupiter is None or not self.jupiter.enabled:
            return size_usd, None

        # Honeypot AVANT le dimensionnement : inutile de calculer une taille
        # sur un token dont on ne peut pas sortir. Aucun wallet requis — c'est
        # un devis dans le sens vente.
        sol_price = self.jupiter.price(SOL_MINT) or 0.0
        vendable, pourquoi = self.jupiter.sellable(
            candidate.token_address, size_usd, sol_price
        )
        if vendable is False:
            print(f"     🍯 {pourquoi}")
            self.cache.blacklist(candidate.token_address, pourquoi, 24)
            return 0.0, {"viable": False, "reason": f"honeypot — {pourquoi}"}

        exits = arm.params.get("exit_rules", {})
        max_cost = arm.params.get("entry_rules.max_round_trip_pct", 6.0)
        sized, cost, why = size_for_cost(
            self.jupiter, candidate.token_address, size_usd, max_cost,
            sol_price_usd=sol_price,
        )
        if sized <= 0:
            print(f"     💸 {why}")
            return 0.0, {"viable": False, "reason": why, "round_trip_pct": cost}

        stats = arm.portfolio.stats()
        tp1 = exits.get("take_profit_1", 100)
        # Le vécu du bras s'il est significatif, sinon l'a priori mesuré sur
        # l'historique global : un bras neuf a un win rate de 0 %, ce qui
        # exigeait un TP de +60% et l'empêchait de jamais démarrer.
        win_rate, source, win_rate_high = economics.win_rate_for(
            tp1, stats["win_rate"] / 100, stats["total_trades"]
        )
        verdict = economics.evaluate(
            round_trip_pct=cost,
            take_profit_pct=tp1,
            win_rate=win_rate,
            partial_fraction=exits.get("partial_sell_tp1_pct", 0.5),
            loss_pct=exits.get("stop_loss_pct", -25),
            max_round_trip_pct=max_cost,
            # Le veto se juge sur la borne haute : le taux d'atteinte vient de
            # 4 succès sur 26 positions, refuser une entrée sur les 15 % nus
            # serait de la fausse précision.
            win_rate_high=win_rate_high,
        )
        print(f"     💸 {verdict.reason} ({source})")
        return (sized if verdict.viable else 0.0), verdict.as_dict()

    def _technical_verdict(self, candidate: Candidate) -> TechnicalVerdict:
        """Vraies bougies Birdeye si disponibles, sinon reconstruction.

        1 requête Birdeye par candidat évalué — seuls ceux au-dessus du seuil
        d'entrée arrivent ici, donc 1 à 3 requêtes par cycle.
        """
        # CHAÎNE DE REPLI. GMGN passe AVANT Birdeye : il rend les mêmes
        # bougies sans plafond mensuel connu, quand Birdeye est à 1 req/s et
        # que son quota gratuit s'épuise en quelques jours. Sans vraies
        # bougies, `analyze` retombe sur des snapshots reconstruits, beaucoup
        # moins fiables — et le faisait jusqu'ici en silence.
        interval = self.params.get("entry_rules.ohlcv_interval", "1m")
        candles = None
        if self.gmgn and self.gmgn.enabled:
            rows = self.gmgn.kline(
                candidate.token_address, resolution=interval, lookback_seconds=3600
            )
            if rows:
                candles = from_gmgn_kline(rows)
        if candles is None and self.birdeye.enabled:
            ohlcv = self.birdeye.get_ohlcv(
                candidate.token_address, interval=interval, lookback_seconds=3600
            )
            if ohlcv:
                candles = from_ohlcv(ohlcv)
        if candles is None:
            print(f"     📉 aucune bougie réelle pour {candidate.symbol} — "
                  f"structure jugée sur des snapshots reconstruits")

        return analyze(
            candidate,
            self.history,
            max_initial_dump_pct=self.params.get("entry_rules.max_initial_dump_pct", -50),
            candle_seconds=self.params.get("entry_rules.candle_seconds", 180),
            required_candles=self.params.get("entry_rules.required_candles", 3),
            candles=candles,
            structure_mode=self.params.get("entry_rules.structure_mode", "balanced"),
        )

    def _open_position(self, candidate: Candidate, arm) -> None:
        stats = arm.portfolio.stats()
        size = arm.portfolio.position_size(
            risk_per_trade=arm.params.get("risk_per_trade", 0.03),
            win_rate=stats["win_rate"] / 100,
            total_trades=stats["total_trades"],
        )
        # Dernière porte, et la seule qui regarde le CARNET plutôt que le
        # signal : un devis Jupiter dit ce que la taille coûte vraiment.
        size, verdict = self._economics_guard(candidate, size, arm)
        trace = (arm.name, candidate.token_address, candidate.symbol)
        self.funnel.record(
            *trace, "economie", size > 0, (verdict or {}).get("reason", ""),
            {
                "size_usd": size,
                "round_trip_pct": (verdict or {}).get("round_trip_pct"),
                "minimum_tp_pct": (verdict or {}).get("minimum_tp_pct"),
            },
        )
        if size <= 0:
            return

        # `arm.params.data` et pas `self.params.data` : les règles de sortie
        # sont figées à l'entrée depuis le document DE CE BRAS.
        position = arm.portfolio.open(candidate, arm.params.data, size)
        self.funnel.record(*trace, "entree", True, "", {"size_usd": size})
        self._measure_entry(position, candidate)
        cost = (verdict or {}).get("round_trip_pct")
        frais = f" | frais A/R {cost:.1f}%" if cost is not None else ""
        print(
            f"🟢 ENTRY{arm.label} [{self.mode}] | ${position.symbol} "
            f"| Score {position.alpha_score} | Size {size:.2f} $ "
            f"| Entry ${position.entry_price:.8f} | SL {position.stop_loss_pct}% "
            f"| TP1 +{position.take_profit_1}%{frais}"
        )

    def _run_agents(self, essais, symbol: str) -> None:
        """Exécute des mesures en isolant les pannes.

        Un agent de mesure qui tombe ne doit pas emporter le cycle : à
        l'ouverture la position existe déjà, à la clôture le trade est déjà
        écrit au journal. Perdre la boucle de vue serait bien pire que perdre
        une mesure. Invariant « la boucle ne meurt jamais ».
        """
        for nom, mesure in essais:
            try:
                mesure()
            except Exception as exc:  # noqa: BLE001
                print(f"[Agents] {nom} indisponible sur {symbol} : {exc}")

    def _measure_entry(self, position, candidate: Candidate) -> None:
        """Les mesures qui n'ont de sens QU'À L'OUVERTURE.

        POURQUOI ICI ET NULLE PART AILLEURS. `PriceHistory` vit en MÉMOIRE : il
        n'est écrit nulle part et disparaît à chaque redémarrage. Les snapshots
        d'AVANT l'entrée n'existent donc que pendant le cycle qui ouvre la
        position — c'est la seule fenêtre où le contrefactuel peut reconstituer
        ce que valait le token un cycle plus tôt. Et le candidat enrichi, que
        lit `dev_history`, n'existe pas non plus au-delà de ce cycle.

        Conséquence : ces mesures ne peuvent PAS être rejouées sur les 93
        trades déjà clôturés. Elles partent de zéro, vers l'avant.

        CE QUI A ÉTÉ DÉPLACÉ, ET POURQUOI. RSI, volatilité et microstructure
        étaient ici aussi. Mesuré sur les deux premières entrées réelles du
        2026-08-02 : 1 et 3 clôtures pour un RSI qui en demande 15, 1 et 5
        rendements pour une volatilité qui en demande 8. Le bot entre vite
        après la découverte, donc `PriceHistory` est presque vide à l'ouverture.
        Ces trois-là mesurent une ACCUMULATION : leur place est à la clôture,
        sur les snapshots du monitoring à 5 s. Voir `_measure_hold`.

        AUCUNE DE CES MESURES NE DÉCIDE. La position est déjà ouverte quand
        cette méthode est appelée ; elle ne peut ni l'annuler ni la
        redimensionner. C'est délibéré : sur 93 trades, brancher une décision
        sur un indicateur neuf serait du surapprentissage.
        """
        snapshots = self.history.snapshots(position.token_address)
        self._run_agents((
            ("counterfactual", lambda: self.counterfactual.observe(
                position.token_address, position.symbol,
                position.entry_price, time.time(), snapshots,
            )),
            ("dev_history", lambda: self.dev_history.observe(candidate)),
        ), position.symbol)

    def _measure_hold(self, arm, position, row: dict) -> None:
        """Les mesures d'ACCUMULATION, déclenchées à la clôture finale.

        POURQUOI À LA CLÔTURE ET PAS À CHAQUE TICK. Le monitoring tourne à 5 s :
        mesurer à chaque tour écrirait des centaines de lignes par position,
        toutes redondantes sauf la dernière. À la clôture, les snapshots de
        toute la détention sont là — c'est le point de mesure le plus riche, et
        il ne coûte qu'une ligne par position.

        CE QUE ÇA CHANGE CONCRÈTEMENT. À l'ouverture la volatilité voyait 1 à 5
        rendements pour un minimum de 8. Sur une détention de 30 min à 5 s par
        tick, elle en voit environ 360. Le RSI reste le plus exigeant : les
        bougies font 180 s, donc ses 15 clôtures demandent 45 minutes de
        détention — plus long que la durée de vie de plusieurs bras. Il
        journalisera « inconnu » sur les positions courtes, et c'est la
        réponse honnête plutôt qu'un RSI calculé sur trois points.

        `position_id` et `arm` sont écrits pour que ces lignes se joignent au
        journal de trades : une volatilité sans son P&L ne sert à rien.
        """
        snapshots = self.history.snapshots(position.token_address)
        contexte = {
            "position_id": row.get("position_id") or position.id,
            "arm": arm.name,
            "pnl_pct": row.get("pnl_pct"),
            "peak_pct": position.high_water_pct,
            "duration_min": position.duration_minutes(),
        }
        self._run_agents((
            ("volatility", lambda: self.volatility.observe(
                position.token_address, position.symbol, snapshots,
                extra=contexte)),
            ("microstructure", lambda: self.microstructure.observe(
                position.token_address, position.symbol, snapshots,
                size_usd=position.size_usd, extra=contexte)),
            ("rsi", lambda: self.rsi.observe(
                position.token_address, position.symbol,
                self.history.candles(position.token_address), extra=contexte)),
        ), position.symbol)

    def _periodic_reports(self) -> None:
        now = time.time()
        if now - self._last_dashboard >= DASHBOARD_EVERY_SECONDS:
            self._print_dashboard()
            self._last_dashboard = now
        if now - self._last_summary >= SUMMARY_EVERY_SECONDS:
            self.telegram.send_summary(self.portfolio.stats())
            self._send_arm_digest()
            self._last_summary = now

    def _send_arm_digest(self) -> None:
        """Un seul message pour toutes les stratégies silencieuses.

        Sept bras qui notifient chacun leurs entrées et sorties noieraient le
        canal ; le témoin parle en direct, les autres s'accumulent ici.
        """
        lignes = []
        for arm in self.arms:
            notifier = getattr(arm.portfolio, "notifier", None)
            messages = notifier.drain_digest() if notifier else []
            stats = arm.portfolio.stats()
            if messages or stats["total_trades"]:
                lignes.append(
                    f"<b>{arm.name}</b> — {stats['total_trades']} trades, "
                    f"WR {stats['win_rate']}%, {stats['total_pnl_usd']:+.2f} $"
                )
                # LA TRONCATURE EST DITE. À 8-32 trades par jour et par bras,
                # une fenêtre de 4 h en accumule bien plus que 5 : n'en montrer
                # que les derniers SANS le signaler faisait passer un digest
                # amputé pour un digest complet.
                garde = messages[-DIGEST_MAX_LINES:]
                omis = len(messages) - len(garde)
                if omis > 0:
                    lignes.append(f"  <i>({omis} message(s) plus ancien(s) omis)</i>")
                lignes += [f"  {m}" for m in garde]
        if lignes:
            self.telegram.send("🧬 <b>Stratégies</b>\n" + "\n".join(lignes))

    def _print_dashboard(self) -> None:
        """Deux blocs, et l'ordre compte.

        LA FLOTTE D'ABORD. Ce panneau n'affichait que le témoin, gelé et sans
        trade depuis des heures : il montrait « 4 gagnants sur 39 » pendant que
        les six autres bras en accumulaient 27 sur 93. Les gains étaient
        enregistrés — c'est la lecture qui regardait le mauvais portefeuille.

        LE TÉMOIN QUAND MÊME, en dessous et étiqueté. C'est la seule référence
        comparable aux trades historiques : le sortir du panneau ferait perdre
        le point de comparaison qui donne un sens aux chiffres de la flotte.
        """
        stats = self.portfolio.stats()
        agg = self._aggregate_stats()
        history = self.params.get("learning.parameter_adjustment_history", []) or []
        last = history[-1] if history else None
        allowed, why = self.learning.live_mode_allowed()

        print(f"\n┌{'─' * 60}┐")
        print(f"│ MEMECOIN ALPHA LOOP — DASHBOARD [{stats['mode']}]".ljust(61) + "│")
        print(f"├{'─' * 60}┤")
        print(
            f"│ FLOTTE ({agg['arms']} bras) — capital {agg['equity']:.2f} $ | "
            f"P&L {agg['total_pnl_usd']:+.2f} $".ljust(61) + "│"
        )
        print(
            f"│ Win rate : {agg['win_rate']}% ({agg['wins']}/{agg['closed_trades']}) | "
            f"Profit factor : {agg['profit_factor']}".ljust(61) + "│"
        )
        print(
            f"│ Positions ouvertes : {agg['open_positions']} | "
            f"exposition {agg['exposure_usd']:.2f} $ | "
            f"pire drawdown {agg['worst_arm_drawdown_pct']}%".ljust(61) + "│"
        )
        print(f"├{'─' * 60}┤")
        print(
            f"│ témoin (gelé) : {stats['equity']:.2f} $ | "
            f"{stats['total_pnl_usd']:+.2f} $ | WR {stats['win_rate']}% | "
            f"PF {stats['profit_factor']} | {stats['total_trades']} trades".ljust(61)
            + "│"
        )
        if stats["cooldown_min"]:
            print(f"│ ⛔ COOLDOWN : {stats['cooldown_min']:.0f} min restantes".ljust(61) + "│")
        if last:
            print(f"│ Dernier ajustement : {last['param_name']}".ljust(61) + "│")
            print(f"│   {last['old_value']} → {last['new_value']}".ljust(61) + "│")
        print(f"│ Passage LIVE : {'AUTORISÉ' if allowed else 'bloqué'} ({why})".ljust(61) + "│")
        print(f"└{'─' * 60}┘")

    def _print_banner(self, interval: int) -> None:
        status = settings.key_status()
        active = ", ".join(k for k, v in status.items() if v) or "aucune"
        missing = ", ".join(k for k, v in status.items() if not v) or "aucune"
        print(
            f"\n{'=' * 78}\n"
            f"  MEMECOIN ALPHA LOOP V2 — mode {self.mode}\n"
            f"{'=' * 78}\n"
            f"  Capital papier  : {self.portfolio.capital:.2f} $\n"
            f"  Intervalle      : {interval}s (monitoring "
            f"{self.params.get('scan.monitor_interval_seconds')}s)\n"
            f"  Chaînes         : {', '.join(self.params.get('scan.chains', []))}\n"
            f"  Seuil d'entrée  : alpha absolu ≥ "
            f"{self.params.get('scan.alpha_score_entry_threshold')}\n"
            f"  Structure       : {self.params.get('entry_rules.structure_mode')}\n"
            f"  Positions max   : {self.params.get('max_concurrent_positions')}\n"
            f"  Cache TTL       : {self.params.get('scan.cache_ttl_minutes')} min "
            f"({self.cache.stats['seen']} en cache, "
            f"{self.cache.stats['watched']} surveillés, "
            f"{self.cache.stats['blacklisted']} blacklistés)\n"
            f"  APIs actives    : {active}\n"
            f"  APIs manquantes : {missing}\n"
            f"  Stratégies      : {self._arms_banner()}\n"
            f"  Capacités       :\n{self.capabilities.summary()}\n"
            f"{'=' * 78}"
        )

    def _arms_banner(self) -> str:
        if len(self.arms) < 2:
            return "1 (mono-stratégie)"
        details = ", ".join(
            f"{arm.name} (SL {arm.params.get('exit_rules.stop_loss_pct')} / "
            f"TP1 +{arm.params.get('exit_rules.take_profit_1')})"
            for arm in self.arms
        )
        return f"{len(self.arms)} — {details}\n                    " + (
            f"chacune {self.portfolio.baseline:.0f} $ de mise INDÉPENDANTE "
            f"(capital_pct = plan d'allocation LIVE, inutilisé en PAPER)"
        )

    def _print_result(self, result: ScanResult) -> None:
        print(
            f"\n📊 {result.scanned_count} scannés | {result.cached_skipped} sautés (cache) "
            f"| {len(result.candidates)} qualifiés | {result.duration_sec}s "
            f"| {self.history.tracked_count} suivis "
            f"| {self.dex.request_count} req DEX / {self.birdeye.request_count} req Birdeye"
        )
        for rank, candidate in enumerate(result.candidates[:10], 1):
            print(f"  {rank:>2}. {candidate.summary()}")
        if not result.candidates:
            print("  (aucun candidat qualifié ce cycle)")


def main() -> None:
    # Une seule boucle à la fois : deux instances corrompent l'état partagé.
    lock = InstanceLock(settings.LOCK_PATH)
    holder = lock.acquire()
    if holder is not None:
        print(
            f"⛔ Une boucle tourne déjà (PID {holder}). "
            f"Arrête-la d'abord, ou supprime {settings.LOCK_PATH} si elle est morte."
        )
        raise SystemExit(1)

    signal.signal(signal.SIGINT, _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)
    try:
        AlphaLoop().run()
    finally:
        lock.release()


if __name__ == "__main__":
    main()
