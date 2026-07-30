"""Chemins projet + clés API lues depuis l'environnement.

RÈGLE DE SÉCURITÉ : aucune clé n'est écrite en dur ici, ni loggée en clair.
Voir .env.example. Les modules dégradent proprement si une clé manque.
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PARAMS_PATH = os.path.join(BASE_DIR, "config", "params.json")
CACHE_PATH = os.path.join(BASE_DIR, "data", "token_cache.json")
TRADES_LOG_PATH = os.path.join(BASE_DIR, "data", "trades_log.jsonl")
SHADOW_LOG_PATH = os.path.join(BASE_DIR, "data", "shadow_log.jsonl")
STATE_PATH = os.path.join(BASE_DIR, "data", "state.json")
LOCK_PATH = os.path.join(BASE_DIR, "data", "alpha_loop.pid")
POSITIONS_PATH = os.path.join(BASE_DIR, "data", "open_positions.json")
LOG_DIR = os.path.join(BASE_DIR, "logs")


def _load_dotenv(path: str = os.path.join(BASE_DIR, ".env")) -> None:
    """Charge .env sans dépendance externe. N'écrase pas l'env existant."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv()

# --- Clés API (None = module désactivé, pipeline continue en mode dégradé) ---
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY") or None
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY") or None
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN") or None
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or None
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or None

# DexScreener et RugCheck : API publiques, aucune clé requise.

GMGN_ENABLED = bool(os.getenv("GMGN_API_KEY")) or os.path.exists(
    os.path.expanduser("~/.config/gmgn/.env")
)

TRADING_MODE = os.getenv("TRADING_MODE", "PAPER").upper()


def key_status() -> dict[str, bool]:
    """Pour le dashboard : quelles intégrations sont actives (jamais la valeur)."""
    return {
        "dexscreener": True,
        "rugcheck": True,
        "helius": HELIUS_API_KEY is not None,
        "birdeye": BIRDEYE_API_KEY is not None,
        "twitter": TWITTER_BEARER_TOKEN is not None,
        "telegram": TELEGRAM_BOT_TOKEN is not None and TELEGRAM_CHAT_ID is not None,
        "gmgn": GMGN_ENABLED,
    }
