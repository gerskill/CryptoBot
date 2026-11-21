"""MemeCoin Alpha Loop V2 — boucle principale.

Boucle complète en PAPER : scan -> scoring -> analyse technique -> ouverture
de position simulée -> monitoring SL/TP/trailing -> journal -> apprentissage.

AUCUNE TRANSACTION RÉELLE. Le passage en LIVE demande un module d'exécution
(swap Jupiter, signature, wallet) qui n'existe pas ici, et il est verrouillé
par `LearningEngine.live_mode_allowed()` : 20 trades, WR > 40%, PF > 1.5.

Lancement : python -m src.main
Arrêt     : Ctrl-C, ou /STOP depuis Telegram (ferme toutes les positions).
"""

import math
import os
import signal
import threading
import time
from contextlib import nullcontext
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
from src.core.pair_resolver import PairResolver
from src.apis.gmgn import CACHE_TTL_SECONDS as GMGN_CACHE_TTL_SECONDS, GmgnAPI
from src.apis.helius import HeliusAPI, use_watch_priority
from src.apis.rugcheck import RugCheckAPI
from src.apis.twitter import TwitterAPI
from src.agents import (
    CounterfactualTimingAgent,
    DevHistoryAgent,
    MicrostructureAgent,
    RSIAgent,
    TelegramReporterAgent,
    VolatilityAgent,
)
from src.core.entry_context import build_entry_context
from src.agents.onchain_observer import SOCIAL_SCORE_SOURCE, OnchainObserver, social_links
from src.core.score_v2 import score_v2_gate
from src.core.anti_rug import anti_rug_gate
from src.core import copy_smart
from src.core.arm import CONSENSUS, attach_portfolios, bootstrap_arms
from src.apis.jupiter import SOL_MINT, JupiterAPI
from src.core import correlation, economics
from src.core.risk import is_soft_reject, pre_trade_check
from src.core.budget import load_budgets
from src.core.dev_watchdog import DevWatchdog
from src.core.capabilities import build_registry
from src.core.economics import size_for_cost
from src.core.exit_fees import measure_exit_cost
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
from src.core.portfolio import (
    ENTRY_COST_VERSION,
    MAX_POSITION_PCT_OF_CAPITAL,
    PREPARED_UNSET,
    SCALE_IN_RETRY_SECONDS,
)
from src.core.position_watch import (
    PositionWatch,
    fetch_unique,
    quote_is_usable,
    sample_gap_line,
    watch_snapshot,
)
from src.core.positions import (
    RUG_LIQUIDITY_DROP_PCT,
    evaluate_exits,
    scale_in_due,
    update_water_marks,
)
from src.core.quota_agent import QuotaAgent
from src.core.research_log import ResearchJournal
from src.core.pricefeed import (
    MONITOR_BUDGET_PER_MIN,
    CycleMarketCache,
    effective_monitor_interval,
)
from src.core import commandes as bot_commands
from src.core.shadow import ShadowTracker
from src.core.state import StateWriter, candidate_to_dict, position_to_dict
from src.core.wallets import WalletRegistry
from src.notify.telegram import TelegramNotifier
from src.pipeline import CollectedBatch, ScanPipeline, discovery_envelope

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
# Délai de grâce APRÈS le time stop avant de fermer d'office une position dont
# aucune source ne donne de prix. Avec un prix, le time stop l'aurait déjà
# fermée : encore ouverte 30 min plus tard, elle est forcément bloquée sans
# prix (token mort ou rugué). Le délai absorbe une panne passagère des API.
ZOMBIE_GRACE_MINUTES = 30
# Un RUG_PULL exige 2 lectures consécutives du monitoring sous le seuil (une de
# plus = 5 s au tick nominal), sauf si le prix s'est effondré en même temps.
# 225 des 229 faux RUG_PULL mesurés venaient d'UNE lecture « liquidité 0 » qui
# n'était qu'une liquidité INCONNUE (repli Jupiter, paire pump.fun).
RUG_CONFIRM_READINGS = 2
RUG_PRICE_CRASH_PCT = -40.0
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


def _books_lock(loop):
    """Verrou des livres, ou rien si l'objet est un double de test incomplet."""
    lock = getattr(loop, "_books", None)
    if lock is None:
        return nullcontext()
    return lock


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
        # UNE SEULE INSTANCE, PARTAGÉE. Deux `HeliusAPI(...)` séparées ici même
        # (capabilities et pipeline) créaient chacune leur propre RateLimiter :
        # 10/s x2 instances = jusqu'à 20 req/s réelles contre un palier prévu
        # pour 10, sans que rien ne le voie. Trouvé en câblant la mesure de
        # priority fee, qui a besoin elle aussi d'un client Helius — la
        # troisième instance aurait aggravé le même défaut au lieu de le
        # corriger.
        self.helius = HeliusAPI(
            settings.HELIUS_API_KEY,
            max_per_second=settings.HELIUS_MAX_PER_SECOND,
        )

        # OBSERVATEUR ON-CHAIN (2026-10-10, observation seulement) : holders,
        # lancement, bundles, wallets frais, flux d'acheteurs. Fil d'arrière-
        # plan, budget de crédits journalier, aucun appel réseau dans la
        # boucle ; un résultat n'entre au journal que s'il est horodaté AVANT
        # la décision. Ne lit et n'écrit aucun paramètre de décision.
        try:
            self.onchain = OnchainObserver(
                settings.HELIUS_API_KEY, rate_limiter=self.helius.rate_limiter
            )
        except Exception as exc:
            print(f"[OnChain] ⚠️ observateur désactivé — {type(exc).__name__}")
            self.onchain = OnchainObserver(None, start_thread=False)

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
        # CELUI-CI, LUI, DÉCIDE — contrairement aux cinq ci-dessus. Il ferme
        # une position quand le créateur distribue pendant qu'on la détient
        # (slow rug), là où `DevHistoryAgent` ne juge qu'à l'entrée et où le
        # détecteur de rug ne voit que l'effondrement brutal de liquidité.
        # `risk_rules.dev_dump_panic_exit` à `false` le remet en simple
        # observateur : il mesure et alerte, sans fermer.
        self.dev_watchdog = DevWatchdog(helius=self.helius)
        self.budgets = load_budgets(
            settings.BUDGET_PATH,
            {k: v for k, v in (self.params.get("api_budgets", {}) or {}).items()
             if isinstance(v, int)},
            # `self.telegram` n'existe pas encore à cet instant de `__init__` :
            # la fermeture ne le lit qu'au premier épuisement réel, jamais ici.
            on_exhausted=lambda name: self.telegram.send(
                f"⚠️ <b>Quota mensuel épuisé</b> : {name} — collecte désactivée "
                f"jusqu'au mois prochain."
            ),
        )
        # Ajuste le débit (`scan.social_max_lookups_per_cycle`) sur le RYTHME
        # de consommation mesuré, plutôt que sur une constante réglée une fois
        # à la main. Voir `src/core/quota_agent.py`.
        self.quota_agent = QuotaAgent(self.params)
        # Attributs et non instances anonymes : le registre de capacités a
        # besoin des MÊMES objets que le pipeline pour juger leur état. Deux
        # instances distinctes se seraient désynchronisées dès le premier
        # auto-arrêt (Twitter 402, RugCheck en panne).
        self.rugcheck = RugCheckAPI()
        self.twitter = TwitterAPI(
            settings.TWITTER_BEARER_TOKEN,
            max_lookups_per_cycle=self.params.get("scan.social_max_lookups_per_cycle", 2),
            budget=self.budgets.get("twitter"),
        )
        self.pipeline = ScanPipeline(
            params=self.params,
            cache=self.cache,
            dex=self.dex,
            helius=self.helius,
            rugcheck=self.rugcheck,
            birdeye=self.birdeye,
            twitter=self.twitter,
            gmgn=self.gmgn,
            history=self.history,
        )

        # Santé par CAPACITÉ, pas par API : ce qui compte n'est pas « Birdeye
        # est mort » mais « peut-on encore obtenir des bougies ». Publié dans
        # state.json pour que la dégradation soit visible au lieu d'être subie.
        # Construit APRÈS le pipeline : `self.twitter` dépend de `self.budgets`,
        # lui-même construit plus haut.
        self.capabilities = build_registry(
            birdeye=self.birdeye, gmgn=self.gmgn, jupiter=self.jupiter, dex=self.dex,
            helius=self.helius, rugcheck=self.rugcheck, twitter=self.twitter,
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
        # Bras de test `copy_smart` (2026-10-29) : liste calculée sur notre
        # historique, signaux lus dans le flux GMGN déjà payé. Zéro appel en plus.
        self.copy_list = copy_smart.SmartWalletList()
        self.copy_sources = copy_smart.CopySources(
            f"{settings.BASE_DIR}/data/copy_smart_sources.json"
        )
        # Wallets manuels (2026-11-02, accord Killian) : toujours suivis, lus
        # sur le RPC Solana (Chainstack → Helius → public) car absents du flux GMGN.
        self.copy_manual_path = f"{settings.BASE_DIR}/config/copy_smart_manual_wallets.json"
        self.copy_manual = copy_smart.load_manual_wallets(self.copy_manual_path)
        # RPC : Chainstack → Helius → public (rpc_endpoint), sans forcer une URL seule.
        self.copy_watcher = copy_smart.ManualWalletWatcher()
        if self.copy_manual:
            print(f"[CopySmart] {len(self.copy_manual)} wallet(s) manuel(s) suivi(s) : "
                  + ", ".join(w[:4] + "…" + w[-3:] for w in self.copy_manual))
        # « Pourquoi ce bras ne trade pas ? » : marché vide, seuil trop
        # strict, technique qui refuse, ou carnet trop mince ? Sans trace par
        # porte, ces quatre causes sont indiscernables et appellent des
        # corrections opposées.
        self.funnel = FunnelRecorder(settings.FUNNEL_LOG_PATH)
        self.state = StateWriter(settings.STATE_PATH)
        self.telegram = TelegramNotifier(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)
        self.telegram.drain_pending()
        # POINT DE SORTIE UNIQUE des sept stratégies. Il arbitre par NATURE
        # d'événement et non par bras : mesuré le 2026-08-02 sur 108 trades,
        # la politique par bras avait tu 16 sorties à +50 % ou plus.
        self.reporter = TelegramReporterAgent(
            inner=self.telegram, log_path=settings.TELEGRAM_REPORTER_LOG_PATH
        )
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
            notifier=self.reporter,
            mode=self.mode,
            cooldown_hours=cooldown,
            losses_trigger=self.params.get("risk_rules.consecutive_losses_trigger", 3),
            max_drawdown_stop_pct=self.params.get(
                "risk_rules.max_drawdown_stop_pct", 0.0
            ),
            exit_fee_measurer=self._measure_exit_cost,
            scale_in_checker_factory=self._make_scale_in_checker,
        )
        # `self.portfolio` reste celui du témoin : le dashboard, l'API et les
        # 36 trades historiques continuent de le lire sans migration.
        self.portfolio = self.baseline.portfolio
        self.learning = self.baseline.learning
        self.shadow = self.baseline.shadow
        # Câblé ici et non à la construction de `self.pipeline` : `journal`,
        # `wallets` et le `shadow` définitif (celui du témoin) n'existent pas
        # encore à ce moment de `__init__`.
        self.pipeline.wallets = self.wallets
        self.pipeline.journal = self.journal
        self.pipeline.shadow = self.shadow
        self.market = CycleMarketCache()
        # Livres (positions, capital, journal de sortie) : le scan et le fil
        # de surveillance s'y retrouvent. Le verrou n'est PAS tenu pendant
        # les lectures de prix, de liquidité ou de devis.
        self._books = threading.RLock()
        self._watch_metrics = {
            "quotes_ok": 0,
            "quotes_missing": 0,
            "last_quote_at": None,
            "last_tick_sec": None,
            "last_scan_sec": None,
        }
        self._position_watch = PositionWatch(
            self._watch_tick, self._monitor_interval_seconds
        )
        self._last_stretched_interval = None
        # Paire DexScreener des positions entrées sans paire (GMGN) : liquidité
        # réellement lue au monitoring. Appel groupé, cache 120 s par token.
        self.pair_resolver = PairResolver(self.dex.get_tokens_data)
        self._monitor_sources: dict[str, str] = {}

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
        # Durée du CYCLE PRÉCÉDENT, pas du courant : `_publish_state` tourne
        # avant que le cycle en cours ne soit fini de mesurer. Un décalage
        # d'un cycle plutôt qu'un chiffre inventé. Sert le badge de santé du
        # dashboard — `scan.duration_sec` ne couvrait que le sous-temps de
        # collecte, pas le cycle entier (monitoring, entrées, notifications).
        self._last_cycle_duration_sec = 0.0

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
        # La surveillance des positions tourne pendant le scan, pas seulement
        # dans le temps qui reste après. Un seul fil, arrêté avec la boucle.
        watch = getattr(self, "_position_watch", None)
        if watch is not None:
            watch.start()

        while _running:
            started = time.monotonic()
            self.cycle_count += 1
            try:
                self._cycle()
            except Exception as exc:  # la boucle ne meurt jamais sur un cycle
                print(f"[Loop] ⚠️ Erreur cycle {self.cycle_count} : {type(exc).__name__} — {exc}")

            elapsed = time.monotonic() - started
            self._last_cycle_duration_sec = round(elapsed, 1)
            metrics = getattr(self, "_watch_metrics", None)
            if metrics is not None:
                metrics["last_scan_sec"] = round(elapsed, 1)
            if elapsed > interval:
                print(f"[Loop] ⏱️ Cycle {elapsed:.0f}s > {interval}s — enchaînement direct")
            self._sleep_monitoring(max(0.0, interval - elapsed))

        if watch is not None:
            watch.stop()
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
        watch = getattr(self, "_position_watch", None)
        if watch is not None and watch.alive:
            # Le fil surveille déjà, y compris pendant le scan. Un second
            # passage ici doublerait les sorties et les lignes de journal.
            deadline = time.monotonic() + duration
            while _running and time.monotonic() < deadline:
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
            return
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
        # Le fil de surveillance couvre ce passage en continu, y compris
        # pendant le scan qui suit. S'il est tombé, on le relance ; s'il ne
        # repart pas, on garde l'ancien passage synchrone avant le scan.
        watch = getattr(self, "_position_watch", None)
        if watch is not None and not watch.alive:
            watch.start()
        if watch is None or not watch.alive:
            self._monitor_positions()

        self.market.reset_cycle()
        result = self._scan_all_arms()
        self._request_onchain_observation()
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
        self._record_research()
        self.funnel.flush()

        # APRÈS le flush : le relâchement lit l'entonnoir, y compris les lignes
        # de ce cycle-ci.
        self._relax_inactive_arms()
        self._recalibrate_quota()
        # Un seul appel par cycle : le reporter décide lui-même si son lot ou
        # son rapport sont dus, et absorbe toute panne.
        self.reporter.tick(self.arms)
        self.cache.purge()
        self._publish_state(result)
        self._periodic_reports()

    def _relax_inactive_arms(self) -> None:
        """Desserre les bras qui n'entrent plus, depuis le CYCLE.

        POURQUOI PAS DEPUIS `_after_trade_closed` COMME LE RESTE. `learning.run`
        n'est appelé qu'à la fermeture d'une position. Un bras qui n'ouvre
        jamais rien n'en ferme jamais, donc `run` n'est jamais appelé, donc le
        relâchement sur inactivité serait inatteignable **exactement pour les
        bras qu'il doit débloquer**. Mesuré : 0 entrée sur 927 cycles.

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

    def _recalibrate_quota(self) -> None:
        """Ajuste le débit des lookups sociaux sur le rythme de budget mesuré.

        Même cadence que `_relax_inactive_arms` : la demande brute vs servie
        (`record_demand`, dans `_scan_all_arms`) s'accumule à chaque cycle,
        mais la décision elle-même n'a besoin d'être reconsidérée qu'au même
        rythme que le reste des ajustements automatiques du dépôt.
        """
        if self.cycle_count % INACTIVITY_CHECK_EVERY != 0:
            return
        for change in self.quota_agent.recalibrate(self.budgets):
            print(f"🧠[QUOTA] {change}")

    def _scan_all_arms(self) -> ScanResult:
        """Une collecte, N évaluations. Retourne celle du bras témoin.

        Le coût API est entièrement dans `collect()` ; `evaluate()` est du CPU
        pur sur au plus 25 candidats, donc ajouter un bras ne coûte rien en
        requêtes. Le social est arbitré une seule fois entre les listes de
        souhaits : le plafond par cycle et le budget mensuel restent globaux.
        """
        # Un bras à source « mature » (2026-10-11) n'entre PAS dans
        # l'enveloppe : l'élargir à 30 jours changerait le lot de tous les bras.
        main_arms = [a for a in self.arms if getattr(a, "discovery", "main") == "main"]
        envelope = discovery_envelope([arm.filters() for arm in main_arms])
        batch = self.pipeline.collect(envelope)
        self._observe_wallets(batch)
        batches = {arm.name: batch for arm in self.arms}
        mature_arms = [a for a in self.arms if getattr(a, "discovery", "main") == "mature"]
        if mature_arms:
            try:
                mature_batch = self.pipeline.collect_mature(
                    mature_arms[0].filters(),
                    mature_arms[0].params.get("scan.mature_source", {}) or {},
                )
            except Exception as exc:  # source opt-in : ne casse jamais le cycle
                print(f"[Mature] ⚠️ collecte — {type(exc).__name__}: {exc}")
                mature_batch = CollectedBatch()
            for arm in mature_arms:
                batches[arm.name] = mature_batch

        pre = {
            arm.name: self.pipeline.evaluate(batches[arm.name], params=arm.params, label=arm.label)
            for arm in self.arms
        }
        # Seuls les bras qui partagent l'arbitrage social y entrent : un fork
        # du témoin doublerait sinon la « demande » sur ses tokens.
        sharing = {arm.name for arm in self.arms if getattr(arm, "social_wishlist", True)}
        addresses = merge_social_wishlists(
            {name: evaluation.wishlist for name, evaluation in pre.items() if name in sharing},
            limit=self.params.get("scan.social_max_lookups_per_cycle", 2),
        )
        social = self.pipeline.enrich_social(batch.enriched, addresses)

        # Demande BRUTE (avant le plafond par cycle) vs ce qui a pu être servi
        # — le signal dont `QuotaAgent` a besoin pour distinguer « le plafond
        # est trop bas » (demande non servie) de « le plafond ne sert à rien »
        # (personne n'en veut). Voir `src/core/quota_agent.py`.
        wanted = [ev.wishlist for name, ev in pre.items() if name in sharing]
        raw_wanted = len(set().union(*wanted)) if wanted else 0
        self.quota_agent.record_demand("twitter", wanted=raw_wanted, served=len(addresses))

        evaluations = {
            arm.name: self.pipeline.evaluate(
                batches[arm.name],
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
        main_names = {arm.name for arm in main_arms}
        for name, evaluation in evaluations.items():
            # Le lot « mature » ne touche pas la watchlist partagée : un token
            # surveillé est réinjecté dans la découverte de TOUS les bras.
            if name not in main_names:
                continue
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
            with _books_lock(self):
                positions = []
                for arm in self.arms:
                    for position in list(arm.portfolio.positions.values()):
                        # Garde-fou : une position fermée (`remaining_fraction`
                        # à 0) ne doit jamais atteindre le dashboard, même si
                        # elle traîne encore dans `arm.portfolio.positions`
                        # (crash de finalisation). Sinon elle s'affiche gelée
                        # pour de bon — figée, grisée, incompréhensible.
                        if not position.is_open:
                            continue
                        price = self._last_prices.get(position.token_address)
                        payload = position_to_dict(position, price)
                        payload["arm"] = arm.name
                        positions.append(payload)
                book_stats = self.portfolio.stats()
                book_aggregate = self._aggregate_stats()
                book_exposure = self._fleet_exposure().as_dict()
                book_arms = self._arms_payload()

            self.state.write(
                {
                    "mode": self.mode,
                    "paused": self.paused,
                    "cycle": self.cycle_count,
                    "interval_seconds": self.params.get("scan.interval_seconds", 90),
                    "next_scan_at": self._next_scan_at,
                    "cycle_duration_sec": self._last_cycle_duration_sec,
                    # `stats` reste celui du TÉMOIN : l'API, les 36 trades
                    # historiques et la courbe d'équité s'y appuient.
                    # `aggregate` donne la vue d'ensemble sans casser ça.
                    "stats": book_stats,
                    "aggregate": book_aggregate,
                    # Concentration par meta sur les sept bras. Publiée pour
                    # la même raison que `positions` l'a été : l'exposition
                    # réelle est justement ce qu'un tableau de bord doit
                    # montrer, et elle était invisible.
                    "sector_exposure": book_exposure,
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
                    "position_watch": (
                        watch_snapshot(self._position_watch, self._watch_metrics)
                        if getattr(self, "_position_watch", None) is not None
                        else None
                    ),
                    # Additif : les clés ci-dessus restent celles du bras
                    # témoin, le dashboard existant ne bouge pas. Pas de
                    # listes de candidats par bras ici — `state.json` est
                    # resérialisé sur le WebSocket toutes les secondes.
                    "arms": book_arms,
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
        # dire le TÉMOIN — sans trade depuis 16 h ce jour-là. Il montrait « 4
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
        témoin ne dit rien de ce que `quality` a écarté.

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
        # Telegram puis l'iPhone (boîte `data/commandes_bot/`, ADR 021) : mêmes
        # commandes, même traitement.
        commands = list(self.telegram.poll_commands())
        from_phone = bot_commands.lire_et_vider(os.path.join(settings.BASE_DIR, "data"))
        if from_phone:
            self.telegram.send("📱 Commande reçue depuis l'iPhone : " + ", ".join(from_phone))
        for command in commands + from_phone:
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
            with _books_lock(self):
                position_ids = list(arm.portfolio.positions)
            for position_id in position_ids:
                with _books_lock(self):
                    position = arm.portfolio.positions.get(position_id)
                if position is None:
                    continue
                price = self.market.price(position.token_address) or self._current_price(
                    position.token_address, position.chain, position.pair_address
                )
                if not quote_is_usable(price):
                    # Une seule lecture ratée ne doit pas booker -100 % sur une
                    # position peut-être saine : /STOP n'a pas de délai de
                    # grâce, donc un second essai avant de conclure « sans prix ».
                    price = self._current_price(
                        position.token_address, position.chain, position.pair_address
                    )
                if price is None or not math.isfinite(price) or price <= 0:
                    # Aucune source ne donne de prix : /STOP veut dire stop.
                    # On la compte à -100 % de la part restante plutôt que de
                    # la laisser ouverte et invisible (B1).
                    row = self._close_worthless(
                        arm, position, "PANIC_STOP (sans prix)"
                    )
                    if row:
                        print(
                            f"  💀{arm.label} {position.symbol} sans prix : "
                            f"fermé à {row['pnl_pct']:+.1f}%"
                        )
                    continue
                self.market.put(
                    position.chain, position.pair_address, position.token_address,
                    price, None,
                )
                row = self._close_at_price(arm.portfolio, position, price, "PANIC_STOP")
                if row:
                    print(f"  💰{arm.label} {position.symbol} fermé à {row['pnl_pct']:+.1f}%")
                    self._after_trade_closed(arm)
        self.paused = True

    def _open_positions(self) -> list:
        with _books_lock(self):
            return [
                p for arm in self.arms
                for p in list(arm.portfolio.positions.values())
            ]

    # ------------------------------------------------ concentration sectorielle

    @property
    def max_sector_positions(self) -> int:
        """Plafond de positions simultanées dans une meta, TOUS BRAS CONFONDUS.

        Lu sur le document GLOBAL (`self.params`) et non sur celui d'un bras :
        la règle porte sur l'agrégat des sept, aucun bras ne peut donc en
        détenir sa propre version sans que la garde perde son sens.
        """
        return self.params.get("risk_rules.max_sector_positions", 3)

    @property
    def max_sector_exposure_pct(self) -> float:
        return self.params.get("risk_rules.max_sector_exposure_pct", 50.0)

    def _fleet_exposure(self):
        """Exposition par meta, agrégée sur les sept portefeuilles.

        Recalculée à chaque candidat plutôt que mise en cache par cycle : une
        entrée ouverte plus tôt DANS LE MÊME CYCLE doit compter tout de suite,
        sinon sept bras qui évaluent le même lot ouvrent sept positions de la
        même meta avant que la garde ne voie la première.
        """
        # Les bras de test (role "test") sont exclus : ils ne doivent pas
        # consommer le plafond sectoriel des bras existants.
        with _books_lock(self):
            return correlation.measure([
                p for arm in self.arms if not getattr(arm, "is_test", False)
                for p in arm.portfolio.positions.values()
            ])

    def _monitor_positions(self, verbose: bool = True) -> None:
        """Un prix par PAIRE distincte, partagé par toutes les stratégies.

        Sept bras sur le même token demandaient sept fois le même prix par
        tick. Pire, ils empilaient sept snapshots identiques dans la deque que
        lit le détecteur de rug : la fenêtre de détection en sortait faussée.
        """
        positions = self._open_positions()
        if not positions:
            return
        if self._resolve_pairs(positions):
            positions = self._open_positions()

        self.market.reset()
        # Réseau HORS verrou. Une paire partagée par plusieurs bras n'est lue
        # qu'une fois. Un prix inutilisable reste vide : on ne reprend pas
        # `_last_prices`, qui ne sert qu'à l'affichage.
        quotes = fetch_unique(
            positions,
            lambda position: self.market.key(
                position.chain, position.pair_address, position.token_address
            ),
            self._current_market,
        )
        token_pairs: dict[str, Optional[str]] = {}
        ok = 0
        missing = 0
        counted: set[str] = set()
        for position in positions:
            key = self.market.key(
                position.chain, position.pair_address, position.token_address
            )
            price, liquidity = quotes[key]
            if key not in counted:
                counted.add(key)
                if quote_is_usable(price):
                    ok += 1
                else:
                    missing += 1
            if not quote_is_usable(price):
                price = None
            self.market.put(
                position.chain, position.pair_address, position.token_address,
                price, liquidity,
            )
            if price:
                token_pairs[position.token_address] = position.pair_address
        # `ok` / `missing` comptent les PAIRES, pas les positions : deux bras
        # sur le même token ne font qu'une lecture. L'âge ne bouge que
        # lorsqu'au moins un prix frais est arrivé.
        metrics = getattr(self, "_watch_metrics", None)
        if metrics is not None:
            metrics["quotes_ok"] = ok
            metrics["quotes_missing"] = missing
            if ok:
                metrics["last_quote_at"] = time.time()

        # UN enregistrement par token par tick, pas un par position.
        for token_address, price, liquidity in self.market.by_token():
            # None reste None : liquidité INCONNUE, pas nulle (faux RUG_PULL sinon).
            # Source étiquetée PAR PAIRE : après une re-résolution (migration),
            # l'ancienne paire n'est jamais comparée à la nouvelle.
            source = f"monitor:{token_pairs.get(token_address) or '-'}"
            self._monitor_sources[token_address] = source
            self.history.record_raw(
                token_address, price=price, liquidity=liquidity, source=source
            )
            self._last_prices[token_address] = price

        # PAR TOKEN, PAS PAR POSITION, et avant les sorties normales : un
        # créateur qui distribue concerne les sept bras à la fois, et la
        # mesure coûte deux appels RPC qu'il serait absurde de payer sept
        # fois. Même raisonnement que le prix partagé juste au-dessus.
        self._watch_dev_dumps()
        self._watch_copy_sells()

        for arm in self.arms:
            self._monitor_arm(arm, verbose=verbose)

    # ------------------------------------------------------- slow rug (dev)

    @property
    def dev_dump_panic_exit(self) -> bool:
        """La détection ferme-t-elle, ou se contente-t-elle d'alerter ?

        `true` par défaut : une distribution du créateur est la seule cause de
        perte que ni le stop loss ni le trailing n'anticipent — ils SUIVENT la
        descente qu'elle provoque. Le mettre à `false` garde la mesure et
        l'alerte sans jamais fermer, le temps de juger les seuils sur le
        journal produit (ils ne sont calibrés sur rien, voir
        `src/core/dev_watchdog.py`).
        """
        return bool(self.params.get("risk_rules.dev_dump_panic_exit", True))

    def _watch_dev_dumps(self) -> None:
        """Contrôle le solde du créateur des tokens détenus, et agit s'il chute.

        Ne casse jamais le monitoring : une panne Helius rend « non mesuré »,
        et une position sans mesure reste ouverte sous ses règles normales.
        """
        tenus = {p.token_address for p in self._open_positions()}
        # Hygiène mémoire : un token dont plus rien n'est ouvert n'a plus de
        # ligne de base à conserver, sur un processus qui tourne des semaines.
        for oublie in [t for t in self.dev_watchdog.tracked_tokens() if t not in tenus]:
            self.dev_watchdog.forget(oublie)

        for token_address in tenus:
            if self.dev_watchdog.already_flagged(token_address):
                continue
            try:
                verdict = self.dev_watchdog.check(token_address)
            except Exception as exc:  # noqa: BLE001
                print(f"[DevWatchdog] {token_address[:8]} indisponible : {exc}")
                continue
            if not verdict.dumping:
                continue

            symbole = next(
                (p.symbol for p in self._open_positions()
                 if p.token_address == token_address),
                token_address[:8],
            )
            print(f"🚨 SLOW RUG {symbole} — {verdict.reason}")
            self.telegram.send(
                f"🚨 <b>Distribution du créateur</b> — ${symbole}\n{verdict.reason}\n"
                + ("Fermeture de toutes les positions." if self.dev_dump_panic_exit
                   else "Observation seule (dev_dump_panic_exit désactivé).")
            )
            if self.dev_dump_panic_exit:
                self._force_exit_token(token_address, "DEV_DUMP")

    # ------------------------------------------------- copy_smart (test)

    def _copy_smart_gate(self, candidate, arm):
        """(ok, raison, wallets) — signal de copie + garde « déjà trop monté »."""
        rules = copy_smart.rules_from(arm.params.get("entry_rules", {}) or {})
        listed = self.copy_list.refresh(self.wallets, self.journal, self.shadow, rules)
        listed = copy_smart.merge_manual(listed, self.copy_manual)
        if not listed:
            return False, "liste de smart wallets vide", None
        trades = self._copy_trades()
        if trades is None:
            return False, "flux GMGN indisponible", None
        buys = copy_smart.recent_copy_buys(
            trades, listed, float(rules["copy_smart_window_minutes"]),
        )
        hit = buys.get(candidate.token_address)
        if not hit:
            return False, "aucun achat récent d'un wallet de la liste", None
        change_1h = getattr(candidate, "price_change_1h", None) or 0.0
        if change_1h > float(rules["copy_smart_max_change_1h_pct"]):
            return False, f"déjà +{change_1h:.0f}% sur 1h", None
        # Filtre « token copieur » (2026-10-29, accord Killian) : ticker/nom qui
        # reprend ou imite un token connu ou déjà vu. Local, zéro appel.
        if rules["copy_smart_copycat_filter"]:
            why = self.copy_list.copycats.check(
                getattr(candidate, "symbol", ""), getattr(candidate, "name", ""),
                candidate.token_address, getattr(candidate, "liquidity_usd", 0.0) or 0.0,
                float(rules["copy_smart_copycat_similarity"]),
                int(rules["copy_smart_copycat_min_len_fuzzy"]),
            )
            if why:
                return False, why, None
        return True, "", set(hit["wallets"])

    def _copy_trades(self, refresh: bool = True):
        """Flux GMGN (cache 120 s) + trades des wallets manuels (RPC public, 60 s).

        `refresh=False` (tick de surveillance) : lit seulement les caches et
        relance la relève GMGN dans un fil à part si elle est périmée. Le CLI
        GMGN a 30 s de délai max : il ne doit jamais retarder SL, TP ou rug.
        """
        trades = []
        gmgn_ok = bool(self.pipeline.gmgn and self.pipeline.gmgn.enabled)
        if gmgn_ok:
            if refresh:
                trades.extend(self.pipeline.gmgn.fetch_smart_money_trades() or [])
            else:
                trades.extend(self._gmgn_trades_cached())
        if self.copy_manual:
            # Lecture du cache seulement (liste vide = pas de relève) : la
            # relève RPC tourne dans son propre fil, voir `_poll_manual_wallets`.
            trades.extend(self.copy_watcher.trades(()))
        elif not gmgn_ok:
            return None
        return trades

    def _gmgn_trades_cached(self) -> list:
        """Dernier instantané smart money GMGN, sans jamais attendre le CLI."""
        gmgn = self.pipeline.gmgn
        snapshot = getattr(gmgn, "_smart_money_snapshot", None)
        stale = not snapshot or time.time() - snapshot[0] >= GMGN_CACHE_TTL_SECONDS
        fil = getattr(self, "_gmgn_refresh_thread", None)
        if stale and (fil is None or not fil.is_alive()):
            fil = threading.Thread(
                target=gmgn.fetch_smart_money_trades, name="gmgn-refresh", daemon=True
            )
            self._gmgn_refresh_thread = fil
            fil.start()
        return list(snapshot[1]) if snapshot else []

    def _poll_manual_wallets(self) -> None:
        """Relève RPC des wallets manuels dans un fil à part, jamais bloquante.

        Jusqu'à 15 signatures par wallet, un `getTransaction` chacune, 8 s de
        délai max par appel : faite dans le tick de surveillance, elle pouvait
        retarder SL, TP et détection de rug de toutes les positions. Le tick
        lit le cache ; la relève suivante part quand la précédente a fini.
        """
        fil = getattr(self, "_manual_poll_thread", None)
        if fil is not None and fil.is_alive():
            return
        if time.time() - getattr(self.copy_watcher, "_last_poll", 0.0) < getattr(
            self.copy_watcher, "poll_sec", 60.0
        ):
            return
        fil = threading.Thread(
            target=self.copy_watcher.trades,
            args=(list(self.copy_manual),),
            name="copy-manual-poll",
            daemon=True,
        )
        self._manual_poll_thread = fil
        fil.start()

    def _watch_copy_sells(self) -> None:
        """Sortie du bras `copy_smart` dès qu'un wallet copié revend le token."""
        if self.copy_manual:
            # Lecture des wallets manuels à CHAQUE tick (throttle 60 s), même
            # quand aucun candidat n'atteint la porte de copie.
            self._poll_manual_wallets()
        for arm in self.arms:
            if not arm.params.get("entry_rules.copy_smart_enabled", False):
                continue
            rules = copy_smart.rules_from(arm.params.get("entry_rules", {}) or {})
            if not rules["copy_smart_exit_on_wallet_sell"] or not arm.portfolio.positions:
                continue
            trades = self._copy_trades(refresh=False)
            if trades is None:
                return
            with _books_lock(self):
                position_ids = list(arm.portfolio.positions)
            for position_id in position_ids:
                with _books_lock(self):
                    position = arm.portfolio.positions.get(position_id)
                if position is None:
                    continue
                src = self.copy_sources.get(arm.name, position.token_address)
                if not src:
                    continue  # source inconnue : SL/TP normaux uniquement
                seller = copy_smart.copied_wallet_sold(
                    trades, position.token_address, src["wallets"], src["since_ts"]
                )
                if not seller:
                    continue
                price, _ = self.market.get(
                    position.chain, position.pair_address, position.token_address
                )
                if price is None or not math.isfinite(price):
                    continue  # pas de prix : on réessaie au tick suivant
                row = self._close_at_price(
                    arm.portfolio, position, price, "COPY_WALLET_SOLD"
                )
                if not row:
                    continue
                self.copy_sources.forget(arm.name, position.token_address)
                self.funnel.record_exit(
                    arm.name, position, price, "COPY_WALLET_SOLD", row["pnl_pct"], None
                )
                print(
                    f"  💰{arm.label} COPY_WALLET_SOLD ({seller[:6]}…) | {position.symbol} "
                    f"| {row['pnl_pct']:+.1f}% | {row['pnl_usd']:+.2f} $"
                )
                self._measure_hold(arm, position, row)
                self._after_trade_closed(arm)

    def _force_exit_token(self, token_address: str, reason: str) -> None:
        """Sortie totale sur TOUS les bras détenant ce token.

        Passe par `force_close`, donc par le même mesureur de coût réel que
        les sorties normales : un panic close arrive précisément quand le
        slippage est le pire, c'est le dernier endroit où laisser le P&L au
        prix nu.
        """
        for arm in self.arms:
            for position_id in list(arm.portfolio.positions):
                position = arm.portfolio.positions.get(position_id)
                if position is None or position.token_address != token_address:
                    continue
                price, _ = self.market.get(
                    position.chain, position.pair_address, position.token_address
                )
                if price is None or not math.isfinite(price):
                    # Sans prix, fermer inventerait un P&L. La position reste
                    # ouverte et le token reste signalé : le prochain tick
                    # avec un prix la fermera.
                    print(
                        f"[DevWatchdog]{arm.label} {position.symbol} : prix "
                        f"indisponible, fermeture reportée"
                    )
                    self.dev_watchdog.unflag(token_address)
                    continue
                row = self._close_at_price(arm.portfolio, position, price, reason)
                if not row:
                    continue
                self.funnel.record_exit(
                    arm.name, position, price, reason, row["pnl_pct"], None
                )
                print(
                    f"  💰{arm.label} {reason} | {position.symbol} "
                    f"| {row['pnl_pct']:+.1f}% | {row['pnl_usd']:+.2f} $"
                )
                self._measure_hold(arm, position, row)
                self._after_trade_closed(arm)

    def _watch_tick(self) -> None:
        """Un passage de surveillance. Appelé par le fil, jamais par le scan."""
        if not _running:
            return
        started = time.monotonic()
        try:
            # Les appels Helius de ce fil passent devant ceux du scan.
            with use_watch_priority():
                self._monitor_positions(verbose=False)
                self._publish_state()
        finally:
            self._watch_metrics["last_tick_sec"] = round(time.monotonic() - started, 3)

    def _monitor_interval_seconds(self) -> float:
        """Cadence réelle : le réglage, allongé si le budget de paires est dépassé.

        Le plafond de 180 requêtes/minute est inchangé. Plus de paires =
        un tick plus lent, pas plus d'appels.
        """
        base = float(self.params.get("scan.monitor_interval_seconds", 20) or 20)
        pairs = len({
            self.market.key(p.chain, p.pair_address, p.token_address)
            for p in self._open_positions()
        })
        interval = float(effective_monitor_interval(base, pairs))
        if interval > base and interval != self._last_stretched_interval:
            print(
                f"[Monitor] {pairs} paires — tick allongé à {interval}s "
                f"(budget {MONITOR_BUDGET_PER_MIN} req/min)"
            )
            self._last_stretched_interval = interval
        elif interval <= base:
            self._last_stretched_interval = None
        return interval

    def _prepare_exit(self, portfolio, position, price, liquidity, drop):
        """Mesure ce que la sortie va coûter, sans toucher aux livres.

        Le réseau (devis de sortie, devis d'ajout) est ici. L'application
        est ensuite faite sous le verrou, qui revérifie la taille.
        """
        marked = update_water_marks(position, price)
        actions = evaluate_exits(marked, price, drop)
        final_cost = PREPARED_UNSET
        scale_check = PREPARED_UNSET
        scale_add = None
        if any(action.is_final for action in actions) and portfolio.exit_fee_measurer is not None:
            final_cost = portfolio.quote_final_cost(position, price)
        elif (
            not actions
            and portfolio.scale_in_checker is not None
            and scale_in_due(position, price)
        ):
            with _books_lock(self):
                capital = portfolio.capital
                last = portfolio._scale_in_attempt_at.get(position.id)
            if last is None or time.time() - last >= SCALE_IN_RETRY_SECONDS:
                scale_add = min(position.scale_in_pending_usd, max(0.0, capital))
                try:
                    scale_check = portfolio.scale_in_checker(
                        position, scale_add, price, liquidity
                    )
                except Exception:  # noqa: BLE001
                    scale_check = None
        entry_quote = PREPARED_UNSET
        if (
            scale_add
            and getattr(position, "entry_cost_version", "") == ENTRY_COST_VERSION
            and getattr(position, "entry_impact_charged", False)
            and portfolio.exit_fee_measurer is not None
        ):
            entry_quote = portfolio._measure_entry_leg(position.token_address, scale_add)
        return (
            position.remaining_fraction,
            position.size_usd,
            final_cost,
            scale_check,
            scale_add,
            entry_quote,
        )

    def _close_at_price(self, portfolio, position, price, reason):
        """Ferme une position. Le devis est pris avant le verrou.

        Si la position a changé pendant le devis (vente partielle du fil de
        surveillance), on reprend ce qui reste et on redemande un devis.
        Un seul nouvel essai : /STOP ne doit pas laisser le reliquat ouvert.
        """
        for _attempt in range(2):
            cost = PREPARED_UNSET
            if portfolio.exit_fee_measurer is not None and quote_is_usable(price):
                cost = portfolio.quote_final_cost(position, price)
            with _books_lock(self):
                live = portfolio.positions.get(position.id)
                if live is None:
                    return None
                if (
                    live.remaining_fraction != position.remaining_fraction
                    or live.size_usd != position.size_usd
                ):
                    position = live
                    continue
                return portfolio.force_close(position.id, price, reason, exit_cost=cost)
        return None

    def _close_worthless(self, arm, position, reason: str):
        """Ferme à -100 % une position sans prix, sous le verrou des livres.

        Ne lève jamais : la boucle de surveillance ne doit pas mourir pour une
        position qu'on n'arrive pas à clore (elle sera retentée au tick suivant).
        """
        try:
            with _books_lock(self):
                row = arm.portfolio.close_worthless(position.id, reason)
            if row:
                try:
                    self.funnel.record_exit(
                        arm.name, position, 0.0, row["exit_reason"],
                        row["pnl_pct"], None,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[Monitor]{arm.label} entonnoir : sortie sans prix non notée ({exc})")
                try:
                    self._after_trade_closed(arm)
                except Exception as exc:  # noqa: BLE001
                    # La clôture est déjà bookée : une erreur d'apprentissage
                    # ne doit pas passer pour un échec de fermeture.
                    print(f"[Monitor]{arm.label} apprentissage après clôture : {exc}")
            return row
        except Exception as exc:  # noqa: BLE001
            print(
                f"[Monitor]{arm.label} {position.symbol} : fermeture sans prix "
                f"impossible ({type(exc).__name__}: {exc}), nouvel essai au prochain tick"
            )
            return None

    def _monitor_arm(self, arm, verbose: bool = True) -> None:
        portfolio = arm.portfolio
        with _books_lock(self):
            position_ids = list(portfolio.positions)
        for position_id in position_ids:
            with _books_lock(self):
                position = portfolio.positions.get(position_id)
            if position is None:
                continue

            price, liquidity = self.market.get(
                position.chain, position.pair_address, position.token_address
            )
            # NaN/inf passeraient tous les seuils de sortie sans jamais les
            # déclencher (une comparaison avec NaN est toujours False en
            # Python). <= 0 non plus n'est pas un prix. On ne reprend pas
            # `_last_prices` : c'est l'affichage, pas un prix exécutable.
            if not quote_is_usable(price):
                # Avec un prix, le time stop l'aurait déjà fermée : encore
                # ouverte au-delà du délai de grâce, elle est bloquée sans
                # prix. La règle ne lit que `entry_time` (persisté), donc elle
                # tient après un redémarrage.
                age = position.duration_minutes()
                limite = position.max_hold_time_minutes + ZOMBIE_GRACE_MINUTES
                if age >= limite:
                    row = self._close_worthless(
                        arm, position, "ZOMBIE (sans prix depuis le time stop)"
                    )
                    if row:
                        print(
                            f"[Monitor]{arm.label} {position.symbol} : aucun prix depuis "
                            f"{age:.0f} min (time stop {position.max_hold_time_minutes:.0f} "
                            f"+ grâce {ZOMBIE_GRACE_MINUTES} min), fermée à -100 %"
                        )
                elif verbose:
                    print(
                        f"[Monitor]{arm.label} {position.symbol} : prix indisponible, "
                        f"position gardée"
                    )
                continue

            drop = self.history.liquidity_drop_pct(
                position.token_address,
                window_seconds=RUG_WINDOW_SECONDS,
                source=self._monitor_sources.get(position.token_address, "monitor"),
                confirm_readings=RUG_CONFIRM_READINGS,
                price_crash_pct=RUG_PRICE_CRASH_PCT,
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

            # Devis HORS verrou. L'application (sortie, capital, journal)
            # reprend le verrou et revérifie que la position est encore la
            # même : un devis calculé pour une autre taille ne sort pas.
            remaining, size, final_cost, scale_check, scale_add, entry_quote = self._prepare_exit(
                portfolio, position, price, liquidity, drop
            )
            with _books_lock(self):
                live = portfolio.positions.get(position_id)
                if (
                    live is None
                    or live.remaining_fraction != remaining
                    or live.size_usd != size
                ):
                    continue
                rows = portfolio.update(
                    position_id, price, drop, liquidity_usd=liquidity,
                    prepared_final_cost=final_cost,
                    prepared_scale_check=scale_check,
                    prepared_scale_add=scale_add,
                    prepared_entry_quote=entry_quote,
                )
            for row in rows:
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
                gap = sample_gap_line(self.history.snapshots(position.token_address))
                if gap:
                    print(f"  ⏱️{arm.label} {position.symbol} | {gap}")
        with _books_lock(self):
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
        """Prix + liquidité. DexScreener d'abord (quota large), Jupiter puis Birdeye en repli.

        DexScreener exige `pair_address` — sans lui (positions ouvertes avant
        que la découverte du pair soit fiable, ou jamais renseigné) le prix
        n'était JAMAIS tenté : repli direct sur Birdeye, mort ce mois-ci
        (quota épuisé). Résultat mesuré : 7 positions bloquées à "prix
        indisponible" depuis leur ouverture, stop-loss jamais évalué, l'une
        d'elles réellement à -99% sans que rien ne le sache. Jupiter price
        par mint (pas de pair_address requis) comble ce trou — déjà actif et
        utilisé ailleurs dans le bot (`_economics_guard`), juste jamais câblé
        ici. Repéré 2026-08-17.
        """
        if position.pair_address:
            data = self.dex.get_pair_data(position.chain, position.pair_address)
            pairs = data.get("pairs") or []
            if pairs:
                pair = pairs[0]
                price = float(pair.get("priceUsd") or 0)
                # Paire pump.fun (courbe) ou migrée : pas de champ liquidité.
                # Inconnue ≠ 0 — renvoyer 0 déclenchait un faux RUG_PULL.
                raw_liquidity = (pair.get("liquidity") or {}).get("usd")
                liquidity = float(raw_liquidity) if raw_liquidity is not None else None
                if liquidity is None:
                    self._mark_pair_stale(position.token_address)
                if price > 0:
                    return price, liquidity
            else:
                # Paire disparue de DexScreener (migration) : à re-résoudre.
                self._mark_pair_stale(position.token_address)
        jupiter_price = self.jupiter.price(position.token_address) if self.jupiter else None
        if jupiter_price:
            return jupiter_price, None
        return self.birdeye.get_price(position.token_address), None

    def _mark_pair_stale(self, token_address: str) -> None:
        resolver = getattr(self, "pair_resolver", None)
        if resolver is not None:
            resolver.mark_stale(token_address)

    def _resolve_pairs(self, positions) -> int:
        """Résout/re-résout la paire des positions qui n'en ont pas (ou plus).

        UN appel DexScreener groupé (≤ 30 tokens) au plus par passage, et pas
        plus d'une tentative par token toutes les 120 s. Applique la paire à
        TOUTES les positions ouvertes du token, tous bras confondus : un seul
        prix/une seule liquidité par token dans l'historique.
        """
        resolver = getattr(self, "pair_resolver", None)
        if resolver is None:
            return 0
        wanted = [(p.token_address, p.chain, p.pair_address) for p in positions]
        try:
            changes = resolver.resolve(wanted)
        except Exception as exc:  # jamais bloquant pour le monitoring
            print(f"[PairResolver] ⚠️ {type(exc).__name__}")
            return 0
        updated = 0
        with _books_lock(self):
            for arm in self.arms:
                for pid, position in list(arm.portfolio.positions.items()):
                    new_pair = changes.get(position.token_address)
                    if new_pair and arm.portfolio.set_pair_address(pid, new_pair):
                        updated += 1
        for token, pair in changes.items():
            symbol = next((p.symbol for p in positions if p.token_address == token), token[:6])
            print(f"[PairResolver] {symbol} → paire {pair[:8]}… (liquidité lue au monitoring)")
        return updated

    def _current_price(self, token_address: str, chain: str, pair_address: Optional[str]):
        if pair_address:
            price = self.dex.get_price(chain, pair_address)
            if price:
                return price
        jupiter_price = self.jupiter.price(token_address) if self.jupiter else None
        if jupiter_price:
            return jupiter_price
        return self.birdeye.get_price(token_address)

    def _after_trade_closed(self, arm) -> None:
        """Réapprentissage APRÈS chaque trade, sur le journal DE CE BRAS.

        Chaque stratégie apprend de ses propres trades, avec ses propres
        bornes. Un plancher de `MIN_TRADES_PER_ARM` empêche d'ajuster sur un
        échantillon que le multi-bras a déjà divisé par sept.
        """
        if arm.learning is None:
            # Bras aux paramètres figés (`"learning": false` au manifeste).
            return
        changes = arm.learning.run(max_drawdown_pct=arm.portfolio.max_drawdown_pct)
        for change in changes:
            print(f"🧠{arm.label} {change}")
        # Seules les vraies décisions partent en notification : « en attente »
        # est une information de log, pas une alerte.
        #
        # TOUS LES BRAS, PAS SEULEMENT LE TÉMOIN. `arm.is_baseline` limitait
        # cette notification au seul témoin — les cinq autres ajustaient
        # leurs paramètres sans que rien n'en sorte sur Telegram. Même défaut
        # que les 16 sorties jamais notifiées, une couche plus profonde :
        # passe par `arm.portfolio.notifier` (donc par le reporter unique),
        # jamais par `self.telegram` en direct.
        decisions = [c for c in changes if not c.startswith("(en attente)")]
        notifier = getattr(arm.portfolio, "notifier", None)
        if decisions and notifier is not None:
            notifier.send_learning("\n".join(decisions))

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

            # SCORE V2 (opt-in, 2026-10-10) : filtre anti-pump APRÈS le seuil
            # alpha v1, pour que le bras reste un sous-ensemble exact du témoin.
            # `continue` et non `break` : la liste est triée par alpha v1, pas
            # par score v2. Désactivé (0) pour tous les bras sauf `score_v2`.
            min_prob_v2 = float(arm.params.get("entry_rules.score_v2_min_prob", 0) or 0)
            if min_prob_v2 > 0:
                ok_v2, prob_v2, why_v2 = score_v2_gate(candidate, min_prob_v2)
                self.funnel.record(
                    *trace, "score_v2", ok_v2, why_v2, {"prob": round(prob_v2, 4)}
                )
                if not ok_v2:
                    continue

            # COPIE SMART WALLETS (opt-in, 2026-10-29, bras `copy_smart`) : AVANT
            # l'anti-arnaque, pour ne dépenser le budget on-chain que sur des
            # tokens qu'un wallet de la liste vient d'acheter.
            copy_wallets = None
            if arm.params.get("entry_rules.copy_smart_enabled", False):
                ok_copy, why_copy, copy_wallets = self._copy_smart_gate(candidate, arm)
                self.funnel.record(*trace, "copy_smart", ok_copy, why_copy,
                                   {"wallets": sorted(copy_wallets or [])})
                if not ok_copy:
                    continue
                print(f"👥{arm.label} {candidate.symbol} — copie de {len(copy_wallets)} wallet(s)")

            # ANTI-ARNAQUE (opt-in, 2026-10-11, bras `anti_rug`) : même
            # placement que score v2 — sous-ensemble exact du témoin. Lit
            # l'instantané on-chain calculé AVANT la décision. Données pas
            # prêtes : `continue`, le token est réévalué au cycle suivant.
            if arm.params.get("entry_rules.anti_rug_enabled", False):
                snapshot = self.onchain.snapshot(candidate.token_address, time.time())
                if snapshot.get("onchain_status") in ("missing", "budget"):
                    # Jamais demandé (hors des 3 nouveaux par cycle) : mise en
                    # file non bloquante, pour le cycle suivant.
                    try:
                        self.onchain.request_many([candidate], priority=True)
                    except Exception:
                        pass
                rug = anti_rug_gate(
                    snapshot, candidate, arm.params.get("entry_rules", {}) or {}
                )
                self.funnel.record(
                    *trace, "anti_rug", rug.passed, rug.reason,
                    {"missing": rug.missing, "checks": rug.checks},
                )
                if not rug.passed:
                    print(f"🛡️{arm.label} {candidate.symbol} — {rug.reason}")
                    continue

            # `entry_rules.consensus_gate` (opt-in, bras test `consensus_af`,
            # 2026-11-02) : même quorum, mêmes votants, sans être votant.
            if arm.role == CONSENSUS or arm.params.get("entry_rules.consensus_gate", False):
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

            # Porte unique anticipée (sans économie / carnet) : court-circuite
            # avant l'analyse technique coûteuse. Le verdict définitif est
            # rejoué dans `_open_position` — chemin unique non contournable.
            sector = correlation.classify(candidate.symbol, candidate.name)
            # Sous le verrou des livres : le fil de surveillance peut fermer une
            # position pendant ce contrôle (itération de `positions`).
            with _books_lock(self):
                early_reject = pre_trade_check(
                    mode=self.mode,
                    candidate=candidate,
                    portfolio=arm.portfolio,
                    max_positions=max_positions,
                    fleet_exposure=self._fleet_exposure(),
                    size_usd=arm.portfolio.capital * MAX_POSITION_PCT_OF_CAPITAL,
                    max_sector_positions=self.max_sector_positions,
                    max_sector_exposure_pct=self.max_sector_exposure_pct,
                    live_allowed=(
                        self.learning.live_mode_allowed()
                        if self.mode == "LIVE" and self.learning is not None
                        else None
                    ),
                    risk_rules=self.params.get("risk_rules") or {},
                    max_score_alpha=arm.params.get("entry_rules.max_score_alpha"),
                    max_volume_liquidity_ratio=arm.params.get("entry_rules.max_volume_liquidity_ratio"),
                    require_market_data=False,
                )
            self.funnel.record(
                *trace, "pre_trade", not early_reject, early_reject or "",
                {"sector": sector, "phase": "early"},
            )
            if early_reject:
                print(f"⏸️ {arm.label} {candidate.symbol} — {early_reject}")
                if is_soft_reject(early_reject):
                    continue
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
                if copy_wallets and any(
                    p.token_address == candidate.token_address
                    for p in arm.portfolio.positions.values()
                ):
                    self.copy_sources.remember(arm.name, candidate.token_address, copy_wallets)

    def _make_scale_in_checker(self, arm):
        """Vérificateur d'ajout pour un bras à entrée progressive (`scaled_be`).

        Devis Jupiter réel (timeout court, sans réessai), sécurité et coût de
        sortie à la taille totale — voir `src/core/scale_in.py`. Seuils lus
        dans le document DU BRAS : liquidité minimale (copie du témoin) et
        `entry_rules.max_exit_cost_pct_entry`.
        """
        from src.core.scale_in import QUOTE_TIMEOUT_SECONDS, check_scale_in

        def checker(position, add_usd: float, observed_price: float, liquidity_usd):
            return check_scale_in(
                self.jupiter,
                position.token_address,
                add_usd=add_usd,
                total_usd=position.size_usd + add_usd,
                observed_price=observed_price,
                liquidity_usd=liquidity_usd,
                min_liquidity_usd=float(arm.params.get("filters.min_liquidity_usd", 0) or 0),
                max_exit_cost_pct=float(
                    arm.params.get("entry_rules.max_exit_cost_pct_entry", 0) or 0
                ),
                sol_price_fn=lambda: (
                    self.jupiter.price(SOL_MINT, timeout=QUOTE_TIMEOUT_SECONDS, max_retries=1)
                    if self.jupiter is not None else None
                ),
            )

        return checker

    def _measure_exit_cost(self, token_address: str, size_usd: float):
        """Coût réel d'une clôture, maintenant. Injecté dans les 6 `PaperPortfolio`.

        UN SEUL CALLABLE PARTAGÉ, pas une mesure par bras : le coût de sortie
        ne dépend pas de la stratégie qui a acheté, seulement du token et du
        montant. Voir `src/core/exit_fees.py` pour ce qui est mesuré et ce qui
        est une hypothèse documentée (le budget de CU du priority fee).
        """
        sol_price = self.jupiter.price(SOL_MINT) or 0.0
        note = getattr(self, "_note_sol", None)
        if note is not None:
            note(sol_price)
        return measure_exit_cost(self.jupiter, self.helius, token_address, size_usd, sol_price)

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
        note = getattr(self, "_note_sol", None)
        if note is not None:
            note(sol_price)
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

        # PLAFOND DE COÛT DE SORTIE ESTIMÉ (opt-in, 0/absent = désactivé ;
        # bras `scaled_be`). Refus NET, pas de réduction de taille : même
        # devis Jupiter que ci-dessus (aller-retour à la taille retenue, celui
        # que la sortie finale facture). Devis indisponible -> n'entre pas
        # en jeu (une donnée manquante ne rejette jamais).
        max_exit_cost = float(arm.params.get("entry_rules.max_exit_cost_pct_entry", 0) or 0)
        if max_exit_cost > 0 and cost is not None and cost > max_exit_cost:
            why = (
                f"coût de sortie estimé {cost:.1f}% > {max_exit_cost:.1f}% "
                f"(max_exit_cost_pct_entry) — pas d'entrée"
            )
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
        kline_source = None
        if self.gmgn and self.gmgn.enabled:
            rows = self.gmgn.kline(
                candidate.token_address, resolution=interval, lookback_seconds=3600
            )
            if rows:
                candles = from_gmgn_kline(rows)
                kline_source = "gmgn"
        if candles is None and self.birdeye.enabled:
            ohlcv = self.birdeye.get_ohlcv(
                candidate.token_address, interval=interval, lookback_seconds=3600
            )
            if ohlcv:
                candles = from_ohlcv(ohlcv)
                kline_source = "birdeye"
        if candles is not None and kline_source:
            # Déjà lues pour l'analyse technique : on les garde, sans nouvel appel.
            try:
                journal = self._research()
                if journal is not None:
                    journal.note_klines(candidate.token_address, candles, kline_source)
            except Exception:
                pass
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

    def _request_onchain_observation(self) -> None:
        """Met en file (non bloquant) les candidats du cycle, meilleurs d'abord.

        Pure observation : n'altère ni les candidats ni les évaluations.
        """
        try:
            seen: dict[str, Candidate] = {}
            # Les bras à source séparée (« mature ») ne consomment pas le
            # budget d'observation on-chain des autres.
            separate = {
                a.name for a in getattr(self, "arms", None) or []
                if getattr(a, "discovery", "main") != "main"
            }
            for name, evaluation in self._last_evaluations.items():
                if name in separate:
                    continue
                for candidate in evaluation.result.candidates:
                    seen.setdefault(candidate.token_address, candidate)
            ranked = sorted(
                seen.values(),
                key=lambda c: getattr(c, "alpha_score_absolute", None) or 0.0,
                reverse=True,
            )
            self.onchain.request_many(ranked)
        except Exception as exc:  # une mesure ne gêne jamais le cycle
            print(f"[OnChain] ⚠️ mise en file — {type(exc).__name__}")

    def _open_position(self, candidate: Candidate, arm) -> None:
        stats = arm.portfolio.stats()
        size = arm.portfolio.position_size(
            risk_per_trade=arm.params.get("risk_per_trade", 0.03),
            win_rate=stats["win_rate"] / 100,
            total_trades=stats["total_trades"],
        )
        # Dernière mesure carnet (peut réduire la taille). Le refus d'ouvrir
        # passe ensuite par `pre_trade_check` — porte unique avant `open`.
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

        jupiter_ok = bool(self.jupiter is not None and self.jupiter.enabled)
        price_ok = (getattr(candidate, "price_usd", None) or 0) > 0
        sellable: Optional[bool] = None
        if jupiter_ok:
            reason_eco = (verdict or {}).get("reason", "") if verdict else ""
            if size <= 0 and reason_eco.startswith("honeypot"):
                sellable = False
            elif verdict is not None:
                # La garde économique a interrogé Jupiter (honeypot + coût) :
                # si on arrive ici avec un verdict, la vendabilité a été vue.
                sellable = not reason_eco.startswith("honeypot")
        economics_reject = None
        if size <= 0:
            economics_reject = (verdict or {}).get("reason") or "économie non viable"

        # Sous le verrou des livres : le fil de surveillance peut fermer une
        # position pendant ce contrôle (itération de `positions`).
        with _books_lock(self):
            reject = pre_trade_check(
                mode=self.mode,
                candidate=candidate,
                portfolio=arm.portfolio,
                max_positions=arm.max_positions,
                fleet_exposure=self._fleet_exposure(),
                size_usd=size if size > 0 else arm.portfolio.capital * MAX_POSITION_PCT_OF_CAPITAL,
                max_sector_positions=self.max_sector_positions,
                max_sector_exposure_pct=self.max_sector_exposure_pct,
                economics_reject=economics_reject,
                live_allowed=(
                    self.learning.live_mode_allowed()
                    if self.mode == "LIVE" and self.learning is not None
                    else None
                ),
                jupiter_ok=jupiter_ok,
                sellable=sellable,
                price_ok=price_ok,
                risk_rules=self.params.get("risk_rules") or {},
                max_score_alpha=arm.params.get("entry_rules.max_score_alpha"),
                max_volume_liquidity_ratio=arm.params.get("entry_rules.max_volume_liquidity_ratio"),
                require_market_data=True,
            )
        self.funnel.record(
            *trace, "pre_trade", reject is None, reject or "",
            {"phase": "open", "size_usd": size},
        )
        if reject:
            print(f"⏸️ {arm.label} {candidate.symbol} — pre_trade : {reject}")
            return

        # `arm.params.data` et pas `self.params.data` : les règles de sortie
        # sont figées à l'entrée depuis le document DE CE BRAS.
        # Journal enrichi : instantané de ce qui était connu À LA DÉCISION
        # (aucun appel réseau). `round_trip_pct` = devis Jupiter aller-retour
        # de la porte économie, mesuré AVANT l'achat.
        try:
            entry_context = build_entry_context(
                candidate, (verdict or {}).get("round_trip_pct")
            )
        except Exception as exc:  # une mesure ne bloque jamais une entrée
            print(f"[Journal] ⚠️ contexte d'entrée — {type(exc).__name__}: {exc}")
            entry_context = {}
        # Signaux on-chain (Helius, calculés en arrière-plan AVANT la décision,
        # sinon null) et liens sociaux déclarés (DexScreener/GMGN, déjà en
        # mémoire). Aucun appel réseau ici.
        try:
            entry_context.update(self.onchain.snapshot(candidate.token_address, time.time()))
        except Exception as exc:
            print(f"[Journal] ⚠️ contexte on-chain — {type(exc).__name__}")
        try:
            entry_context.update(social_links(candidate))
            entry_context["social_score_source"] = SOCIAL_SCORE_SOURCE
        except Exception as exc:
            print(f"[Journal] ⚠️ contexte social — {type(exc).__name__}")
        # Le devis d'entrée est un appel réseau : hors du verrou, sinon la
        # surveillance des sorties attend la réponse.
        entry_quote = PREPARED_UNSET
        if (
            arm.portfolio.exit_fee_measurer is not None
            and getattr(arm.portfolio, "mode", "PAPER") == "PAPER"
        ):
            engaged = arm.portfolio._engaged_size(arm.params.data, size)
            entry_quote = arm.portfolio._measure_entry_leg(candidate.token_address, engaged)
        with _books_lock(self):
            position = arm.portfolio.open(
                candidate, arm.params.data, size, entry_context=entry_context,
                entry_quote=entry_quote,
            )
        # `position.size_usd` = taille réellement engagée (== `size` pour
        # tous les bras sauf entrée progressive, où c'est la fraction initiale).
        self.funnel.record(*trace, "entree", True, "", {"size_usd": position.size_usd})
        try:
            journal = self._research()
            if journal is not None:
                journal.note_entered(candidate.token_address)
        except Exception:
            pass
        self._measure_entry(position, candidate)
        cost = (verdict or {}).get("round_trip_pct")
        frais = f" | frais A/R {cost:.1f}%" if cost is not None else ""
        print(
            f"🟢 ENTRY{arm.label} [{self.mode}] | ${position.symbol} "
            f"| Score {position.alpha_score} | Size {position.size_usd:.2f} $ "
            + (
                f"(+{position.scale_in_pending_usd:.2f} $ à "
                f"+{position.scale_in_trigger_pct:.0f}%) "
                if position.scale_in_trigger_pct > 0
                else ""
            )
            + f"| Entry ${position.entry_price:.8f} | SL {position.stop_loss_pct}% "
            f"| TP1 +{position.take_profit_1}%{frais}"
        )

    def _research(self):
        """Journal de recherche, démarré au premier besoin. Jamais une décision."""
        journal = getattr(self, "_research_journal", None)
        if journal is not None or getattr(self, "_research_failed", False):
            return journal
        try:
            dex = getattr(self, "dex", None)
            fetch = getattr(dex, "try_tokens_snapshot", None) if dex is not None else None
            journal = ResearchJournal.load(settings.BASE_DIR, fetch=fetch)
            self._research_journal = journal
            journal.start()
            return journal
        except Exception as exc:
            self._research_failed = True
            print(f"[Research] ⚠️ journal non démarré — {type(exc).__name__}")
            return None

    def _note_sol(self, price: float) -> None:
        if not price:
            return
        try:
            journal = self._research()
            if journal is not None:
                journal.note_sol(price)
        except Exception:
            pass

    def _record_research(self) -> None:
        """Une ligne par jeton évalué. Les données sont déjà là : aucun appel."""
        try:
            journal = self._research()
            if journal is None or not journal.enabled:
                return
            gmgn = getattr(self, "gmgn", None)
            snap = getattr(gmgn, "_smart_money_snapshot", None) if gmgn is not None else None
            trades = snap[1] if snap else None
            onchain = getattr(self, "onchain", None)
            journal.record_cycle(
                cycle_id=self.cycle_count,
                evaluations=getattr(self, "_last_evaluations", {}) or {},
                thresholds={arm.name: arm.entry_threshold for arm in self.arms},
                funnel_rows=list(self.funnel.rows),
                onchain_lookup=onchain.snapshot if onchain is not None else None,
                trades=trades,
            )
        except Exception as exc:
            print(f"[Research] ⚠️ cycle non journalisé — {type(exc).__name__}")

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
            # LIGNE DE BASE DU SLOW RUG. Elle doit être prise ICI et pas au
            # premier contrôle : ce qu'on veut mesurer est « ce que le
            # créateur a cédé DEPUIS QU'ON EST ENTRÉ ». Une référence prise
            # trois minutes plus tard aurait déjà avalé la première vague de
            # distribution et l'aurait comptée comme normale.
            ("dev_watchdog", lambda: self.dev_watchdog.establish_baseline(
                position.token_address
            )),
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
        """Remplacé par `TelegramReporterAgent`. Conservé comme point d'entrée.

        Le digest par bras cadencé à 4 h était l'une des causes des 16 sorties
        à +50 % ou plus jamais notifiées : son compteur repartait à zéro à
        chaque redémarrage, et il n'est jamais parti. Le reporter regroupe
        maintenant par NATURE d'événement, avec sa propre cadence.
        """
        self.reporter.flush_batch(force=True)
        self.reporter.report_periodic(self.arms, force=True)

    def _print_dashboard(self) -> None:
        """Deux blocs, et l'ordre compte.

        LA FLOTTE D'ABORD. Ce panneau n'affichait que le témoin, alors sans
        trade depuis des heures : il montrait « 4 gagnants sur 39 » pendant que
        les six autres bras en accumulaient 27 sur 93. Les gains étaient
        enregistrés — c'est la lecture qui regardait le mauvais portefeuille.

        LE TÉMOIN QUAND MÊME, en dessous et étiqueté. C'est la référence
        historique pour `verdict_vs_reference` (sa série de trades est
        continue depuis avant le multi-bras) — pas un bras figé : depuis le
        2026-08-03 il apprend comme les six autres. Le sortir du panneau
        ferait perdre le point de comparaison qui donne un sens aux chiffres
        de la flotte.
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
            f"│ témoin : {stats['equity']:.2f} $ | "
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
