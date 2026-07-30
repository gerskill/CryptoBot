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
from src.analysis.technical import PriceHistory, TechnicalVerdict, analyze, from_ohlcv
from src.apis.birdeye import BirdeyeAPI
from src.apis.dexscreener import DexScreenerAPI
from src.apis.gmgn import GmgnAPI
from src.apis.helius import HeliusAPI
from src.apis.rugcheck import RugCheckAPI
from src.apis.twitter import TwitterAPI
from src.core.cache import TokenCache
from src.core.journal import TradeJournal
from src.core.learning import LearningEngine
from src.core.lock import InstanceLock
from src.core.models import Candidate, ScanResult
from src.core.params import ParamsStore
from src.core.portfolio import PaperPortfolio
from src.core.shadow import ShadowTracker
from src.core.state import StateWriter, candidate_to_dict, position_to_dict
from src.notify.telegram import TelegramNotifier
from src.pipeline import ScanPipeline

PAPER_DEFAULT_CAPITAL = 1000.0
DASHBOARD_EVERY_SECONDS = 300
SUMMARY_EVERY_SECONDS = 4 * 3600
RUG_WINDOW_SECONDS = 120

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
        self.pipeline = ScanPipeline(
            params=self.params,
            cache=self.cache,
            dex=self.dex,
            helius=HeliusAPI(settings.HELIUS_API_KEY),
            rugcheck=RugCheckAPI(),
            birdeye=self.birdeye,
            twitter=TwitterAPI(settings.TWITTER_BEARER_TOKEN),
            gmgn=self.gmgn,
            history=self.history,
        )

        self.journal = TradeJournal(settings.TRADES_LOG_PATH)
        self.shadow = ShadowTracker(settings.SHADOW_LOG_PATH)
        self.state = StateWriter(settings.STATE_PATH)
        self.telegram = TelegramNotifier(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)
        self.telegram.drain_pending()
        self.learning = LearningEngine(self.params, self.journal, shadow=self.shadow)

        self.mode = self.params.get("mode", "PAPER")
        self.portfolio = PaperPortfolio(
            capital=self._starting_capital(),
            journal=self.journal,
            notifier=self.telegram,
            mode=self.mode,
            positions_path=settings.POSITIONS_PATH,
            cooldown_hours=self.params.get("risk_rules.cooldown_hours", 2),
            losses_trigger=self.params.get("risk_rules.consecutive_losses_trigger", 3),
        )
        if self.mode == "LIVE" and self.portfolio.cooldown_hours <= 0:
            print(
                "⚠️ Mode LIVE avec cooldown désactivé — après 3 pertes consécutives "
                "le bot continuera d'ouvrir des positions. Remets "
                "risk_rules.cooldown_hours à 2 dans params.json."
            )

        self.cycle_count = 0
        self.paused = False
        self._last_prices: dict[str, float] = {}
        self._next_scan_at = 0.0
        self._last_scan: Optional[ScanResult] = None
        self._last_dashboard = 0.0
        self._last_summary = time.time()

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
        monitor_interval = self.params.get("scan.monitor_interval_seconds", 20)
        deadline = time.monotonic() + duration
        next_monitor = time.monotonic() + monitor_interval

        while _running and time.monotonic() < deadline:
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
            if self.portfolio.positions and time.monotonic() >= next_monitor:
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

        result = self.pipeline.run_cycle()
        self._print_result(result)
        self._track_rejections(result)

        if not self.paused:
            self._try_entries(result)

        self.cache.purge()
        self._publish_state(result)
        self._periodic_reports()

    def _publish_state(self, result: Optional[ScanResult] = None) -> None:
        """Écrit l'état courant pour le dashboard. Ne doit jamais casser la boucle."""
        try:
            if result is not None:
                self._last_scan = result
            scan = self._last_scan

            positions = []
            for position in self.portfolio.positions.values():
                price = self._last_prices.get(position.token_address)
                positions.append(position_to_dict(position, price))

            self.state.write(
                {
                    "mode": self.mode,
                    "paused": self.paused,
                    "cycle": self.cycle_count,
                    "interval_seconds": self.params.get("scan.interval_seconds", 90),
                    "next_scan_at": self._next_scan_at,
                    "stats": self.portfolio.stats(),
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
                    "shadow_by_family": self.shadow.missed_rate_by_family(min_sample=1),
                    "entry_threshold": self.params.get("scan.alpha_score_entry_threshold", 75),
                    "live_allowed": self.learning.live_mode_allowed(),
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

    def _track_rejections(self, result: ScanResult) -> None:
        """Met les rejets sous observation et juge ceux arrivés à terme.

        Sans ça, le bot n'apprend que de ses propres trades et ne peut jamais
        découvrir qu'un filtre est trop strict.
        """
        added = self.shadow.record_rejections(result.rejected)

        pending = self.shadow.pending_review()
        if pending:
            for address, pairs in self.dex.get_tokens_data(pending).items():
                if pairs:
                    price = float(pairs[0].get("priceUsd") or 0)
                    self.shadow.update_price(address, price)

        for verdict in self.shadow.expire():
            icon = "💸" if verdict.would_have_won else "  "
            print(
                f"{icon} SHADOW {verdict.symbol} rejeté ({verdict.reason_family}) "
                f"→ pic {verdict.peak_gain_pct:+.0f}% en {verdict.minutes_tracked:.0f} min"
            )

        if added or pending:
            stats = self.shadow.stats
            print(
                f"[Shadow] +{added} sous observation | {stats['tracked']} suivis "
                f"| {stats['judged']} jugés"
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
        """Panic button : ferme tout au prix courant, puis passe en veille."""
        print("🛑 PANIC EXIT — fermeture de toutes les positions")
        self.telegram.send("🛑 <b>/STOP reçu</b> — fermeture de toutes les positions.")
        for position_id in list(self.portfolio.positions):
            position = self.portfolio.positions[position_id]
            price = self._current_price(
                position.token_address, position.chain, position.pair_address
            )
            if price is None:
                print(f"  ⚠️ {position.symbol} : prix indisponible, position NON fermée")
                continue
            row = self.portfolio.force_close(position_id, price, "PANIC_STOP")
            if row:
                print(f"  💰 {position.symbol} fermé à {row['pnl_pct']:+.1f}%")
                self._after_trade_closed()
        self.paused = True

    def _monitor_positions(self, verbose: bool = True) -> None:
        """`verbose=False` pour les ticks rapprochés : on ne loggue que les sorties."""
        if not self.portfolio.positions:
            return
        for position_id in list(self.portfolio.positions):
            position = self.portfolio.positions.get(position_id)
            if position is None:
                continue

            price, liquidity = self._current_market(position)
            if price is None:
                if verbose:
                    print(f"[Monitor] {position.symbol} : prix indisponible, position gardée")
                continue

            self._last_prices[position.token_address] = price
            self.history.record_raw(position.token_address, price=price, liquidity=liquidity or 0)
            drop = self.history.liquidity_drop_pct(
                position.token_address, window_seconds=RUG_WINDOW_SECONDS
            )
            if verbose:
                print(
                    f"[Monitor] {position.symbol:>10} | ${price:.8f} "
                    f"| P&L {position.pnl_pct(price):+.1f}% "
                    f"| {position.duration_minutes():.0f} min | reste "
                    f"{position.remaining_fraction * 100:.0f}%"
                )

            for row in self.portfolio.update(position_id, price, drop):
                print(
                    f"  💰 {row['exit_reason']} | {row['pnl_pct']:+.1f}% | {row['pnl_usd']:+.2f} $"
                )
                if row["is_final_exit"]:
                    self._after_trade_closed()

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

    def _after_trade_closed(self) -> None:
        """Étape 6 : réapprentissage après chaque trade clôturé."""
        changes = self.learning.run(max_drawdown_pct=self.portfolio.max_drawdown_pct)
        for change in changes:
            print(f"🧠 {change}")
        if changes:
            self.telegram.send("🧠 <b>Paramètres ajustés</b>\n" + "\n".join(changes))

    def _try_entries(self, result: ScanResult) -> None:
        threshold = self.params.get("scan.alpha_score_entry_threshold", 75)
        max_positions = self.params.get("max_concurrent_positions", 3)

        for candidate in result.candidates:
            # Le seuil porte sur le score ABSOLU : le score classant contient
            # 40% de rang dans le batch, et le meilleur d'un lot médiocre le
            # franchirait mécaniquement.
            if candidate.alpha_score_absolute < threshold:
                break
            blocker = self.portfolio.can_open(candidate, max_positions)
            if blocker:
                print(f"⏸️  {candidate.symbol} qualifié mais bloqué : {blocker}")
                break

            verdict = self._technical_verdict(candidate)
            print(
                f"\n🎯 {candidate.symbol} absolu {candidate.alpha_score_absolute} ≥ {threshold} "
                f"(classement {candidate.alpha_score}) → analyse technique : {verdict.label}"
            )
            for reason in verdict.reasons:
                print(f"     · {reason}")
            if verdict.passed:
                self._open_position(candidate)

    def _technical_verdict(self, candidate: Candidate) -> TechnicalVerdict:
        """Vraies bougies Birdeye si disponibles, sinon reconstruction.

        1 requête Birdeye par candidat évalué — seuls ceux au-dessus du seuil
        d'entrée arrivent ici, donc 1 à 3 requêtes par cycle.
        """
        candles = None
        if self.birdeye.enabled:
            interval = self.params.get("entry_rules.ohlcv_interval", "1m")
            ohlcv = self.birdeye.get_ohlcv(
                candidate.token_address, interval=interval, lookback_seconds=3600
            )
            if ohlcv:
                candles = from_ohlcv(ohlcv)

        return analyze(
            candidate,
            self.history,
            max_initial_dump_pct=self.params.get("entry_rules.max_initial_dump_pct", -50),
            candle_seconds=self.params.get("entry_rules.candle_seconds", 180),
            required_candles=self.params.get("entry_rules.required_candles", 3),
            candles=candles,
            structure_mode=self.params.get("entry_rules.structure_mode", "balanced"),
        )

    def _open_position(self, candidate: Candidate) -> None:
        stats = self.portfolio.stats()
        size = self.portfolio.position_size(
            risk_per_trade=self.params.get("risk_per_trade", 0.03),
            win_rate=stats["win_rate"] / 100,
            total_trades=stats["total_trades"],
        )
        position = self.portfolio.open(candidate, self.params.data, size)
        print(
            f"🟢 ENTRY [{self.mode}] | ${position.symbol} | Score {position.alpha_score} "
            f"| Size {size:.2f} $ | Entry ${position.entry_price:.8f} "
            f"| SL {position.stop_loss_pct}% | TP1 +{position.take_profit_1}%"
        )

    def _periodic_reports(self) -> None:
        now = time.time()
        if now - self._last_dashboard >= DASHBOARD_EVERY_SECONDS:
            self._print_dashboard()
            self._last_dashboard = now
        if now - self._last_summary >= SUMMARY_EVERY_SECONDS:
            self.telegram.send_summary(self.portfolio.stats())
            self._last_summary = now

    def _print_dashboard(self) -> None:
        stats = self.portfolio.stats()
        history = self.params.get("learning.parameter_adjustment_history", []) or []
        last = history[-1] if history else None
        allowed, why = self.learning.live_mode_allowed()

        print(f"\n┌{'─' * 60}┐")
        print(f"│ MEMECOIN ALPHA LOOP — DASHBOARD [{stats['mode']}]".ljust(61) + "│")
        print(f"├{'─' * 60}┤")
        print(
            f"│ Capital : {stats['equity']:.2f} $ | P&L : {stats['total_pnl_usd']:+.2f} $".ljust(61)
            + "│"
        )
        print(f"│ Positions ouvertes : {stats['open_positions']}".ljust(61) + "│")
        print(
            f"│ Win rate : {stats['win_rate']}% | Profit factor : {stats['profit_factor']}".ljust(61)
            + "│"
        )
        print(
            f"│ Max drawdown : {stats['max_drawdown_pct']}% | Trades : {stats['total_trades']}".ljust(61)
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
            f"{'=' * 78}"
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
