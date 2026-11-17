#!/usr/bin/env python3
"""PAPER copy-robustness v2 — NEW criteria vs copy_fitness_all / ROI-ann shortlist.
Priority: edge after copy friction → DD quality → age/low-snipe/followable → WR stability / win:loss
Never prints secrets / API keys / RPC URLs.
"""
from __future__ import annotations
import csv, json, os, sys, time, urllib.request, urllib.error, statistics, math
from collections import defaultdict, deque, Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
QUOTE = {WSOL, USDC, USDT, "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"}
SNIPE_S = 30
SLEEP = 0.18
TZ = ZoneInfo("Europe/Zurich")
DROP = {"3DnwtnkPxgCEmaxcpYviEUNFqoED6X2icoY2KdhLXaQS"}

ROOT = Path.home() / "Projects" / "CryptobBot"
OUT = ROOT / "scripts" / "out"
WS = Path("/workspace/wallets_alpha")
# Prefer workspace if mounted; else Mac out + sync later
DEST = WS if WS.exists() else OUT


def load_env(path: Path) -> dict:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from helius_budget import CreditBudget  # plafond de crédits : scripts/helius_budget.py
BUDGET = CreditBudget(name=__file__)


def helius_txs(api_key: str, wallet: str, before: Optional[str] = None):
    q = f"api-key={api_key}&limit=100"
    if before:
        q += f"&before={before}"
    url = f"https://api.helius.xyz/v0/addresses/{wallet}/transactions?{q}"
    if not BUDGET.charge():
        return [], "budget_credits_epuise"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=50) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        return [], f"http_{e.code}"
    except Exception as e:
        return [], type(e).__name__


def get_sol_price() -> float:
    try:
        req = urllib.request.Request(
            "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return float(json.loads(r.read())["solana"]["usd"])
    except Exception:
        return 160.0


def fetch_enhanced(api_key: str, wallet: str, max_pages: int):
    all_txs = []
    before = None
    last_err = None
    for _ in range(max_pages):
        batch, err = helius_txs(api_key, wallet, before)
        if err:
            last_err = err
            if "429" in str(err):
                time.sleep(2.5)
                batch, err = helius_txs(api_key, wallet, before)
                if err:
                    last_err = err
                    break
            else:
                break
        if not batch:
            break
        all_txs.extend(batch)
        before = batch[-1].get("signature")
        if len(batch) < 100:
            break
        time.sleep(SLEEP)
    return all_txs, last_err


def parse_events(txs, wallet):
    events = []
    for tx in txs:
        ts = tx.get("timestamp") or 0
        if tx.get("transactionError"):
            continue
        token_deltas = defaultdict(float)
        for t in tx.get("tokenTransfers") or []:
            mint = t.get("mint")
            if not mint:
                continue
            amt = float(t.get("tokenAmount") or 0)
            if t.get("toUserAccount") == wallet:
                token_deltas[mint] += amt
            if t.get("fromUserAccount") == wallet:
                token_deltas[mint] -= amt
        native = 0.0
        for n in tx.get("nativeTransfers") or []:
            amt = float(n.get("amount") or 0) / 1e9
            if n.get("toUserAccount") == wallet:
                native += amt
            if n.get("fromUserAccount") == wallet:
                native -= amt
        native -= float(tx.get("fee") or 0) / 1e9
        quote_usd = 0.0
        sol_delta = native
        for mint, d in list(token_deltas.items()):
            if mint in (USDC, USDT):
                quote_usd += d
                del token_deltas[mint]
            elif mint == WSOL:
                sol_delta += d
                del token_deltas[mint]
            elif mint in QUOTE:
                del token_deltas[mint]
        for mint, d in token_deltas.items():
            if abs(d) < 1e-12:
                continue
            events.append({
                "mint": mint, "side": "buy" if d > 0 else "sell", "amount": abs(d),
                "quote_usd": quote_usd, "sol_delta": sol_delta,
                "ts": ts, "sig": tx.get("signature"),
            })
    events.sort(key=lambda e: e["ts"])
    return events


def fifo_closed(events, sol_usd):
    by_sig = defaultdict(list)
    for e in events:
        by_sig[e["sig"]].append(e)
    adjusted = []
    for sig, group in by_sig.items():
        if len(group) == 1:
            adjusted.append(group[0])
            continue
        total_abs = sum(g["amount"] for g in group) or 1.0
        q, s = group[0]["quote_usd"], group[0]["sol_delta"]
        for g in group:
            w = g["amount"] / total_abs
            ng = dict(g)
            ng["quote_usd"] = q * w
            ng["sol_delta"] = s * w
            adjusted.append(ng)
    adjusted.sort(key=lambda e: e["ts"])
    lots = defaultdict(deque)
    closed = []
    for e in adjusted:
        mint = e["mint"]
        flow = e["quote_usd"] + e["sol_delta"] * sol_usd
        if e["side"] == "buy":
            lots[mint].append([e["amount"], max(0.0, -flow), e["ts"]])
        else:
            rem = e["amount"]
            proceeds = max(0.0, flow)
            amt_total = e["amount"] or 1.0
            while rem > 1e-12 and lots[mint]:
                lot_amt, lot_cost, lot_ts = lots[mint][0]
                take = min(rem, lot_amt)
                frac = take / lot_amt if lot_amt else 0
                cost_take = lot_cost * frac
                proc_take = proceeds * (take / amt_total)
                closed.append({
                    "pnl": proc_take - cost_take,
                    "cost": cost_take,
                    "proceeds": proc_take,
                    "mint": mint,
                    "hold_s": max(0, e["ts"] - lot_ts),
                    "exit_ts": e["ts"],
                    "entry_ts": lot_ts,
                })
                lot_amt -= take
                lot_cost -= cost_take
                rem -= take
                if lot_amt <= 1e-12:
                    lots[mint].popleft()
                else:
                    lots[mint][0] = [lot_amt, lot_cost, lot_ts]
    closed.sort(key=lambda c: c["exit_ts"])
    return closed


def analyze_closed(closed: list[dict]) -> dict:
    if not closed:
        return {"n": 0}
    wins = [c for c in closed if c["pnl"] > 0]
    losses = [c for c in closed if c["pnl"] <= 0]
    gp = sum(c["pnl"] for c in wins)
    gl = -sum(c["pnl"] for c in losses)
    pnl = sum(c["pnl"] for c in closed)
    cost = sum(c["cost"] for c in closed)
    proceeds = sum(c.get("proceeds", c["cost"] + c["pnl"]) for c in closed)
    notional = cost + proceeds
    pf = (gp / gl) if gl > 1e-9 else (999.0 if gp > 0 else 0.0)
    avg_w = statistics.mean([c["pnl"] for c in wins]) if wins else 0.0
    avg_l = statistics.mean([-c["pnl"] for c in losses]) if losses else 0.0
    wl_ratio = (avg_w / avg_l) if avg_l > 1e-9 else (999.0 if avg_w > 0 else 0.0)
    # fee stress: 0.5% and 1.0% per side on notional
    e05 = pnl - 0.005 * notional
    e10 = pnl - 0.01 * notional
    # concentration: top-10 winners share of gross profit
    top_wins = sorted([c["pnl"] for c in wins], reverse=True)[:10]
    conc10 = (sum(top_wins) / gp * 100) if gp > 1e-9 else 0.0
    # snipes
    snipe_pct = 100 * sum(1 for c in closed if c["hold_s"] <= SNIPE_S) / len(closed)
    holds = [c["hold_s"] for c in closed]
    med_hold = statistics.median(holds)
    # span / tpd
    t0, t1 = closed[0]["exit_ts"], closed[-1]["exit_ts"]
    span_d = max((t1 - t0) / 86400, 1 / 24)
    tpd = len(closed) / span_d
    # equity DD
    eq = peak = 0.0
    max_dd = 0.0
    max_dd_pct_peak = 0.0
    open_dd = 0.0
    phase_start = None
    recovered_phases = []
    open_phase = None
    for c in closed:
        eq += c["pnl"]
        if eq >= peak - 1e-9:
            if open_phase:
                open_phase["end"] = c["exit_ts"]
                open_phase["recovered"] = True
                open_phase["dur_d"] = (c["exit_ts"] - open_phase["start"]) / 86400
                recovered_phases.append(open_phase)
                open_phase = None
            peak = eq
            phase_start = c["exit_ts"]
        else:
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd
            if peak > 50:
                pct = dd / peak * 100
                if pct > max_dd_pct_peak:
                    max_dd_pct_peak = pct
            if open_phase is None:
                open_phase = {"start": phase_start or c["exit_ts"], "peak": peak, "depth": dd, "recovered": False}
            elif dd > open_phase["depth"]:
                open_phase["depth"] = dd
    if open_phase:
        open_phase["end"] = closed[-1]["exit_ts"]
        open_phase["dur_d"] = (closed[-1]["exit_ts"] - open_phase["start"]) / 86400
        open_dd = open_phase["depth"]
    # weekly WR
    weeks = defaultdict(list)
    for c in closed:
        d = datetime.fromtimestamp(c["exit_ts"], TZ).date()
        monday = d - timedelta(days=d.weekday())
        weeks[monday.isoformat()].append(1 if c["pnl"] > 0 else 0)
    week_wrs = []
    week_pnls = []
    for k in sorted(weeks):
        xs = weeks[k]
        if len(xs) >= 5:
            week_wrs.append(100 * sum(xs) / len(xs))
        week_pnls.append(sum(
            c["pnl"] for c in closed
            if (datetime.fromtimestamp(c["exit_ts"], TZ).date() - timedelta(days=datetime.fromtimestamp(c["exit_ts"], TZ).date().weekday())).isoformat() == k
        ))
    wr_sigma = statistics.pstdev(week_wrs) if len(week_wrs) >= 2 else None
    wr_min = min(week_wrs) if week_wrs else None
    wr_max = max(week_wrs) if week_wrs else None
    pos_weeks = sum(1 for p in week_pnls if p > 0)
    return {
        "n": len(closed),
        "wr": 100 * len(wins) / len(closed),
        "pnl": pnl,
        "cost": cost,
        "pf": pf,
        "avg_win": avg_w,
        "avg_loss": avg_l,
        "wl_ratio": wl_ratio,
        "e05": e05,
        "e10": e10,
        "survives_05": e05 > 0,
        "survives_10": e10 > 0,
        "conc10_pct": conc10,
        "snipe_pct": snipe_pct,
        "med_hold_s": med_hold,
        "span_d": span_d,
        "tpd": tpd,
        "max_dd_usd": max_dd,
        "max_dd_pct_peak": max_dd_pct_peak,
        "open_dd_usd": open_dd,
        "n_recovered_dd": len(recovered_phases),
        "longest_recovered_dd_d": max((p["dur_d"] for p in recovered_phases), default=0),
        "open_dd_d": open_phase["dur_d"] if open_phase else 0,
        "wr_sigma": wr_sigma,
        "wr_range": f"{wr_min:.0f}–{wr_max:.0f}" if wr_min is not None else "n/a",
        "pos_weeks": pos_weeks,
        "n_weeks": len(week_pnls),
        "median_cost": statistics.median(c["cost"] for c in closed),
    }


def score_row(m: dict, age: float, prior_verd: str, arb: bool, rotation: str) -> tuple[float, str, str]:
    """Return (score, band, why). Higher = more robust for PAPER copy."""
    if m.get("n", 0) < 40:
        return -999, "À ÉVITER", "trop peu de trades fermés pour juger"
    reasons = []
    score = 0.0

    # 1) Edge after friction (weight ~40)
    e05, e10 = m["e05"], m["e10"]
    pf = m["pf"]
    if e10 > 0:
        score += 25 + min(15, e10 / 500)
        reasons.append(f"edge survit 1%/côté ({e10:.0f}$)")
    elif e05 > 0:
        score += 12 + min(8, e05 / 500)
        reasons.append(f"edge survit 0.5%/côté ({e05:.0f}$) mais pas 1%")
    else:
        score -= 25
        reasons.append(f"edge meurt à 0.5%/côté ({e05:.0f}$)")
    if pf >= 1.5:
        score += 10
        reasons.append(f"PF {pf:.2f}")
    elif pf >= 1.2:
        score += 6
        reasons.append(f"PF {pf:.2f}")
    elif pf >= 1.05:
        score += 2
        reasons.append(f"PF mince {pf:.2f}")
    else:
        score -= 8
        reasons.append(f"PF faible {pf:.2f}")

    # 2) DD quality (weight ~20)
    dd_pct = m["max_dd_pct_peak"]
    open_dd = m["open_dd_usd"]
    if dd_pct <= 25 and open_dd < 200:
        score += 12
        reasons.append(f"DD contenu ({dd_pct:.0f}% peak, ouvert {open_dd:.0f}$)")
    elif dd_pct <= 50 and open_dd < 1000:
        score += 6
        reasons.append(f"DD acceptable ({dd_pct:.0f}%)")
    elif dd_pct <= 100:
        score += 0
        reasons.append(f"DD élevé ({dd_pct:.0f}%)")
    else:
        score -= 10
        reasons.append(f"DD chaotique ({dd_pct:.0f}% peak)")
    if m["open_dd_d"] > 30:
        score -= 6
        reasons.append(f"sous l'eau depuis {m['open_dd_d']:.0f}j")
    conc = m["conc10_pct"]
    if conc <= 35:
        score += 6
        reasons.append(f"peu concentré top10={conc:.0f}%")
    elif conc <= 55:
        score += 2
    else:
        score -= 6
        reasons.append(f"PnL concentré top10={conc:.0f}%")

    # 3) Age / snipes / followability (weight ~25)
    if age >= 90:
        score += 10
        reasons.append(f"âge {age:.0f}j")
    elif age >= 45:
        score += 6
        reasons.append(f"âge {age:.0f}j")
    elif age >= 20:
        score += 2
    else:
        score -= 8
        reasons.append(f"jeune {age:.0f}j")
    if m["span_d"] >= 30:
        score += 6
    elif m["span_d"] >= 14:
        score += 3
    else:
        score -= 5
        reasons.append(f"span court {m['span_d']:.0f}j")
    sn = m["snipe_pct"]
    if sn <= 20:
        score += 10
        reasons.append(f"snipes bas {sn:.0f}%")
    elif sn <= 35:
        score += 4
        reasons.append(f"snipes ok {sn:.0f}%")
    elif sn <= 50:
        score -= 4
        reasons.append(f"snipes élevés {sn:.0f}% — beaucoup de misses copie")
    else:
        score -= 12
        reasons.append(f"snipes {sn:.0f}% — difficilement copiable")
    tpd = m["tpd"]
    if tpd <= 40:
        score += 5
        reasons.append(f"freq suivable {tpd:.0f}/j")
    elif tpd <= 80:
        score += 1
    else:
        score -= 6
        reasons.append(f"trop fréquent {tpd:.0f}/j")

    # 4) WR stability + W/L (weight ~15)
    if m["wr_sigma"] is not None:
        if m["wr_sigma"] < 8:
            score += 6
            reasons.append(f"WR stable σ={m['wr_sigma']:.0f}")
        elif m["wr_sigma"] < 18:
            score += 2
        else:
            score -= 4
            reasons.append(f"WR instable σ={m['wr_sigma']:.0f}")
    if m["wl_ratio"] >= 1.2:
        score += 5
        reasons.append(f"W/L {m['wl_ratio']:.2f}")
    elif m["wl_ratio"] >= 1.0:
        score += 2
    else:
        score -= 3
        reasons.append(f"W/L <1 ({m['wl_ratio']:.2f})")

    # hard penalties
    if arb:
        score -= 15
        reasons.append("tag arbitrage — latence copie")
    if rotation and rotation not in ("non", "n/a", "", None):
        if "oui" in str(rotation).lower() or "suspect" in str(rotation).lower():
            score -= 10
            reasons.append(f"rotation {rotation}")

    # bands
    if score >= 45 and m["survives_05"] and sn <= 40 and age >= 20 and m["span_d"] >= 14:
        band = "COPIABLE"
    elif score >= 20 and (m["survives_05"] or (m["pf"] >= 1.2 and sn <= 35)):
        band = "RISQUÉ"
    else:
        band = "À ÉVITER"
    # override: cannot be COPIABLE if snipes >45 or edge dies at 0.5
    if band == "COPIABLE" and (sn > 45 or not m["survives_05"]):
        band = "RISQUÉ"
        reasons.append("rétrogradé: snipes ou friction")
    return score, band, "; ".join(reasons[:6])


def proxy_from_prior(f: dict, r: dict) -> dict:
    """Cheap proxy when we don't deep-fetch."""
    cost = float(r.get("cost_usd") or 0)
    pnl = float(r.get("pnl_usd") if r.get("pnl_usd") is not None else f.get("pnl_usd") or 0)
    n = int(f.get("n_closed") or r.get("n_closed") or 0)
    wr = float(f.get("wr") or r.get("wr") or 0)
    # approximate PF from WR and roi is weak — mark pf unknown via roi
    roi = float(r.get("roi_pct") or f.get("roi_pct") or 0)
    # crude: assume avg structure — PF ≈ (wr/100 * avg_mult) / ((1-wr/100)) — skip, use roi proxy
    notional = 2 * cost + pnl if cost else 0
    e05 = pnl - 0.005 * notional if notional else pnl
    e10 = pnl - 0.01 * notional if notional else pnl
    snipe = f.get("pct_snipe")
    snipe = float(snipe) if snipe not in (None, "") else 99.0
    span = float(f.get("trade_span_days") or f.get("span_days") or r.get("span_days") or 0)
    tpd = float(f.get("trades_per_day") or (n / span if span else 999))
    dd = f.get("max_dd_pct")
    try:
        dd_pct = float(str(dd).replace(">", "")) if dd not in (None, "") else 999.0
    except Exception:
        dd_pct = 999.0
    return {
        "n": n, "wr": wr, "pnl": pnl, "cost": cost,
        "pf": max(0.5, 1.0 + roi / 100) if roi else 1.0,  # weak proxy
        "avg_win": 0, "avg_loss": 0, "wl_ratio": 1.0,
        "e05": e05, "e10": e10,
        "survives_05": e05 > 0, "survives_10": e10 > 0,
        "conc10_pct": 50.0,  # unknown
        "snipe_pct": snipe, "med_hold_s": 0,
        "span_d": span, "tpd": tpd,
        "max_dd_usd": 0, "max_dd_pct_peak": dd_pct,
        "open_dd_usd": 0, "n_recovered_dd": 0, "longest_recovered_dd_d": 0, "open_dd_d": 0,
        "wr_sigma": None, "wr_range": str(f.get("wr_stability") or "n/a"),
        "pos_weeks": 0, "n_weeks": 0, "median_cost": float(f.get("median_usd") or 0),
        "proxy": True,
    }


def main():
    env = load_env(ROOT / ".env")
    api_key = env.get("HELIUS_API_KEY") or ""
    if not api_key:
        print("NO_HELIUS", file=sys.stderr)
        sys.exit(1)
    sol = get_sol_price()
    print(f"sol_usd≈{sol:.2f} (price only)", flush=True)

    fit_path = OUT / "copy_fitness_all.json"
    if not fit_path.exists():
        fit_path = WS / "copy_fitness_all.json"
    res_path = OUT / "results_all.json"
    if not res_path.exists():
        res_path = WS / "results_all.json"
    fitness = json.loads(fit_path.read_text())
    results = {r["wallet"]: r for r in json.loads(res_path.read_text())}

    # Select deep-fetch targets: survive friction proxy OR (low snipe + age + not huge loser)
    targets = []
    for f in fitness:
        w = f["wallet"]
        if w in DROP:
            continue
        age = float(f.get("age_days") or 0)
        span = float(f.get("trade_span_days") or f.get("span_days") or 0)
        disp = f.get("disposable") in (True, "True", "true")
        if disp or span < 7 or age < 10:
            continue
        r = results.get(w, {})
        proxy = proxy_from_prior(f, r)
        snipe = proxy["snipe_pct"]
        # deep if: e05>0 with age>=20, OR (snipe<=35 and age>=20 and span>=14 and pnl proxy > -2000)
        if (proxy["survives_05"] and age >= 20 and span >= 10) or (
            snipe <= 35 and age >= 20 and span >= 14 and proxy["pnl"] > -3000 and proxy["n"] >= 100
        ):
            # skip pure arb with rotation oui unless edge huge
            if f.get("arb_hint") and str(f.get("rotation") or "").lower().startswith("oui"):
                if proxy["e10"] <= 0:
                    continue
            targets.append((w, f, r, proxy))

    # Cap deep fetches — prioritize by e05 then low snipe then age
    targets.sort(key=lambda t: (
        0 if t[3]["survives_10"] else 1 if t[3]["survives_05"] else 2,
        t[3]["snipe_pct"],
        -float(t[1].get("age_days") or 0),
        -t[3]["e05"],
    ))
    # Always include these if present
    prefer = [
        "hnu69n6P5CgYXCtUKii9wgamqtDeTVHY3TVJ6HKt7wC",
        "DJze9rksBmd1VsLXCZeE7uU92rGVuGd2e9q8WmDeC5Rv",
        "63h1KZSKzVmS24mcB4A3G2fszzf2nM3P2XVEQBqU8hsc",
        "BqJHX59hPud6T2SXX1WKAQ9eJPTyC9xPtexWhrt2r2Dp",
        "DXRt3NqEGMYRr4Q8XWLSUfau2XKHHAEcApR7bC7bVZ2K",
        "GHNCDK6dxhothbdYKMx3UFC6rP1GJshEF9rwBZECZS1J",
        "8n79PwcyrybEECM3ghGetPnm6Y2SRBVzJNd536Z1C1PU",
        "7uDFnn6ZUd7tkwYLwMisCezsKds8XAi436iufBSRhJTC",
        "8mjGW2WzVrLcxHydbA5eAgJJrJwRGiAPr9h4cVXh4LeM",
        "58opGgFq15Ew8QbHqkQjpnYNUFrZrsu2jZLNTRfuQ387",
    ]
    deep_set = []
    seen = set()
    for w in prefer:
        hit = next((t for t in targets if t[0] == w), None)
        if hit and w not in seen:
            deep_set.append(hit)
            seen.add(w)
    for t in targets:
        if t[0] not in seen:
            deep_set.append(t)
            seen.add(t[0])
        if len(deep_set) >= 14:
            break

    print(f"deep targets: {len(deep_set)}", flush=True)
    deep_metrics = {}
    for i, (w, f, r, proxy) in enumerate(deep_set):
        pages = 30 if float(f.get("age_days") or 0) >= 40 else 20
        print(f"[{i+1}/{len(deep_set)}] fetch {w[:8]}… pages≤{pages}", flush=True)
        txs, err = fetch_enhanced(api_key, w, pages)
        if err:
            print(f"  err={err} n={len(txs)}", flush=True)
        events = parse_events(txs, w)
        closed = fifo_closed(events, sol)
        m = analyze_closed(closed)
        m["enhanced_n"] = len(txs)
        m["err"] = err
        m["proxy"] = False
        if m["n"] < 30:
            # fall back to proxy but mark
            m = {**proxy, "enhanced_n": len(txs), "err": err or "few_closed", "proxy": True, "fallback": True}
        deep_metrics[w] = m
        # persist closed for later light DD (no secrets)
        try:
            cpath = OUT / f"robust_v2_closed_{w[:8]}.json"
            cpath.write_text(json.dumps({
                "wallet": w,
                "sol_usd": sol,
                "n_txs": len(txs),
                "closed": closed,
            }))
        except Exception as ex:
            print(f"  warn save closed: {type(ex).__name__}", flush=True)
        print(
            f"  n={m['n']} wr={m.get('wr',0):.1f} pf={m.get('pf',0):.2f} e05={m.get('e05',0):.0f} "
            f"e10={m.get('e10',0):.0f} snipe={m.get('snipe_pct',0):.1f} dd%={m.get('max_dd_pct_peak',0):.1f} "
            f"conc10={m.get('conc10_pct',0):.0f}",
            flush=True,
        )
        time.sleep(0.3)

    # Score ALL non-excluded wallets (deep if available else proxy)
    rows = []
    for f in fitness:
        w = f["wallet"]
        if w in DROP:
            continue
        age = float(f.get("age_days") or 0)
        span = float(f.get("trade_span_days") or f.get("span_days") or 0)
        disp = f.get("disposable") in (True, "True", "true")
        if disp or span < 7 or age < 10:
            # still record as excluded
            rows.append({
                "wallet": w, "band": "À ÉVITER", "score": -1000, "depth": "excluded",
                "age_days": age, "span_d": span, "why": "exclu: disposable / span<7j / âge<10j",
                "prior_verdict": f.get("verdict"),
            })
            continue
        r = results.get(w, {})
        m = deep_metrics.get(w) or proxy_from_prior(f, r)
        score, band, why = score_row(
            m, age, f.get("verdict") or "", bool(f.get("arb_hint") or r.get("arb")),
            f.get("rotation") or "",
        )
        rows.append({
            "wallet": w,
            "band": band,
            "score": round(score, 1),
            "depth": "deep" if w in deep_metrics and not m.get("proxy") else ("proxy_deep_fail" if w in deep_metrics else "proxy"),
            "age_days": round(age, 1),
            "span_d": round(m.get("span_d") or span, 2),
            "n_closed": m.get("n"),
            "wr": round(m.get("wr") or 0, 1),
            "pf": round(m.get("pf") or 0, 3),
            "pnl": round(m.get("pnl") or 0, 1),
            "e05": round(m.get("e05") or 0, 1),
            "e10": round(m.get("e10") or 0, 1),
            "survives_05": m.get("survives_05"),
            "survives_10": m.get("survives_10"),
            "snipe_pct": round(m.get("snipe_pct") or 0, 1),
            "tpd": round(m.get("tpd") or 0, 1),
            "max_dd_pct_peak": round(m.get("max_dd_pct_peak") or 0, 1),
            "open_dd_usd": round(m.get("open_dd_usd") or 0, 1),
            "open_dd_d": round(m.get("open_dd_d") or 0, 1),
            "conc10_pct": round(m.get("conc10_pct") or 0, 1),
            "wl_ratio": round(m.get("wl_ratio") or 0, 2),
            "wr_range": m.get("wr_range"),
            "wr_sigma": None if m.get("wr_sigma") is None else round(m["wr_sigma"], 1),
            "median_cost": round(m.get("median_cost") or 0, 1),
            "arb": bool(f.get("arb_hint") or r.get("arb")),
            "rotation": f.get("rotation"),
            "prior_verdict": f.get("verdict"),
            "why": why,
        })

    rows.sort(key=lambda x: (-x.get("score", -9999), -float(x.get("e05") or -1e18)))

    DEST.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for base in (DEST, OUT):
        with (base / "copy_robust_v2.csv").open("w", newline="") as fh:
            fields = [
                "wallet", "band", "score", "depth", "age_days", "span_d", "n_closed", "wr", "pf",
                "pnl", "e05", "e10", "survives_05", "survives_10", "snipe_pct", "tpd",
                "max_dd_pct_peak", "open_dd_usd", "open_dd_d", "conc10_pct", "wl_ratio",
                "wr_range", "wr_sigma", "median_cost", "arb", "rotation", "prior_verdict", "why",
            ]
            wri = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            wri.writeheader()
            for row in rows:
                wri.writerow(row)
        (base / "copy_robust_v2.json").write_text(json.dumps({
            "generated_zurich": datetime.now(TZ).isoformat(timespec="seconds"),
            "sol_usd": sol,
            "criteria": {
                "priority": [
                    "edge after 0.5–1%/side copy friction",
                    "DD quality + low top10 concentration",
                    "age / long span / low snipes / followable freq",
                    "weekly WR stability + win/loss >1",
                ],
                "deprioritized": ["annualized ROI", "raw WR alone"],
                "hard_excludes": list(DROP) + ["disposable", "span<7d", "age<10d"],
            },
            "deep_wallets": list(deep_metrics.keys()),
            "rows": rows,
        }, indent=2))

    # Shortlist top
    short = [r for r in rows if r["band"] in ("COPIABLE", "RISQUÉ") and r.get("depth") == "deep"]
    short = short[:8]
    # If no COPIABLE, say so
    copiable = [r for r in rows if r["band"] == "COPIABLE"]
    risque_deep = [r for r in rows if r["band"] == "RISQUÉ" and r.get("depth") == "deep"]

    md = []
    md.append("# Copy robustesse v2 (PAPER) — critères ≠ copy_fitness_all / ROI-ann")
    md.append("")
    md.append(f"Généré (Europe/Zurich) : {datetime.now(TZ).isoformat(timespec='seconds')}")
    md.append("")
    md.append("## Pourquoi ce ranking change")
    md.append("- **Avant** (`copy_fitness_all`) : âge + snipes bas + suivabilité → `3Dnwtnk` #1, mais edge fragile aux frais/latence copie.")
    md.append("- **Avant** (shortlist ROI-ann) : WR>60 + perf annualisée gonflée → wallets jeunes / snipers.")
    md.append("- **Maintenant** : l’edge doit **survivre à +0.5–1 %/côté** sur le notionnel ; DD récupéré / peu concentré ; snipes & fréquence ensuite. ROI annualisé et WR brut sont dépriorisés.")
    md.append("- Exclus durs : `3Dnwtnk…`, jetables, span <7 j, âge <~10 j.")
    md.append("")
    md.append("## Poids (ordre)")
    md.append("1. Profit factor + PnL restant après friction 0.5 % et 1 %/côté (~40 %)")
    md.append("2. Qualité DD (profondeur, ouvert vs récupéré) + concentration top-10 winners (~20 %)")
    md.append("3. Âge / span long, % snipes <30 s, trades/jour suivables (~25 %)")
    md.append("4. Stabilité WR hebdo + ratio gain moyen / perte moyenne (~15 %)")
    md.append("")
    md.append(f"## Résumé bandes (hors exclus durs traités à part)")
    from collections import Counter as C
    md.append(f"- COPIABLE : {sum(1 for r in rows if r['band']=='COPIABLE')}")
    md.append(f"- RISQUÉ : {sum(1 for r in rows if r['band']=='RISQUÉ')}")
    md.append(f"- À ÉVITER : {sum(1 for r in rows if r['band']=='À ÉVITER')}")
    md.append(f"- Deep Helius recalculés : {len(deep_metrics)}")
    md.append("")
    md.append("## Shortlist 3–5 (nouveaux critères)")
    md.append("")
    if not copiable and not risque_deep:
        md.append("**Aucun assez solide** pour un bras copy PAPER sous ces critères.")
    else:
        picks = (copiable + [r for r in risque_deep if r not in copiable])[:5]
        md.append("| # | Wallet | Band | Score | Âge j | Span | WR | PF | e05$ | e10$ | Snipes% | DD%peak | Conc10% | Pourquoi |")
        md.append("|---|--------|------|-------|-------|------|----|----|------|------|---------|---------|---------|----------|")
        for i, r in enumerate(picks, 1):
            md.append(
                f"| {i} | `{r['wallet']}` | {r['band']} | {r['score']} | {r['age_days']} | {r['span_d']} | "
                f"{r['wr']} | {r['pf']} | {r['e05']} | {r['e10']} | {r['snipe_pct']} | "
                f"{r['max_dd_pct_peak']} | {r['conc10_pct']} | {r['why'][:80]} |"
            )
        md.append("")
        if not copiable:
            md.append("> Aucun **COPIABLE** strict. Les RISQUÉ ci-dessus sont les moins mauvais ; lancer seulement en micro PAPER ou attendre.")
    md.append("")
    md.append("## Top deep recalculés (tous)")
    md.append("")
    md.append("| Wallet | Band | Score | PF | e05 | e10 | Snipe | Âge | Prior |")
    md.append("|--------|------|-------|----|-----|-----|-------|-----|-------|")
    for r in rows:
        if r.get("depth") != "deep":
            continue
        md.append(
            f"| `{r['wallet'][:12]}…` | {r['band']} | {r['score']} | {r['pf']} | {r['e05']} | {r['e10']} | "
            f"{r['snipe_pct']} | {r['age_days']} | {r['prior_verdict']} |"
        )
    md.append("")
    md.append("Fichiers : `copy_robust_v2.csv` / `.json` / `.md`")
    text = "\n".join(md) + "\n"
    for base in (DEST, OUT):
        (base / "copy_robust_v2.md").write_text(text)

    # Pick #1 for light DD: best COPIABLE else best RISQUÉ deep with survives_05
    candidates_dd = copiable or [r for r in risque_deep if r.get("survives_05")]
    if not candidates_dd:
        candidates_dd = risque_deep[:1]
    top1 = candidates_dd[0] if candidates_dd else None
    meta = {"top1": top1["wallet"] if top1 else None, "shortlist": [r["wallet"] for r in (copiable + risque_deep)[:5]]}
    (DEST / "copy_robust_v2_meta.json").write_text(json.dumps(meta, indent=2))
    (OUT / "copy_robust_v2_meta.json").write_text(json.dumps(meta, indent=2))
    # save closed for top1 if we have deep
    if top1 and top1["wallet"] in deep_metrics:
        # re-fetch closed already in memory — recompute from last analyze: store closed in deep pass
        pass
    print("TOP1", meta["top1"])
    print("SHORT", meta["shortlist"])
    print("DONE", DEST)


if __name__ == "__main__":
    main()
