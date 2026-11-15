#!/usr/bin/env python3
"""PAPER copy-fitness for up to ~100 candidats.
Priority: age/long span, no monthly rotation, WR stability, followable size/freq
— BEFORE inflated annualized ROI.
Reuses copy_fitness_9.json when present. Never prints secrets.
"""
from __future__ import annotations
import csv, json, os, sys, time, urllib.request, urllib.error, statistics
from collections import defaultdict, deque, Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
QUOTE = {WSOL, USDC, USDT, "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"}
SNIPE_S = 30
SLEEP = 0.18

ROOT = Path.home() / "Projects" / "CryptobBot"
OUT = ROOT / "scripts" / "out"
WS = Path("/workspace/wallets_alpha")


def load_env(path: Path) -> dict:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def helius_txs(api_key: str, wallet: str, before: Optional[str] = None):
    q = f"api-key={api_key}&limit=100"
    if before:
        q += f"&before={before}"
    url = f"https://api.helius.xyz/v0/addresses/{wallet}/transactions?{q}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=50) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        return [], f"http_{e.code}"
    except Exception as e:
        return [], type(e).__name__


def rpc_call(api_key: str, method: str, params: list) -> Any:
    url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            data = json.loads(r.read())
        if "error" in data:
            return None
        return data.get("result")
    except Exception:
        return None


def get_sol_price() -> float:
    try:
        req = urllib.request.Request(
            "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return float(json.loads(r.read())["solana"]["usd"])
    except Exception:
        return 160.0


def oldest_sig_age(api_key: str, wallet: str, max_pages: int):
    before = None
    first_ts = last_ts = None
    n = 0
    exhausted = False
    for _ in range(max_pages):
        opts = {"limit": 1000}
        if before:
            opts["before"] = before
        res = rpc_call(api_key, "getSignaturesForAddress", [wallet, opts])
        if not res:
            exhausted = True
            break
        if last_ts is None and res[0].get("blockTime"):
            last_ts = int(res[0]["blockTime"])
        n += len(res)
        before = res[-1]["signature"]
        if res[-1].get("blockTime"):
            first_ts = int(res[-1]["blockTime"])
        if len(res) < 1000:
            exhausted = True
            break
        time.sleep(0.1)
    return first_ts, last_ts, n, exhausted


def fetch_enhanced(api_key: str, wallet: str, max_pages: int):
    all_txs = []
    before = None
    last_err = None
    for _ in range(max_pages):
        batch, err = helius_txs(api_key, wallet, before)
        if err:
            last_err = err
            if "429" in err:
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
            adjusted.append(group[0]); continue
        total_abs = sum(g["amount"] for g in group) or 1.0
        q, s = group[0]["quote_usd"], group[0]["sol_delta"]
        for g in group:
            w = g["amount"] / total_abs
            ng = dict(g); ng["quote_usd"] = q * w; ng["sol_delta"] = s * w
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
                    "pnl": proc_take - cost_take, "cost": cost_take, "mint": mint,
                    "hold_s": max(0, e["ts"] - lot_ts),
                    "exit_ts": e["ts"], "entry_ts": lot_ts, "size_usd": cost_take,
                })
                lot_amt -= take; lot_cost -= cost_take; rem -= take
                if lot_amt <= 1e-12:
                    lots[mint].popleft()
                else:
                    lots[mint][0] = [lot_amt, lot_cost, lot_ts]
    closed.sort(key=lambda c: c["exit_ts"])
    return closed


def max_dd_pct(closed):
    if not closed:
        return 0.0
    equity = peak = 0.0
    max_dd = 0.0
    for c in closed:
        equity += c["pnl"]
        if equity > peak:
            peak = equity
        if peak > 50:  # ignore tiny early peaks
            dd = (peak - equity) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return max_dd


def wr_stability(closed):
    if not closed:
        return "n/a", 0.0
    buckets = defaultdict(list)
    for c in closed:
        buckets[int(c["exit_ts"] // (7 * 86400))].append(1 if c["pnl"] > 0 else 0)
    wrs = [sum(xs)/len(xs)*100 for xs in buckets.values() if len(xs) >= 5]
    if len(wrs) < 2:
        days = defaultdict(list)
        for c in closed:
            days[int(c["exit_ts"] // 86400)].append(1 if c["pnl"] > 0 else 0)
        drs = [sum(xs)/len(xs)*100 for xs in days.values() if len(xs) >= 5]
        if len(drs) >= 2:
            st = statistics.pstdev(drs)
            return f"jours σ={st:.0f}", st
        return "trop court", 0.0
    st = statistics.pstdev(wrs)
    mn, mx = min(wrs), max(wrs)
    if st < 8:
        return f"stable ({mn:.0f}–{mx:.0f}%)", st
    if st < 18:
        return f"moyen ({mn:.0f}–{mx:.0f}%)", st
    return f"instable ({mn:.0f}–{mx:.0f}%)", st


def funding(txs, wallet):
    inbound = defaultdict(list)
    for tx in txs:
        ts = tx.get("timestamp") or 0
        for n in tx.get("nativeTransfers") or []:
            if n.get("toUserAccount") != wallet:
                continue
            frm = n.get("fromUserAccount") or ""
            if not frm or frm == wallet:
                continue
            amt = float(n.get("amount") or 0) / 1e9
            if amt < 0.05:
                continue
            inbound[frm].append((amt, ts))
    agg = [(f, sum(a for a,_ in items), min(t for _,t in items)) for f, items in inbound.items()]
    agg.sort(key=lambda x: -x[1])
    return agg


def programs(txs):
    c = Counter()
    for tx in txs:
        src = (tx.get("source") or "UNKNOWN").upper()
        if "PUMP" in src:
            c["PUMP_FUN"] += 1
        elif "RAYDIUM" in src:
            c["RAYDIUM"] += 1
        elif "JUPITER" in src:
            c["JUPITER"] += 1
        elif "ORCA" in src:
            c["ORCA"] += 1
        elif "METEORA" in src:
            c["METEORA"] += 1
        else:
            c[src[:16]] += 1
    return c


def wash_signals(closed):
    if not closed:
        return "n/a"
    total_pnl = sum(c["pnl"] for c in closed)
    total_cost = sum(c["cost"] for c in closed) or 1.0
    flags = []
    if abs(total_pnl) / total_cost < 0.02 and total_cost > 20000:
        flags.append("PnL minuscule vs volume")
    pct_1s = sum(1 for c in closed if c["hold_s"] <= 1) / len(closed) * 100
    if pct_1s > 25:
        flags.append(f"{pct_1s:.0f}% holds ≤1s")
    tiny = sum(1 for c in closed if abs(c["pnl"]) < 0.05 and c["cost"] > 5)
    if tiny / len(closed) > 0.3:
        flags.append("aller-retour ~0$")
    return "; ".join(flags) if flags else "non évident"


def tier_for(prior: dict) -> tuple[str, int, int]:
    """Return (tier, enh_pages, sig_pages). Deep on long-span / high-WR."""
    span = float(prior.get("span_days") or 0)
    wr = float(prior.get("wr") or 0)
    n = int(prior.get("n_closed") or 0)
    # forced deep names
    w = prior.get("wallet") or ""
    force_deep = {
        "hnu69n6P5CgYXCtUKii9wgamqtDeTVHY3TVJ6HKt7wC",
        "GAAcxw3qcRmQTzm34UGQLz6q5V33Wk7vyugMPWXZKAbn",
        "DJze9rksBmd1VsLXCZeE7uU92rGVuGd2e9q8WmDeC5Rv",
        "GHNCDK6dxhothbdYKMx3UFC6rP1GJshEF9rwBZECZS1J",
        "3DnwtnkPxgCEmaxcpYviEUNFqoED6X2icoY2KdhLXaQS",
        "8mjGW2WzVrLcxHydbA5eAgJJrJwRGiAPr9h4cVXh4LeM",
        "DaDtxe6jcjeGHDHF6uQMPoaMwT79occaxyivGKosLRNT",
        "8n79PwcyrybEECM3ghGetPnm6Y2SRBVzJNd536Z1C1PU",
    }
    if w in force_deep or (span >= 25 and wr >= 55 and n >= 100):
        return "deep", 30, 40
    if span >= 20 and wr >= 50 and n >= 100:
        return "mid", 15, 15
    if span >= 14 and wr >= 55:
        return "mid", 12, 12
    if span < 7:
        return "light", 0, 3  # age peek only
    return "light", 5, 5


def verdict(r):
    age = r.get("age_days")
    span = r.get("trade_span_days") or r.get("span_days") or 0
    dd = r.get("max_dd_pct")
    snipe = r.get("pct_snipe")
    wash = r.get("wash") or ""
    arb = r.get("arb_hint")
    wr = r.get("wr") or 0
    tpd = r.get("trades_per_day")
    depth = r.get("depth") or "light"
    roi = r.get("roi_pct") or 0

    if arb:
        return "À ÉVITER", "Taggé arbitrageur — latence copy trop élevée."
    if age is not None and age < 5:
        return "À ÉVITER", "Wallet quasi neuf → pattern jetable / rotation."
    if span < 3 and (r.get("n_closed") or 0) > 20:
        return "À ÉVITER", "Historique de trading trop court — stats trompeuses."
    if snipe is not None and snipe > 60 and (span < 40 or wr < 70):
        return "À ÉVITER", f"Trop de snipes ({snipe:.0f}% <30s) — injouable en copy."
    if "PnL minuscule" in wash and (snipe or 0) > 35:
        return "À ÉVITER", "Signes wash + snipes — pas copiable."

    # Light-only path: use prior span/wr without snipe
    if depth == "light" and snipe is None:
        if span < 7:
            return "À ÉVITER", f"Span trading ~{span:.1f} j seulement → trop jeune pour copy PAPER."
        if span < 14:
            return "RISQUÉ", f"Historique court (~{span:.0f} j), pas creusé en profondeur."
        if wr < 50:
            return "À ÉVITER", f"WR {wr:.0f}% trop bas pour un bras copy (span {span:.0f} j)."
        if wr >= 55 and span >= 25 and roi >= -5:
            return "RISQUÉ", f"Profil intéressant sur papier (span {span:.0f} j, WR {wr:.0f}%) mais analyse légère — à creuser."
        if wr >= 50 and span >= 40:
            return "RISQUÉ", f"Long historique (~{span:.0f} j) mais WR {wr:.0f}% juste moyen."
        return "RISQUÉ", f"Span {span:.0f} j / WR {wr:.0f}% — pas prioritaire."

    reasons = []
    if age is not None and age < 40 and span < 20:
        reasons.append("encore jeune")
    if snipe is not None and snipe > 35:
        reasons.append(f"{snipe:.0f}% snipes <30s")
    if dd is not None and dd > 80:
        reasons.append("PnL très chaotique")
    elif dd is not None and dd > 40:
        reasons.append(f"DD ~{min(dd,99):.0f}%")
    if "instable" in (r.get("wr_stability") or ""):
        reasons.append("WR instable")
    if "oui" in (r.get("cluster") or "").lower():
        reasons.append("cluster/rotation")
    if tpd and tpd > 100:
        reasons.append(f"fréquence extrême (~{tpd:.0f}/j)")
    if roi < -10:
        reasons.append(f"ROI période {roi:.0f}%")

    # COPIABLE strict
    age_ok = (age or 0) >= 45 or ((age or 0) >= 30 and span >= 25)
    span_ok = span >= 20
    snipe_ok = snipe is None or snipe < 35
    dd_ok = dd is None or dd < 45
    wr_ok = wr >= 55
    if age_ok and span_ok and snipe_ok and dd_ok and wr_ok and not arb and roi > -5:
        return "COPIABLE", (
            f"Âge ~{age:.0f} j, trading ~{span:.0f} j, WR {wr:.0f}%, snipes gérables "
            f"({snipe if snipe is not None else '?'}%) — meilleur profil pour bras PAPER."
        )

    # soft COPIABLE: long span, decent WR, moderate snipes
    if span >= 50 and wr >= 55 and (snipe is None or snipe < 45) and roi > -20 and not arb:
        why = f"Un des plus longs historiques (~{span:.0f} j), WR {wr:.0f}%"
        if reasons:
            why += " — vigilance: " + "; ".join(reasons[:2])
        why += "."
        if snipe is not None and snipe > 35:
            return "RISQUÉ", why
        return "COPIABLE", why

    if reasons:
        return "RISQUÉ", "; ".join(reasons[:3]) + "."
    if wr >= 60 and span >= 14:
        return "RISQUÉ", f"WR élevé ({wr:.0f}%) mais à valider (âge/snipes/DD)."
    return "RISQUÉ", "Profil mitigé — pas prioritaire pour un bras copy."


def analyze_deep(api_key, wallet, prior, sol_usd, enh_pages, sig_pages):
    txs, err = fetch_enhanced(api_key, wallet, enh_pages) if enh_pages else ([], None)
    first_ts, last_ts, n_sigs, exhausted = oldest_sig_age(api_key, wallet, sig_pages)
    now = int(time.time())
    age = (now - first_ts) / 86400.0 if first_ts else None
    # if not exhausted and age < prior span, age is floor wrong — use max with span
    prior_span = float(prior.get("span_days") or 0)
    if age is not None and not exhausted and age < prior_span:
        age = prior_span  # underestimate guard when pages maxed
        age_note = "plancher (≥span, sigs denses)"
    elif age is not None and not exhausted:
        age_note = "plancher (pagination max)"
    else:
        age_note = "ok" if exhausted else "?"

    closed = []
    events = []
    if txs:
        events = parse_events(txs, wallet)
        closed = fifo_closed(events, sol_usd)

    if closed:
        span = (closed[-1]["exit_ts"] - closed[0]["entry_ts"]) / 86400.0
        n_closed = len(closed)
        wr = sum(1 for c in closed if c["pnl"] > 0) / n_closed * 100
        dd = max_dd_pct(closed)
        wr_lab, wr_st = wr_stability(closed)
        pct_snipe = sum(1 for c in closed if c["hold_s"] < SNIPE_S) / n_closed * 100
        sizes = [c["size_usd"] for c in closed if c["size_usd"] > 0.01]
        med = statistics.median(sizes) if sizes else 0
        tpd = n_closed / span if span > 0.01 else n_closed
        wash = wash_signals(closed)
        pnl = sum(c["pnl"] for c in closed)
        cost = sum(c["cost"] for c in closed)
        funders = funding(txs, wallet)[:5]
        prog = ", ".join(f"{k}:{v}" for k,v in programs(txs).most_common(4))
    else:
        span = prior_span
        n_closed = prior.get("n_closed") or 0
        wr = prior.get("wr") or 0
        dd = None
        wr_lab, wr_st = "agrégat prior", 0.0
        pct_snipe = None
        med = None
        tpd = (n_closed / span) if span > 0.01 else None
        wash = "n/a (light/prior)"
        pnl = prior.get("pnl_usd")
        cost = prior.get("cost_usd")
        funders = []
        prog = "?"

    # age vs span disposable
    disposable = False
    if age is not None and age < 12:
        disposable = True
    if span < 3 and n_closed > 20:
        disposable = True

    return {
        "wallet": wallet,
        "age_days": round(age, 1) if age is not None else None,
        "age_note": age_note,
        "n_sigs_seen": n_sigs,
        "age_exhausted": exhausted,
        "trade_span_days": round(span, 2),
        "span_days": prior_span,
        "lifetime_vs_span": (
            f"âge {age:.0f}j / trade {span:.1f}j ({age_note})"
            if age is not None else f"trade {span:.1f}j (âge ?)"
        ),
        "disposable": disposable,
        "top_funders": [{"from": f[:12]+"…", "sol": round(s,3)} for f,s,_ in funders],
        "top_funder_full": [f for f,s,_ in funders[:3]],
        "n_closed": n_closed,
        "wr": round(wr, 1),
        "max_dd_pct": round(dd, 1) if dd is not None else None,
        "wr_stability": wr_lab,
        "trades_per_day": round(tpd, 1) if tpd is not None else None,
        "median_usd": round(med, 2) if med is not None else None,
        "pct_snipe": round(pct_snipe, 1) if pct_snipe is not None else None,
        "programs": prog,
        "wash": wash,
        "arb_hint": "arbitrager" in (prior.get("sources") or "").lower(),
        "pnl_usd": round(pnl, 1) if pnl is not None else None,
        "roi_pct": prior.get("roi_pct"),
        "roi_ann_est": prior.get("roi_ann_est"),
        "prior_sources": prior.get("sources") or "",
        "enhanced_n": len(txs),
        "err": err,
    }


def analyze_light(api_key, wallet, prior, sig_pages=3):
    """Age peek + prior stats only."""
    first_ts, last_ts, n_sigs, exhausted = oldest_sig_age(api_key, wallet, sig_pages)
    now = int(time.time())
    age = (now - first_ts) / 86400.0 if first_ts else None
    span = float(prior.get("span_days") or 0)
    if age is not None and not exhausted and age < span:
        age = span
        age_note = "plancher"
    else:
        age_note = "ok" if exhausted else ("plancher" if not exhausted else "?")
    n_closed = prior.get("n_closed") or 0
    wr = prior.get("wr") or 0
    disposable = (age is not None and age < 12) or span < 7
    tpd = (n_closed / span) if span > 0.01 else None
    return {
        "wallet": wallet,
        "age_days": round(age, 1) if age is not None else None,
        "age_note": age_note,
        "n_sigs_seen": n_sigs,
        "age_exhausted": exhausted,
        "trade_span_days": round(span, 2),
        "span_days": span,
        "lifetime_vs_span": (
            f"âge {age:.0f}j / trade {span:.1f}j ({age_note})"
            if age is not None else f"trade {span:.1f}j (âge ?)"
        ),
        "disposable": disposable,
        "top_funders": [],
        "top_funder_full": [],
        "n_closed": n_closed,
        "wr": round(wr, 1),
        "max_dd_pct": None,
        "wr_stability": "agrégat prior",
        "trades_per_day": round(tpd, 1) if tpd is not None else None,
        "median_usd": None,
        "pct_snipe": None,
        "programs": "?",
        "wash": "n/a (light)",
        "arb_hint": "arbitrager" in (prior.get("sources") or "").lower(),
        "pnl_usd": prior.get("pnl_usd"),
        "roi_pct": prior.get("roi_pct"),
        "roi_ann_est": prior.get("roi_ann_est"),
        "prior_sources": prior.get("sources") or "",
        "enhanced_n": 0,
        "err": None,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    env = load_env(ROOT / ".env")
    key = env.get("HELIUS_API_KEY") or ""
    if not key:
        print("NO_HELIUS_KEY", file=sys.stderr); sys.exit(1)
    print(f"helius_len={len(key)}")

    # Load priors
    prior_path = OUT / "results_all.json"
    if not prior_path.exists():
        prior_path = WS / "results_all.json"
    priors = {r["wallet"]: r for r in json.loads(prior_path.read_text()) if r.get("wallet")}
    print(f"priors={len(priors)}")

    # Resume from prior 9 deep analyses
    done = {}
    for p in (OUT / "copy_fitness_9.json", OUT / "copy_fitness_all_partial.json"):
        if p.exists():
            for r in json.loads(p.read_text()):
                if r.get("wallet") and r.get("pct_snipe") is not None:
                    done[r["wallet"]] = r
    print(f"resume_deep={len(done)}")

    sol = get_sol_price()
    print(f"sol={sol:.2f}")

    # Order: deep first by score = span*wr with bonuses
    items = list(priors.values())
    def rank(p):
        span = float(p.get("span_days") or 0)
        wr = float(p.get("wr") or 0)
        return (span >= 25 and wr >= 55, span >= 14 and wr >= 60, span, wr)
    items.sort(key=rank, reverse=True)

    results = []
    funder_map = defaultdict(list)

    for i, prior in enumerate(items):
        w = prior["wallet"]
        tier, enh_p, sig_p = tier_for(prior)
        print(f"\n[{i+1}/{len(items)}] {w[:10]}… tier={tier} span={prior.get('span_days',0):.1f} WR={prior.get('wr',0):.1f}", flush=True)

        if w in done and tier == "deep":
            # reuse if we already have snipes etc.
            r = dict(done[w])
            r["depth"] = "deep"
            r["roi_pct"] = prior.get("roi_pct")
            r["roi_ann_est"] = prior.get("roi_ann_est")
            r.setdefault("trade_span_days", prior.get("span_days"))
            r.setdefault("span_days", prior.get("span_days"))
            print(f"  REUSE deep snipes={r.get('pct_snipe')} age={r.get('age_days')}", flush=True)
        elif tier in ("deep", "mid"):
            try:
                r = analyze_deep(key, w, prior, sol, enh_p, sig_p)
            except Exception as e:
                print(f"  FAIL {type(e).__name__}: {e}", flush=True)
                r = analyze_light(key, w, prior, sig_pages=min(sig_p, 5))
            r["depth"] = tier
            print(
                f"  age={r.get('age_days')} span={r.get('trade_span_days')} snipes={r.get('pct_snipe')} "
                f"dd={r.get('max_dd_pct')} tpd={r.get('trades_per_day')} n={r.get('n_closed')}",
                flush=True,
            )
        else:
            try:
                r = analyze_light(key, w, prior, sig_pages=sig_p)
            except Exception as e:
                print(f"  FAIL light {type(e).__name__}", flush=True)
                r = {
                    "wallet": w, "age_days": None, "trade_span_days": prior.get("span_days"),
                    "span_days": prior.get("span_days"), "n_closed": prior.get("n_closed"),
                    "wr": prior.get("wr"), "disposable": True, "wash": "erreur",
                    "top_funder_full": [], "arb_hint": "arbitrager" in (prior.get("sources") or "").lower(),
                    "roi_pct": prior.get("roi_pct"), "roi_ann_est": prior.get("roi_ann_est"),
                    "lifetime_vs_span": f"trade {prior.get('span_days')}j",
                }
            r["depth"] = "light"
            print(f"  LIGHT age={r.get('age_days')} span={r.get('trade_span_days')}", flush=True)

        results.append(r)
        for f in r.get("top_funder_full") or []:
            funder_map[f].append(w)
        (OUT / "copy_fitness_all_partial.json").write_text(json.dumps(results, indent=2, default=str))
        time.sleep(0.05)

    # cluster
    for r in results:
        linked = []
        for f in r.get("top_funder_full") or []:
            others = [x[:8] for x in funder_map.get(f, []) if x != r["wallet"]]
            if others:
                linked.append(f"{f[:8]}→{','.join(others[:4])}")
        r["cluster"] = ("oui: " + "; ".join(linked[:2])) if linked else ("possible jetable" if r.get("disposable") else "non détecté")
        r["rotation"] = "oui" if linked else ("suspect" if r.get("disposable") else "non")
        verd, why = verdict(r)
        if verd == "COPIABLE" and linked:
            verd = "RISQUÉ"
            why = "Funder partagé (rotation possible). " + why
        r["verdict"] = verd
        r["pourquoi"] = why

    # sort output: COPIABLE first, then RISQUÉ by span/wr, then À ÉVITER
    order = {"COPIABLE": 0, "RISQUÉ": 1, "À ÉVITER": 2}
    results.sort(key=lambda r: (
        order.get(r.get("verdict"), 9),
        -(r.get("trade_span_days") or r.get("span_days") or 0),
        -(r.get("wr") or 0),
    ))

    cols = [
        "wallet", "verdict", "depth", "age_days", "lifetime_vs_span", "cluster", "rotation",
        "max_dd_pct", "wr_stability", "trades_per_day", "median_usd", "pct_snipe",
        "wash", "n_closed", "wr", "trade_span_days", "roi_pct", "disposable", "pourquoi",
    ]

    def write_csv(path: Path):
        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in results:
                row = {k: r.get(k) for k in cols}
                if row.get("max_dd_pct") is not None and row["max_dd_pct"] > 100:
                    row["max_dd_pct"] = ">100"
                w.writerow(row)

    def write_md(path: Path):
        lines = []
        lines.append("# Copy-fitness PAPER — tous candidats (~100)")
        lines.append("")
        lines.append(f"_Généré : {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')} (Europe/Zurich)_")
        lines.append("")
        lines.append("**PAPER only.** Critères dans l’ordre : âge / long span → pas de rotation mensuelle → stabilité WR → fréquence/taille suivables → (ROI annualisé en dernier).")
        lines.append("")
        n_c = sum(1 for r in results if r.get("verdict")=="COPIABLE")
        n_r = sum(1 for r in results if r.get("verdict")=="RISQUÉ")
        n_a = sum(1 for r in results if r.get("verdict")=="À ÉVITER")
        n_deep = sum(1 for r in results if r.get("depth") in ("deep","mid"))
        lines.append(f"Comptages : **{len(results)}** wallets — COPIABLE {n_c} · RISQUÉ {n_r} · À ÉVITER {n_a} (creusés deep/mid : {n_deep}).")
        lines.append("")
        lines.append("## Top recommandés pour bras copy PAPER")
        lines.append("")
        picks = [r for r in results if r.get("verdict")=="COPIABLE"][:3]
        if len(picks) < 2:
            for r in results:
                if r.get("verdict")!="RISQUÉ":
                    continue
                if (r.get("trade_span_days") or 0) < 20 and (r.get("age_days") or 0) < 30:
                    continue
                if r.get("arb_hint"):
                    continue
                if r not in picks:
                    picks.append(r)
                if len(picks) >= 3:
                    break
        if not picks:
            lines.append("**Aucun assez solide** pour un bras copy PAPER fiable pour l’instant.")
            lines.append("")
            lines.append(
                "Les meilleurs WR de la shortlist sont surtout des wallets jeunes / snipers. "
                "Les historiques longs ont souvent un WR trop bas ou un PnL période négatif. "
                "Mieux vaut élargir hors de ces 100 (span > 45 j + WR > 55 % + peu de snipes)."
            )
        else:
            if not any(r.get("verdict")=="COPIABLE" for r in picks):
                lines.append("**Aucun vraiment COPIABLE** au sens strict — **moins risqués** pour un test PAPER microscopique :")
            else:
                lines.append("Candidats retenus (PAPER, très petites tailles) :")
            lines.append("")
            for i, r in enumerate(picks[:3], 1):
                lines.append(
                    f"{i}. `{r['wallet']}` — **{r.get('verdict')}** — {r.get('pourquoi')} "
                    f"(WR {r.get('wr')}%, span {r.get('trade_span_days')} j, âge {r.get('age_days')} j, "
                    f"snipes {r.get('pct_snipe')}, DD {r.get('max_dd_pct')}, depth {r.get('depth')})."
                )
            lines.append("")
            lines.append(
                "Rappel Killian : la copie rate les snipes <30s et ajoute du slippage. "
                "Pas de LIVE sans preuve PAPER nette."
            )
        lines.append("")
        lines.append("## Tableau complet (trié verdict → span)")
        lines.append("")
        lines.append("| # | Wallet | Verdict | Depth | Âge j | Lifetime vs span | Cluster | Max DD % | WR stab. | Trades/j | Méd $ | % snipes | WR % | Span j | Pourquoi |")
        lines.append("|---|--------|---------|-------|-------|------------------|---------|----------|----------|----------|-------|----------|------|--------|----------|")
        for i, r in enumerate(results, 1):
            ws = r["wallet"][:8] + "…" + r["wallet"][-4:]
            dd = r.get("max_dd_pct")
            dd_s = ">100" if (dd is not None and dd > 100) else (dd if dd is not None else "—")
            lines.append(
                f"| {i} | `{ws}` | **{r.get('verdict')}** | {r.get('depth')} | "
                f"{r.get('age_days') if r.get('age_days') is not None else '?'} | "
                f"{(r.get('lifetime_vs_span') or '')[:40]} | {(r.get('cluster') or '')[:28]} | "
                f"{dd_s} | {(r.get('wr_stability') or '')[:22]} | "
                f"{r.get('trades_per_day') if r.get('trades_per_day') is not None else '—'} | "
                f"{r.get('median_usd') if r.get('median_usd') is not None else '—'} | "
                f"{r.get('pct_snipe') if r.get('pct_snipe') is not None else '—'} | "
                f"{r.get('wr')} | {r.get('trade_span_days')} | {(r.get('pourquoi') or '')[:90]} |"
            )
        lines.append("")
        lines.append("## Lecture rapide")
        lines.append("")
        lines.append("- Priorité copy = **durée de vie réelle + stabilité**, pas le ROI annualisé gonflé sur 2 jours.")
        lines.append("- Beaucoup de WR>60 % viennent de wallets **< 2 semaines** → jetables / rotation.")
        lines.append("- Near-misses longs (ex. hnu69 ~30 j WR~86 %, GAAc ~66 j) sont plus utiles à regarder que la shortlist ROI>100.")
        lines.append("")
        lines.append("## Fichiers")
        lines.append("- `copy_fitness_all.md` / `.csv` / `.json`")
        lines.append("- `copy_fitness_9.*` conserve l’analyse détaillée initiale des 9 shortlist")
        path.write_text("\n".join(lines) + "\n")

    (OUT / "copy_fitness_all.json").write_text(json.dumps(results, indent=2, default=str))
    write_csv(OUT / "copy_fitness_all.csv")
    write_md(OUT / "copy_fitness_all.md")
    if WS.exists():
        (WS / "copy_fitness_all.json").write_text(json.dumps(results, indent=2, default=str))
        write_csv(WS / "copy_fitness_all.csv")
        write_md(WS / "copy_fitness_all.md")

    print("\n=== SUMMARY ===")
    print("COPIABLE", sum(1 for r in results if r.get("verdict")=="COPIABLE"))
    print("RISQUÉ", sum(1 for r in results if r.get("verdict")=="RISQUÉ"))
    print("À ÉVITER", sum(1 for r in results if r.get("verdict")=="À ÉVITER"))
    print("TOP:")
    for r in results[:12]:
        print(f"  {r.get('verdict'):10} {r['wallet'][:14]} age={r.get('age_days')} span={r.get('trade_span_days')} wr={r.get('wr')} snipe={r.get('pct_snipe')} | {r.get('pourquoi')[:80]}")
    print("DONE", OUT / "copy_fitness_all.md")


if __name__ == "__main__":
    main()
