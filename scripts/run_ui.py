#!/usr/bin/env python3
"""CryptobBot UI locale: API + WebSocket + dashboard sur un seul port (8000)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dashboard" / "dist"
HOST = "127.0.0.1"
PORT = 8000

sys.path.insert(0, str(ROOT))

if not DIST.is_dir():
    raise SystemExit(f"Manque {DIST}")

import uvicorn
from fastapi.staticfiles import StaticFiles

from api.server import app

if not getattr(app.state, "_cryptobot_ui_mounted", False):
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="dashboard")
    app.state._cryptobot_ui_mounted = True

if __name__ == "__main__":
    print(f"Ouvre Brave sur http://{HOST}:{PORT}", flush=True)
    print("(API + WebSocket + dashboard — laisse cette fenetre ouverte)", flush=True)
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
