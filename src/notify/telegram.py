"""Module Telegram — alertes push et commandes de contrôle.

Deux usages :
  - notifications sortantes (entrée, sortie, résumé, erreurs)
  - commandes entrantes via `getUpdates` en long polling léger : `/STOP`
    (panic button), `/STATUS`, `/RESUME`

Aucune notification ne doit pouvoir casser la boucle : toute erreur réseau est
avalée et logguée. Le trading ne dépend jamais de la disponibilité de Telegram.
"""

import time
from typing import Any, Optional

import requests

BASE_URL = "https://api.telegram.org"
REQUEST_TIMEOUT = 10


class TelegramNotifier:
    """Client Telegram. Sans token/chat_id, toutes les méthodes sont neutres."""

    def __init__(self, bot_token: Optional[str], chat_id: Optional[str]):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)
        self.session = requests.Session()
        self._update_offset: Optional[int] = None

    def _api(self, method: str, params: dict[str, Any], timeout: int = REQUEST_TIMEOUT) -> Optional[dict]:
        if not self.enabled:
            return None
        try:
            response = self.session.get(
                f"{BASE_URL}/bot{self.bot_token}/{method}", params=params, timeout=timeout
            )
            body = response.json()
            if not body.get("ok"):
                print(f"[Telegram] {method} : {body.get('description')}")
                return None
            return body.get("result")
        except requests.exceptions.RequestException as exc:
            print(f"[Telegram] {method} erreur réseau : {exc}")
        except ValueError as exc:
            print(f"[Telegram] {method} JSON invalide : {exc}")
        return None

    # ------------------------------------------------------------- sortant

    def send(self, text: str, silent: bool = False) -> bool:
        result = self._api(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "disable_notification": silent,
            },
        )
        return result is not None

    def send_entry(self, position: Any) -> bool:
        return self.send(
            f"🟢 <b>ENTRY [{position.mode}]</b> | ${position.symbol}\n"
            f"Score alpha : <b>{position.alpha_score}</b>\n"
            f"Taille : {position.size_usd:.2f} $ | Prix : ${position.entry_price:.8f}\n"
            f"SL : {position.stop_loss_pct}% | TP1 : +{position.take_profit_1}% "
            f"| TP2 : +{position.take_profit_2}%\n"
            f"Liq : ${position.liquidity_at_entry:,.0f} | Holders : {position.holders_at_entry}"
        )

    def send_exit(self, position: Any, pnl_pct: float, reason: str, duration_min: float) -> bool:
        icon = "🔴" if pnl_pct < 0 else "🟢"
        return self.send(
            f"{icon} <b>EXIT [{position.mode}]</b> | ${position.symbol}\n"
            f"P&L : <b>{pnl_pct:+.1f}%</b> ({position.realized_pnl_usd:+.2f} $)\n"
            f"Raison : {reason} | Durée : {duration_min:.0f} min\n"
            f"Score entrée : {position.alpha_score}"
        )

    def send_summary(self, stats: dict[str, Any]) -> bool:
        return self.send(
            f"📊 <b>RÉSUMÉ [{stats.get('mode')}]</b>\n"
            f"Capital : {stats.get('capital', 0):.2f} $ | "
            f"P&L total : {stats.get('total_pnl_usd', 0):+.2f} $\n"
            f"Positions ouvertes : {stats.get('open_positions', 0)}\n"
            f"Trades : {stats.get('total_trades', 0)} | "
            f"Win rate : {stats.get('win_rate', 0):.0f}% | "
            f"Profit factor : {stats.get('profit_factor', 0):.2f}\n"
            f"Max drawdown : {stats.get('max_drawdown_pct', 0):.1f}%",
            silent=True,
        )

    # ------------------------------------------------------------- entrant

    def poll_commands(self) -> list[str]:
        """Récupère les commandes reçues depuis le dernier appel.

        Seuls les messages du `chat_id` configuré sont pris en compte : un
        inconnu qui trouve le bot ne peut pas piloter le trading.
        """
        params: dict[str, Any] = {"timeout": 0, "allowed_updates": '["message"]'}
        if self._update_offset is not None:
            params["offset"] = self._update_offset

        updates = self._api("getUpdates", params)
        if not updates:
            return []

        commands = []
        for update in updates:
            self._update_offset = update["update_id"] + 1
            message = update.get("message") or {}
            sender_chat_id = str((message.get("chat") or {}).get("id"))
            if sender_chat_id != str(self.chat_id):
                continue
            text = (message.get("text") or "").strip().upper()
            if text.startswith("/"):
                commands.append(text.split()[0])
        return commands

    def drain_pending(self) -> None:
        """Ignore les commandes reçues bot éteint (évite un /STOP fantôme)."""
        updates = self._api("getUpdates", {"timeout": 0})
        if updates:
            self._update_offset = updates[-1]["update_id"] + 1
