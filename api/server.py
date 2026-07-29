"""API du dashboard Alpha Loop — lecture seule sur l'état du bot.

DÉCOUPLAGE : cette API ne connaît pas la boucle de trading. Elle lit les
fichiers que le bot écrit (`state.json`, `trades_log.jsonl`, `shadow_log.jsonl`)
et les sert en REST + WebSocket. Conséquences voulues :
  - l'API peut planter, redémarrer, être bombardée : le bot ne le sait pas
  - le bot peut être arrêté : le dashboard affiche le dernier état connu
  - aucun endpoint ne déclenche de trade

Lancement : uvicorn api.server:app --reload --port 8000
"""

import asyncio
import json
import os
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from src import settings

POLL_INTERVAL_SECONDS = 1.0
STALE_AFTER_SECONDS = 180

app = FastAPI(title="Alpha Loop Meme API", version="3.0")

# Le dashboard tourne sur un autre port en dev (Vite sur 5173).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _read_json(path: str) -> Optional[dict[str, Any]]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def _read_jsonl(path: str, limit: Optional[int] = None) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows[-limit:] if limit else rows


def _state() -> dict[str, Any]:
    """État courant, enrichi d'un indicateur de fraîcheur.

    `bot_online` distingue « le bot ne trouve rien » de « le bot est mort ».
    Sans ça, un dashboard figé est indiscernable d'un marché calme.
    """
    import time

    state = _read_json(settings.STATE_PATH)
    if state is None:
        return {"bot_online": False, "reason": "aucun state.json — le bot n'a jamais tourné"}

    age = time.time() - float(state.get("updated_at", 0))
    state["state_age_seconds"] = round(age, 1)
    state["bot_online"] = age < STALE_AFTER_SECONDS
    if not state["bot_online"]:
        state["reason"] = f"dernier signe de vie il y a {age / 60:.0f} min"
    return state


@app.get("/api/state")
def get_state() -> dict[str, Any]:
    return _state()


@app.get("/api/trades")
def get_trades(limit: int = 100) -> dict[str, Any]:
    rows = _read_jsonl(settings.TRADES_LOG_PATH, limit)
    finals = [r for r in rows if r.get("is_final_exit")]
    return {"trades": list(reversed(finals)), "partials": len(rows) - len(finals)}


@app.get("/api/shadow")
def get_shadow(limit: int = 100) -> dict[str, Any]:
    """Trades fantômes : ce que le bot a REFUSÉ et ce que c'est devenu."""
    rows = _read_jsonl(settings.SHADOW_LOG_PATH, limit)
    missed = [r for r in rows if r.get("would_have_won")]
    return {
        "shadow": list(reversed(rows)),
        "total": len(rows),
        "missed": len(missed),
        "missed_rate": round(100 * len(missed) / len(rows), 1) if rows else 0.0,
    }


@app.get("/api/params")
def get_params() -> dict[str, Any]:
    params = _read_json(settings.PARAMS_PATH) or {}
    history = (params.get("learning") or {}).get("parameter_adjustment_history", [])
    return {"params": params, "history": list(reversed(history))[:50]}


@app.get("/api/health")
def health() -> dict[str, Any]:
    state = _state()
    return {
        "api": "ok",
        "bot_online": state.get("bot_online", False),
        "mode": state.get("mode"),
        "cycle": state.get("cycle"),
    }


@app.websocket("/ws")
async def websocket_state(websocket: WebSocket) -> None:
    """Pousse l'état dès qu'il change. Diff par `updated_at`, pas de spam.

    Le bot réécrit state.json à chaque tick de monitoring (20s) et à chaque
    cycle (90s) : on relaie ces changements sans imposer de rythme au bot.
    """
    await websocket.accept()
    last_updated = None
    try:
        while True:
            state = _state()
            updated = state.get("updated_at")
            if updated != last_updated:
                await websocket.send_json(state)
                last_updated = updated
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        return
    except Exception:
        # Une socket morte ne doit pas remonter en erreur serveur.
        return
