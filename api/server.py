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
import secrets
import time
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from src import settings
from src.core.arm import load_manifest
from src.core.journal import TradeJournal


# Jeton facultatif. Absent = pas d'authentification, et l'API le DIT au
# démarrage plutôt que de laisser croire qu'elle est protégée.
#
# Ce que l'API expose : P&L, positions ouvertes, et surtout les PARAMÈTRES de
# chaque stratégie. Quiconque les lit connaît la stratégie complète. Tant
# qu'uvicorn écoute sur 127.0.0.1 le risque reste local ; il devient réel dès
# qu'on ouvre le port, qu'on passe par un tunnel, ou qu'un autre programme de
# la machine interroge le port 8000.
API_TOKEN = os.getenv("DASHBOARD_TOKEN") or None
if API_TOKEN is None:
    print(
        "[API] DASHBOARD_TOKEN absent — aucune authentification. "
        "Acceptable en local ; à définir avant tout tunnel ou exposition."
    )


def _require_token(token: Optional[str]) -> None:
    """Compare en temps constant : une comparaison naïve fuit la longueur."""
    if API_TOKEN is None:
        return
    if token is None or not secrets.compare_digest(token, API_TOKEN):
        raise HTTPException(status_code=401, detail="jeton invalide ou absent")


def _resolve_arm(name: Optional[str]) -> str:
    """Nom de bras validé CONTRE LE MANIFESTE.

    Jamais de chemin construit par concaténation depuis la query string, et
    404 plutôt qu'un repli silencieux sur le témoin : un nom inconnu qui
    renvoie les données d'un autre bras est pire qu'une erreur.
    """
    if not name:
        return settings.BASELINE_ARM
    known = {entry["name"] for entry in load_manifest()}
    if name not in known:
        raise HTTPException(status_code=404, detail=f"bras inconnu : {name}")
    return name

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
def get_trades(limit: int = 100, arm: Optional[str] = None) -> dict[str, Any]:
    """Un trade = une POSITION, jambes agrégées.

    Ne renvoyait auparavant que les lignes `is_final_exit`, donc une position
    sortie en TP1 puis breakeven s'affichait comme « BREAKEVEN_STOP -1,8% » :
    les gagnants du bot n'apparaissaient nulle part. `read_positions()`
    recolle les jambes ; `exit_path` garde le détail du chemin de sortie.
    """
    if arm == "all":
        # Défaut du dashboard. Sans ça il n'affichait que le témoin : 4
        # gagnants visibles sur 14 réels, les cinq de `sniper` invisibles.
        # Un tableau de bord qui cache les trades gagnants d'une stratégie
        # empêche exactement la comparaison qu'on cherche à faire.
        positions = []
        for entry in load_manifest():
            path = settings.arm_paths(entry["name"])["trades"]
            if not os.path.exists(path):
                continue
            for row in TradeJournal(path).read_positions():
                row["arm"] = entry["name"]
                positions.append(row)
        positions.sort(key=lambda r: r.get("timestamp_exit") or "")
    else:
        name = _resolve_arm(arm)
        path = settings.arm_paths(name)["trades"]
        positions = TradeJournal(path).read_positions() if os.path.exists(path) else []
        for row in positions:
            row["arm"] = name

    partials = sum(row.get("legs", 1) - 1 for row in positions)
    return {"trades": list(reversed(positions))[:limit], "partials": partials}


@app.get("/api/arms")
def get_arms() -> dict[str, Any]:
    """Les stratégies et leur état. Lu depuis `state.json`, comme le reste."""
    state = _read_json(settings.STATE_PATH) or {}
    return {
        "arms": state.get("arms", []),
        "manifest": load_manifest(),
        "baseline": settings.BASELINE_ARM,
    }


@app.get("/api/confluence")
def get_confluence() -> dict[str, Any]:
    """Sur quels tokens les stratégies tombent d'accord, au dernier cycle."""
    state = _read_json(settings.STATE_PATH) or {}
    return {"confluence": state.get("confluence", [])}


@app.get("/api/shadow")
def get_shadow(limit: int = 100, arm: Optional[str] = None) -> dict[str, Any]:
    """Trades fantômes : ce que le bot a REFUSÉ et ce que c'est devenu."""
    rows = _read_jsonl(settings.arm_paths(_resolve_arm(arm))["shadow"], limit)
    missed = [r for r in rows if r.get("would_have_won")]
    return {
        "shadow": list(reversed(rows)),
        "total": len(rows),
        "missed": len(missed),
        "missed_rate": round(100 * len(missed) / len(rows), 1) if rows else 0.0,
    }


@app.get("/api/params")
def get_params(arm: Optional[str] = None, token: Optional[str] = None) -> dict[str, Any]:
    """Endpoint le plus sensible : il rend la STRATÉGIE COMPLÈTE.

    Les autres exposent des résultats ; celui-ci expose les règles. C'est le
    seul dont la fuite permet de reproduire le bot.
    """
    _require_token(token)
    params = _read_json(settings.arm_paths(_resolve_arm(arm))["params"]) or {}
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
    """Pousse l'état dès qu'il change. Diff par `updated_at`, pas de spam."""
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
