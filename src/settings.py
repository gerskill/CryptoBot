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
BUDGET_PATH = os.path.join(BASE_DIR, "data", "api_budgets.json")
WALLETS_LOG_PATH = os.path.join(BASE_DIR, "data", "wallets_log.jsonl")
FUNNEL_LOG_PATH = os.path.join(BASE_DIR, "data", "funnel_log.jsonl")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# --- Agents de mesure (src/agents) ---
# Ils CALCULENT et journalisent, ils n'ajustent aucun paramètre. Journaux
# séparés : un agent qui déborde ne doit pas noyer la trace d'un autre, et
# `_journal.append` fait tourner chaque fichier indépendamment.
DEV_HISTORY_LOG_PATH = os.path.join(BASE_DIR, "data", "dev_history_log.jsonl")
RSI_LOG_PATH = os.path.join(BASE_DIR, "data", "rsi_log.jsonl")
VOLATILITY_LOG_PATH = os.path.join(BASE_DIR, "data", "volatility_log.jsonl")
MICROSTRUCTURE_LOG_PATH = os.path.join(BASE_DIR, "data", "microstructure_log.jsonl")
COUNTERFACTUAL_LOG_PATH = os.path.join(BASE_DIR, "data", "counterfactual_log.jsonl")
TELEGRAM_REPORTER_LOG_PATH = os.path.join(
    BASE_DIR, "data", "telegram_reporter_log.jsonl"
)

# --- Multi-stratégie ---
STRATEGIES_PATH = os.path.join(BASE_DIR, "config", "strategies.json")
ARMS_CONFIG_DIR = os.path.join(BASE_DIR, "config", "arms")
ARMS_DATA_DIR = os.path.join(BASE_DIR, "data", "arms")

BASELINE_ARM = "baseline"


def arm_paths(name: str) -> dict[str, str]:
    """Fichiers d'un bras. `baseline` garde les chemins historiques.

    Toute la rétrocompatibilité tient dans cette fonction : le bras témoin
    continue d'écrire dans `data/trades_log.jsonl` et `config/params.json`, si
    bien que le dashboard, l'API et les 36 trades déjà collectés fonctionnent
    sans migration. La garder à un seul endroit.
    """
    if name == BASELINE_ARM:
        return {
            "params": PARAMS_PATH,
            "trades": TRADES_LOG_PATH,
            "shadow": SHADOW_LOG_PATH,
            "positions": POSITIONS_PATH,
        }
    directory = os.path.join(ARMS_DATA_DIR, name)
    return {
        "params": os.path.join(ARMS_CONFIG_DIR, f"{name}.json"),
        "trades": os.path.join(directory, "trades_log.jsonl"),
        "shadow": os.path.join(directory, "shadow_log.jsonl"),
        "positions": os.path.join(directory, "open_positions.json"),
    }


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
# Jupiter : prix par lot (50 mints/appel) et impact de prix réel. Le client
# n'expose QUE de la lecture — voir la liste blanche dans src/apis/jupiter.py.
JUPITER_API_KEY = os.getenv("JUPITER_API_KEY") or None
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
        "jupiter": JUPITER_API_KEY is not None,
        "telegram": TELEGRAM_BOT_TOKEN is not None and TELEGRAM_CHAT_ID is not None,
        "gmgn": GMGN_ENABLED,
    }
