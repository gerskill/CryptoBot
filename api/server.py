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
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Body, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import PlainTextResponse

from src import settings
from src.core import commandes as boite_bot
from src.core.arm import load_manifest
from src.core.journal import TradeJournal
from src.monitoring.metrics import generate_metrics_text
from src.prediction import overlay as prediction_overlay
from src.prediction.paper import BetStore, append_jsonl as _append_jsonl
from src.prediction.runner import summary as prediction_summary


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


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    """Jeton porté par `Authorization: Bearer <jeton>` — jamais dans l'URL,
    donc jamais dans les logs d'accès uvicorn ni l'historique du navigateur."""
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value


def _require_token(token: Optional[str] = None, authorization: Optional[str] = None) -> None:
    """Compare en temps constant : une comparaison naïve fuit la longueur.

    Le header `Authorization` est la voie normale ; `token` en query string
    reste accepté en repli pour ne pas casser d'éventuels appels existants,
    mais ne doit plus être utilisé par le dashboard lui-même.
    """
    if API_TOKEN is None:
        return
    candidate = _extract_bearer(authorization) or token
    if candidate is None or not secrets.compare_digest(candidate.encode(), API_TOKEN.encode()):
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

LOCAL_HOSTS = ("localhost", "127.0.0.1")


def parse_allowed_hosts(raw: Optional[str]) -> list[str]:
    """Hôtes acceptés dans l'en-tête Host : local, plus `DASHBOARD_ALLOWED_HOSTS`
    (séparés par des virgules, pour le domaine d'un tunnel). Pas de joker : un
    `*` rouvrirait le rebinding DNS qu'on ferme ici."""
    extra = [h.strip().lower() for h in (raw or "").split(",")]
    return list(LOCAL_HOSTS) + [h for h in extra if h and "*" not in h and h not in LOCAL_HOSTS]


def _hote_local(host: Optional[str]) -> bool:
    """Vrai si l'en-tête Host désigne cette machine (port ignoré), exactement."""
    if not host:
        return False
    return host.split(":")[0].lower() in LOCAL_HOSTS


# Le dashboard tourne sur un autre port en dev (Vite sur 5173).
# Rebinding DNS : une page tierce dont le nom pointe vers 127.0.0.1 enverrait
# des requêtes « même origine » avec SON Host. On refuse tout Host inconnu.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=parse_allowed_hosts(os.getenv("DASHBOARD_ALLOWED_HOSTS")))
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],  # volontaire : la route POST n'est ouverte qu'à la même origine
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


def _collect_positions(arm: Optional[str]) -> list[dict[str, Any]]:
    """Positions clôturées (jambes agrégées), triées par `timestamp_exit`
    croissant, champ `arm` renseigné. `arm="all"` = tous les bras ACTIVÉS.
    Source commune de `/api/trades` et `/api/trades/range`."""
    if arm == "all":
        # Défaut du dashboard. Sans ça il n'affichait que le témoin : 4
        # gagnants visibles sur 14 réels, les cinq de `sniper` invisibles.
        # Un tableau de bord qui cache les trades gagnants d'une stratégie
        # empêche exactement la comparaison qu'on cherche à faire.
        positions = []
        for entry in load_manifest():
            if not entry.get("enabled", True):
                continue
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

    return positions


@app.get("/api/trades")
def get_trades(limit: int = 100, arm: Optional[str] = None) -> dict[str, Any]:
    """Un trade = une POSITION, jambes agrégées.

    Ne renvoyait auparavant que les lignes `is_final_exit`, donc une position
    sortie en TP1 puis breakeven s'affichait comme « BREAKEVEN_STOP -1,8% » :
    les gagnants du bot n'apparaissaient nulle part. `read_positions()`
    recolle les jambes ; `exit_path` garde le détail du chemin de sortie.

    TROIS DÉFAUTS CORRIGÉS ICI, trouvés en vérifiant une courbe d'équité qui
    ne recollait pas avec le capital affiché (écart mesuré : ~47 $).

    1. `load_manifest()` liste TOUS les bras du fichier, désactivés compris.
       Un bras éteint (4 trades réels sur disque) se glissait dans `arm=all`
       alors qu'il est invisible partout ailleurs dans le dashboard — la
       courbe et le tableau ne portaient pas le même périmètre que le panneau
       STRATÉGIES.
    2. `equity_series` : la reconstruction d'une courbe cumulative a besoin
       de TOUT l'historique, pas d'une fenêtre de `limit`. Coupé à 100 sur
       210+ trades réels, le point le plus récent de la courbe ratait le
       P&L des ~110 trades les plus anciens. Champ séparé et JAMAIS borné :
       un flottant par trade ne pèse rien même à plusieurs milliers de
       positions, contrairement aux objets `trade` complets.
    3. `trades` reste borné par `limit` : le tableau « derniers trades »
       n'a besoin que d'une fenêtre récente, lui.
    """
    positions = _collect_positions(arm)

    partials = sum(row.get("legs", 1) - 1 for row in positions)
    return {
        "trades": list(reversed(positions))[:limit],
        "equity_series": [round(row.get("pnl_usd") or 0.0, 4) for row in positions],
        "partials": partials,
    }


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    """ISO 8601 (avec ou sans fuseau ; sans fuseau = UTC) ou epoch en
    secondes/millisecondes. `None` si absent ou illisible."""
    if value is None or str(value).strip() == "":
        return None
    raw = str(value).strip()
    try:
        number = float(raw)
    except ValueError:
        number = None
    if number is not None:
        if number > 1e11:  # millisecondes
            number /= 1000.0
        return datetime.fromtimestamp(number, tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@app.get("/api/trades/range")
def get_trades_range(
    since: Optional[str] = None,
    until: Optional[str] = None,
    arm: Optional[str] = "all",
) -> dict[str, Any]:
    """TOUTES les positions clôturées dans [since, until[, sans `limit`.

    Le rapport hebdo filtrait côté client la liste de `/api/trades`, bornée
    aux 100 positions les plus récentes tous bras confondus : avec ~12 bras,
    ça ne couvrait qu'une douzaine d'heures. « Cette semaine » affichait
    +192 $ (une bonne nuit) au lieu d'environ -5 000 $ sur la vraie semaine.

    Les bornes sont des instants absolus (ISO 8601 avec fuseau, ou epoch).
    Le dashboard envoie lundi 00:00 HEURE LOCALE du navigateur (Europe/Zurich
    sur le Mac) converti en UTC ; la semaine n'est donc pas décalée de 2 h.
    Filtre sur `timestamp_exit` : une position compte dans la semaine où
    elle se clôture, comme partout ailleurs dans le dashboard.
    """
    start = _parse_ts(since)
    end = _parse_ts(until)
    if since and start is None:
        raise HTTPException(status_code=400, detail="since illisible")
    if until and end is None:
        raise HTTPException(status_code=400, detail="until illisible")

    selected = []
    for row in _collect_positions(arm):
        exit_at = _parse_ts(row.get("timestamp_exit"))
        if exit_at is None:
            continue
        if start is not None and exit_at < start:
            continue
        if end is not None and exit_at >= end:
            continue
        selected.append(row)

    return {
        "trades": list(reversed(selected)),
        "count": len(selected),
        "pnl_usd": round(sum(row.get("pnl_usd") or 0.0 for row in selected), 4),
        "since": start.isoformat() if start else None,
        "until": end.isoformat() if end else None,
    }


@app.get("/api/arms")
def get_arms() -> dict[str, Any]:
    """Les stratégies et leur état. Lu depuis `state.json`, comme le reste."""
    state = _read_json(settings.STATE_PATH) or {}
    return {
        "arms": state.get("arms", []),
        "manifest": load_manifest(),
        "baseline": settings.BASELINE_ARM,
    }


# Fenêtres servies par `/api/arms/performance`. Fermées : une fenêtre libre
# (`?days=3`) invite à chercher celle qui flatte un bras, ce qui est un
# choix de période fait après coup sur les résultats.
PERFORMANCE_WINDOWS = {"7d": 7, "30d": 30, "all": None}

# Même règle que `src/main.py::_starting_capital` : `capital_total` de
# params.json, sinon le capital papier fictif de 1000 $. En PAPER chaque bras
# reçoit CETTE mise entière, `capital_pct` n'est pas appliqué (voir
# `src/core/arm.py::attach_portfolios`). Recopié et non importé : importer
# `src.main` depuis l'API chargerait toute la boucle de trading.
PAPER_DEFAULT_CAPITAL = 1000.0


def _starting_capital() -> float:
    params = _read_json(settings.PARAMS_PATH) or {}
    try:
        capital = float(params.get("capital_total") or 0)
    except (TypeError, ValueError):
        capital = 0.0
    return capital if capital > 0 else PAPER_DEFAULT_CAPITAL


@app.get("/api/arms/performance")
def get_arms_performance(window: str = "7d") -> dict[str, Any]:
    """P&L par bras sur 7 j, 30 j ou tout : trades, WR (+ IC95), PF, P&L,
    drawdown de la fenêtre, P&L jour par jour, verdict contre le témoin.

    Source : `read_positions()` (une position = jambes agrégées), comme
    `/api/trades`. Les coûts de sortie sont déjà dans `pnl_usd` (ADR 009, 010,
    012). Chaque ligne porte son `arm` : un chiffre sans bras nommé est le
    bug d'interface le plus coûteux de ce dashboard.

    Le verdict compare le bras au témoin SUR LA MÊME FENÊTRE ; à 7 jours il
    vaut presque toujours « échantillon trop court », et c'est la bonne
    réponse, pas un défaut d'affichage.
    """
    from datetime import timedelta

    from src.core.stats import position_stats, verdict_vs_reference, window_performance

    if window not in PERFORMANCE_WINDOWS:
        raise HTTPException(
            status_code=400,
            detail=f"fenêtre inconnue : {window} (attendu : {', '.join(PERFORMANCE_WINDOWS)})",
        )
    days = PERFORMANCE_WINDOWS[window]
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days) if days is not None else None
    capital = _starting_capital()

    def dans_fenetre(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if since is None:
            return rows
        return [r for r in rows if (m := _parse_ts(r.get("timestamp_exit"))) and m >= since]

    try:
        reference = dans_fenetre(_collect_positions(settings.BASELINE_ARM))
    except HTTPException:
        # Témoin absent du manifeste : pas de référence, donc pas de verdict
        # — et surtout pas une erreur 404 sur tout le tableau.
        reference = []
    arms = []
    fleet: list[dict[str, Any]] = []
    comparaisons = sum(
        1 for e in load_manifest()
        if e.get("enabled", True) and e["name"] != settings.BASELINE_ARM
    )
    for entry in load_manifest():
        if not entry.get("enabled", True):
            continue
        name = entry["name"]
        positions = _collect_positions(name)
        perf = window_performance(positions, capital, since)
        fenetre = dans_fenetre(positions)
        fleet.extend(fenetre)
        perf["verdict"] = (
            None if name == settings.BASELINE_ARM
            else verdict_vs_reference(fenetre, reference, comparisons=comparaisons)
        )
        arms.append({"arm": name, "role": entry.get("role"), **perf})

    flotte = position_stats(fleet)
    flotte["arm"] = "flotte"
    flotte["label"] = "somme des bras activés (drawdown non agrégeable, voir chaque bras)"
    return {
        "window": window,
        "since": since.isoformat() if since else None,
        "until": now.isoformat(),
        "starting_capital_per_arm": capital,
        "baseline": settings.BASELINE_ARM,
        "arms": arms,
        "fleet": flotte,
    }


@app.get("/api/confluence")
def get_confluence() -> dict[str, Any]:
    """Sur quels tokens les stratégies tombent d'accord, au dernier cycle."""
    state = _read_json(settings.STATE_PATH) or {}
    return {"confluence": state.get("confluence", [])}


@app.get("/api/shadow")
def get_shadow(limit: int = 100, arm: Optional[str] = None) -> dict[str, Any]:
    """Trades fantômes : ce que le bot a REFUSÉ et ce que c'est devenu.

    LE BUG CORRIGÉ ICI. `total`/`missed`/`missed_rate` se calculaient sur
    `rows`, DÉJÀ tronqué à `limit` par `_read_jsonl`. Le dashboard affichait
    « 100 jugés » en plafond structurel, quelle que soit la taille réelle du
    journal — mesuré à 730 lignes le jour où ça a été trouvé. `limit` ne doit
    borner que ce qui s'AFFICHE (`shadow`), jamais ce qui se COMPTE.
    """
    toutes = _read_jsonl(settings.arm_paths(_resolve_arm(arm))["shadow"])
    missed = [r for r in toutes if r.get("would_have_won")]
    return {
        "shadow": list(reversed(toutes))[:limit],
        "total": len(toutes),
        "missed": len(missed),
        "missed_rate": round(100 * len(missed) / len(toutes), 1) if toutes else 0.0,
    }


@app.get("/api/params")
def get_params(
    arm: Optional[str] = None,
    token: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Endpoint le plus sensible : il rend la STRATÉGIE COMPLÈTE.

    Les autres exposent des résultats ; celui-ci expose les règles. C'est le
    seul dont la fuite permet de reproduire le bot.
    """
    _require_token(token, authorization)
    params = _read_json(settings.arm_paths(_resolve_arm(arm))["params"]) or {}
    history = (params.get("learning") or {}).get("parameter_adjustment_history", [])
    return {"params": params, "history": list(reversed(history))[:50]}


# ---------------------------------------------------------------------------
# Marchés de prédiction Liquid (PAPIER) — lecture seule de data/prediction/
# ---------------------------------------------------------------------------
# Le processus `scripts/run_prediction.py` est seul propriétaire de ces
# fichiers. `demo=1` lit un AUTRE dossier, rempli par
# `scripts/prediction_demo.py` : chaque réponse porte alors
# `exemple_fictif: true`, et l'interface l'affiche sous un bandeau.

# Un cycle dure ~100 s + 60 s d'attente (mesuré le 2026-11-21) et l'app ne voit
# qu'une publication toutes les 2 min : 300 s déclarait la boucle arrêtée à tort.
PREDICTION_STALE_SECONDS = 600
_COIN_PATTERN = re.compile(r"^#?(\d{1,12})$")


def _prediction_dir(demo: bool) -> str:
    return settings.PREDICTION_DEMO_DIR if demo else settings.PREDICTION_DATA_DIR


def _prediction_config() -> dict[str, Any]:
    config = _read_json(settings.PREDICTION_ARMS_PATH)
    if not config or not isinstance(config.get("arms"), list):
        raise HTTPException(status_code=503, detail="config/prediction_arms.json illisible")
    return config


@app.get("/api/prediction/summary")
def get_prediction_summary(demo: bool = False) -> dict[str, Any]:
    """Une ligne par bras de prédiction. Aucun total tous bras confondus :
    chaque chiffre reste attribué à son bras."""
    config = _prediction_config()
    directory = _prediction_dir(demo)
    state = _read_json(os.path.join(directory, "state.json")) or {}
    age = time.time() - float(state.get("updated_at", 0)) if state else None
    return {
        "exemple_fictif": demo,
        "mode": "PAPER",
        "runner_online": bool(state) and not demo and age is not None and age < PREDICTION_STALE_SECONDS,
        "updated_at_iso": state.get("updated_at_iso"),
        "marches_par_domaine": state.get("marches_par_domaine", {}),
        "config": {key: config.get(key) for key in (
            "capital_fictif_usd", "mise_pct", "mise_max_pct", "exposition_max_pct_par_domaine",
            "frais_pct", "frais_reglement_pct", "min_paris_verdict", "ordre_min_usd",
            "regle_taille_min", "parts_entieres", "delai_prudence_heures")},
        "arms": prediction_summary(config, directory),
    }


@app.get("/api/prediction/bets")
def get_prediction_bets(arm: str = "all", demo: bool = False) -> dict[str, Any]:
    """Paris ouverts et réglés, chaque ligne avec son `arm`."""
    config = _prediction_config()
    # Configuration effective : un fork créé depuis le site a aussi ses paris.
    effective = config if demo else prediction_overlay.effective_config(config, settings.PREDICTION_DATA_DIR)
    known = [entry["name"] for entry in effective["arms"]]
    if arm != "all" and arm not in known:
        raise HTTPException(status_code=404, detail=f"bras inconnu : {arm}")
    bets = []
    for name in (known if arm == "all" else [arm]):
        store = BetStore(_prediction_dir(demo), name)
        for row in store.settled() + store.open_bets():
            row["arm"] = name
            row["exemple_fictif"] = demo
            bets.append(row)
    bets.sort(key=lambda r: r.get("entry_time") or "")
    return {"exemple_fictif": demo, "arm": arm, "total": len(bets), "bets": bets}


@app.get("/api/prediction/prices")
def get_prediction_prices(coin: str, demo: bool = False) -> dict[str, Any]:
    """Série de prix d'une pièce `#N`, pour tracer un pari sur son graphique."""
    match = _COIN_PATTERN.match(coin)
    if not match:
        # Le nom sert à construire un chemin : seuls des chiffres passent.
        raise HTTPException(status_code=400, detail="pièce invalide")
    path = os.path.join(_prediction_dir(demo), "prices", f"{match.group(1)}.jsonl")
    points = [{"t": row.get("t"), "mid": row.get("mid")} for row in _read_jsonl(path)
              if row.get("t") and row.get("mid") is not None]
    return {"exemple_fictif": demo, "coin": f"#{match.group(1)}", "points": points}


@app.get("/api/prediction/coherence")
def get_prediction_coherence(limit: int = 100, demo: bool = False) -> dict[str, Any]:
    rows = _read_jsonl(os.path.join(_prediction_dir(demo), "coherence.jsonl"))
    return {"exemple_fictif": demo, "arm": "pm_coherence", "total": len(rows),
            "rows": rows[-max(1, min(limit, 500)):]}


@app.get("/api/prediction/learning")
def get_prediction_learning(arm: str = "all", limit: int = 100) -> dict[str, Any]:
    """Diagnostics de l'agent d'apprentissage, une entrée par bras, et son
    journal de propositions. Lecture seule : l'agent écrit, l'API lit."""
    config = prediction_overlay.effective_config(_prediction_config(), settings.PREDICTION_DATA_DIR)
    known = [entry["name"] for entry in config["arms"]]
    if arm != "all" and arm not in known:
        raise HTTPException(status_code=404, detail=f"bras inconnu : {arm}")
    directory = os.path.join(settings.PREDICTION_DATA_DIR, "learning")
    rows = []
    for name in (known if arm == "all" else [arm]):
        data = _read_json(os.path.join(directory, f"{name}.json"))
        if isinstance(data, dict):
            rows.append(dict(data, arm=name))
    log = _read_jsonl(os.path.join(directory, "learning_log.jsonl"))
    return {"arm": arm, "total": len(rows), "diagnostics": rows,
            "journal": log[-max(1, min(limit, 500)):]}


@app.get("/api/prediction/requests")
def get_prediction_requests() -> dict[str, Any]:
    """Demandes de modification déposées depuis le site, avec leur sort."""
    rows = prediction_overlay.request_status(settings.PREDICTION_DATA_DIR)
    return {"total": len(rows), "requests": rows,
            "bornes": prediction_overlay.BOUNDS, "max_forks": prediction_overlay.MAX_FORKS}


@app.post("/api/prediction/requests")
def post_prediction_request(
    payload: dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    x_cryptobbot: Optional[str] = Header(default=None),
    content_type: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Dépose UNE demande (activer/désactiver un bras, créer un fork).

    SEULE route d'écriture de cette API (ADR 017). Elle n'écrit que dans sa
    boîte de demandes : c'est la simulation, propriétaire de ses fichiers,
    qui valide et applique au cycle suivant. Bras PAPIER de prédiction
    uniquement ; rien ici ne touche la boucle memecoin.

    L'en-tête `X-CryptobBot` et le type JSON obligent un navigateur à
    demander la permission (preflight) : une page web tierce ne peut pas
    poster ici, le CORS n'autorisant que GET.
    """
    _require_token(None, authorization)
    if x_cryptobbot != "1" or not (content_type or "").lower().startswith("application/json"):
        raise HTTPException(status_code=400, detail="en-tête X-CryptobBot ou type JSON manquant")
    return deposer_demande_prediction(payload)


def deposer_demande_prediction(payload: dict[str, Any]) -> dict[str, Any]:
    """Valide et dépose une demande de bras de prédiction ; HTTPException si refusée.

    Partagée par la route du site et par les commandes iPhone
    (`scripts/publie_supabase.py`, ADR 021) : mêmes règles, même boîte.
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="demande illisible")
    action = payload.get("action")
    if action == "toggle":
        request = {"action": "toggle", "arm": str(payload.get("arm") or ""),
                   "enabled": bool(payload.get("enabled"))}
    elif action == "fork":
        request = {"action": "fork", "parent": str(payload.get("parent") or ""),
                   "name": str(payload.get("name") or ""), "variable": payload.get("variable"),
                   "value": payload.get("value")}
        # Contrôle immédiat pour répondre tout de suite ; la simulation
        # revalide de toute façon avant d'appliquer.
        config = prediction_overlay.effective_config(_prediction_config(), settings.PREDICTION_DATA_DIR)
        _, reason = prediction_overlay.build_fork(config["arms"], request, datetime.now(timezone.utc))
        if reason:
            raise HTTPException(status_code=422, detail=reason)
    else:
        raise HTTPException(status_code=422, detail="action inconnue")
    pending = [r for r in prediction_overlay.request_status(settings.PREDICTION_DATA_DIR, 500)
               if r["statut"] == "en attente"]
    if len(pending) >= 20:
        raise HTTPException(status_code=429, detail="trop de demandes en attente : la simulation tourne-t-elle ?")
    request["id"] = f"{int(time.time() * 1000)}-{secrets.token_hex(3)}"
    request["t"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _append_jsonl(prediction_overlay.requests_path(settings.PREDICTION_DATA_DIR), request)
    return {"ok": True, "id": request["id"], "statut": "en attente",
            "note": "appliquée au prochain cycle de la simulation"}


# ---------------------------------------------------------------------------
# Pilotage depuis le site : arrêt d'urgence et reprise (ADR 021)
# ---------------------------------------------------------------------------
# Même boîte, mêmes deux actions que l'iPhone (`scripts/publie_supabase.py`) et
# que Telegram (/STOP, /RESUME). Le site ne PILOTE pas la boucle : il dépose un
# fichier, la boucle (seule propriétaire de son état) le lit au cycle suivant.
# Rien ici n'ouvre une position ni n'accepte un ordre.

def _data_dir_bot() -> str:
    return os.path.join(settings.BASE_DIR, "data")


@app.get("/api/bot/commandes")
def get_bot_commandes(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    """Commandes déposées que la boucle n'a pas encore lues, et le délai de péremption.
    Jeton par `Authorization` seulement : jamais dans l'URL."""
    _require_token(None, authorization)
    return {"en_attente": boite_bot.en_attente(_data_dir_bot()),
            "expire_apres_s": boite_bot.EXPIRE_S, "actions": sorted(boite_bot.COMMANDES)}


@app.post("/api/bot/commandes")
def post_bot_commande(
    payload: dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    x_cryptobbot: Optional[str] = Header(default=None),
    content_type: Optional[str] = Header(default=None),
    host: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Dépose UNE commande `stop` ou `resume` dans la boîte de la boucle.

    Mêmes garde-fous que la boîte de demandes Liquid : jeton `DASHBOARD_TOKEN`
    (si défini), en-tête `X-CryptobBot` et type JSON (un navigateur tiers doit
    passer par un preflight que le CORS refuse). Liste d'actions fermée.
    """
    _require_token(None, authorization)
    if API_TOKEN is None and not _hote_local(host):
        # Sans jeton, seul l'accès local est admis : un tunnel sans jeton ne pilote pas le bot.
        raise HTTPException(status_code=403, detail="sans DASHBOARD_TOKEN, commande refusée hors accès local")
    if x_cryptobbot != "1" or not (content_type or "").lower().startswith("application/json"):
        raise HTTPException(status_code=400, detail="en-tête X-CryptobBot ou type JSON manquant")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="commande illisible")
    action = payload.get("action")
    if not isinstance(action, str) or action not in boite_bot.COMMANDES:
        raise HTTPException(status_code=422, detail="action inconnue : stop ou resume seulement")
    # Pas de plafond global (il pourrait bloquer un `stop`) : un doublon valide
    # est simplement signalé. Au plus deux commandes valides en attente (stop, resume).
    existante = boite_bot.deja_en_attente(_data_dir_bot(), action)
    if existante:
        return {"ok": True, "id": existante, "statut": "en attente",
                "note": "déjà en attente : la boucle la lira à son prochain cycle"}
    ident = f"{int(time.time() * 1000):013d}-site{secrets.token_hex(3)}"
    boite_bot.deposer(_data_dir_bot(), action, ident)
    quoi = "arrêt d'urgence" if action == "stop" else "reprise des entrées"
    return {"ok": True, "id": ident, "statut": "en attente",
            "note": f"{quoi} transmis à la boucle (prochain cycle, périmé après 15 min)"}


@app.get("/api/prediction/markets")
def get_prediction_markets(demo: bool = False) -> dict[str, Any]:
    """Tous les marchés de prédiction vus au dernier cycle, rangés par famille
    et par domaine. C'est un relevé de prix, pas un résultat de bras."""
    data = _read_json(os.path.join(_prediction_dir(demo), "catalogue.json")) or {}
    markets = data.get("markets") if isinstance(data.get("markets"), list) else []
    return {"exemple_fictif": demo, "updated_at_iso": data.get("updated_at_iso"),
            "total": len(markets), "markets": markets}


@app.get("/api/liquid/perps")
def get_liquid_perps() -> dict[str, Any]:
    """Marchés à terme (perpétuels) de Liquid relevés au dernier cycle.
    Affichage seul : aucun bras ne les trade."""
    data = _read_json(os.path.join(settings.PREDICTION_DATA_DIR, "perps.json")) or {}
    markets = data.get("markets") if isinstance(data.get("markets"), list) else []
    return {"updated_at_iso": data.get("updated_at_iso"), "total": len(markets),
            "markets": markets}


@app.get("/api/metrics")
def get_metrics() -> PlainTextResponse:
    """Métriques de supervision au format texte Prometheus.

    Lecture seule, mêmes sources que le reste de cette API (`state.json`,
    journaux par bras) — voir `src/monitoring/metrics.py`. Distinct de
    `/api/health` : celui-ci reste le battement de cœur consommé par le
    dashboard et le monitoring manuel, inchangé.
    """
    return PlainTextResponse(
        generate_metrics_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


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
