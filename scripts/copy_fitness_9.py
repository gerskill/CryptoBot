#!/usr/bin/env python3
"""PAPER-only copy-trading fitness for 9 shortlisted wallets.
Loads HELIUS_API_KEY from CryptobBot .env — never prints secrets.
"""
from __future__ import annotations
import csv, json, os, sys, time, urllib.request, urllib.error, statistics
from collections import defaultdict, deque, Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
QUOTE = {WSOL, USDC, USDT, "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn"}
SNIPE_S = 30  # hold < 30s hard for copy bot
SLEEP = 0.2

WALLETS = [
    ("DJze9rksBmd1VsLXCZeE7uU92rGVuGd2e9q8WmDeC5Rv", "priority", 40),
    ("663x6uqkXNLwocPXxAxfnhLzGM4cfjSAiok21Vjtx2pU", "low", 8),
    ("4U9s6ktkZVD6W9K5X1RRVG89Mm9ZGznrPhhPhXezNedm", "low", 12),
    ("GHNCDK6dxhothbdYKMx3UFC6rP1GJshEF9rwBZECZS1J", "priority", 40),
    ("DULTfcZFLLnBsaUP5k3jC1YMqQZhu762DjKtVaHf2cNV", "low", 12),
    ("Dnq77xMNwqPTj8NRhXvD827HNJNgQvZpqob3oMFus7tE", "low", 12),
    ("AhdtDQrMdDiaNvyLHhJNmhjeNcZQZB8ye7KMbK6QcyFg", "low", 12),
    ("9NXNywh7n8d8ihgUY2FcJn9hEPwXkYCdLyBdbFiDm4rs", "mid", 20),
    ("8ZVN9Puj6UMPESqFxkYs9PJk9Cf9m1FwZDMo1iAeA62R", "mid", 20),
]

ROOT = Path.home() / "Projects" / "CryptobBot"
OUT_DIR = ROOT / "scripts" / "out"
# Also write to workspace if present
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


def helius_txs(api_key: str, wallet: str, before: Optional[str] = None) -> tuple[list[dict], Optional[str]]:
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


def fetch_all_enhanced(api_key: str, wallet: str, max_pages: int) -> tuple[list[dict], Optional[str]]:
    all_txs: list[dict] = []
    before = None
    last_err = None
    for i in range(max_pages):
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


def oldest_sig_age(api_key: str, wallet: str, max_pages: int = 15) -> tuple[Optional[int], Optional[int], int]:
    """Return (first_ts, last_ts, n_sigs_seen) via getSignaturesForAddress pagination."""
    before = None
    first_ts = None
    last_ts = None
    n = 0
    for _ in range(max_pages):
        opts = {"limit": 1000}
        if before:
            opts["before"] = before
        res = rpc_call(api_key, "getSignaturesForAddress", [wallet, opts])
        if not res:
            break
        if last_ts is None and res:
            # first page = newest
            bt = res[0].get("blockTime")
            if bt:
                last_ts = int(bt)
        n += len(res)
        before = res[-1]["signature"]
        bt = res[-1].get("blockTime")
        if bt:
            first_ts = int(bt)
        if len(res) < 1000:
            break
        time.sleep(0.15)
    return first_ts, last_ts, n


def parse_events(txs: list[dict], wallet: str) -> list[dict]:
    events = []
    for tx in txs:
        ts = tx.get("timestamp") or 0
        if tx.get("transactionError"):
            continue
        token_deltas: dict[str, float] = defaultdict(float)
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
        fee = float(tx.get("fee") or 0) / 1e9
        native -= fee
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
            side = "buy" if d > 0 else "sell"
            events.append({
                "mint": mint, "side": side, "amount": abs(d),
                "quote_usd": quote_usd, "sol_delta": sol_delta,
                "ts": ts, "sig": tx.get("signature"),
            })
    events.sort(key=lambda e: e["ts"])
    return events


def fifo_closed(events: list[dict], sol_usd: float) -> list[dict]:
    by_sig: dict[str, list] = defaultdict(list)
    for e in events:
        by_sig[e["sig"]].append(e)
    adjusted = []
    for sig, group in by_sig.items():
        if len(group) == 1:
            adjusted.append(group[0])
            continue
        total_abs = sum(g["amount"] for g in group) or 1.0
        q = group[0]["quote_usd"]
        s = group[0]["sol_delta"]
        for g in group:
            w = g["amount"] / total_abs
            ng = dict(g)
            ng["quote_usd"] = q * w
            ng["sol_delta"] = s * w
            adjusted.append(ng)
    adjusted.sort(key=lambda e: e["ts"])
    lots: dict[str, deque] = defaultdict(deque)
    closed = []
    for e in adjusted:
        mint = e["mint"]
        flow_usd = e["quote_usd"] + e["sol_delta"] * sol_usd
        if e["side"] == "buy":
            cost = max(0.0, -flow_usd)
            lots[mint].append([e["amount"], cost, e["ts"]])
        else:
            rem = e["amount"]
            proceeds = max(0.0, flow_usd)
            amt_total = e["amount"] or 1.0
            while rem > 1e-12 and lots[mint]:
                lot_amt, lot_cost, lot_ts = lots[mint][0]
                take = min(rem, lot_amt)
                frac_lot = take / lot_amt if lot_amt else 0
                cost_take = lot_cost * frac_lot
                proc_take = proceeds * (take / amt_total)
                pnl = proc_take - cost_take
                closed.append({
                    "pnl": pnl, "cost": cost_take, "mint": mint,
                    "hold_s": max(0, e["ts"] - lot_ts),
                    "exit_ts": e["ts"], "entry_ts": lot_ts,
                    "size_usd": cost_take,
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


def max_drawdown_pct(closed: list[dict]) -> float:
    if not closed:
        return 0.0
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for c in closed:
        equity += c["pnl"]
        if equity > peak:
            peak = equity
        if peak > 1e-6:
            dd = (peak - equity) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return max_dd


def wr_by_week(closed: list[dict]) -> tuple[str, float]:
    """Return label of WR stability + stdev of weekly WR."""
    if not closed:
        return "n/a", 0.0
    buckets: dict[int, list[int]] = defaultdict(list)
    for c in closed:
        week = int(c["exit_ts"] // (7 * 86400))
        buckets[week].append(1 if c["pnl"] > 0 else 0)
    wrs = []
    for week in sorted(buckets):
        xs = buckets[week]
        if len(xs) >= 5:
            wrs.append(sum(xs) / len(xs) * 100)
    if len(wrs) < 2:
        if len(wrs) == 1:
            return f"1 sem (~{wrs[0]:.0f}%)", 0.0
        # fall back to daily if span short
        days: dict[int, list[int]] = defaultdict(list)
        for c in closed:
            day = int(c["exit_ts"] // 86400)
            days[day].append(1 if c["pnl"] > 0 else 0)
        drs = [sum(xs)/len(xs)*100 for xs in days.values() if len(xs) >= 3]
        if len(drs) >= 2:
            st = statistics.pstdev(drs)
            return f"jours σ={st:.0f}", st
        return "trop court", 0.0
    st = statistics.pstdev(wrs)
    mn, mx = min(wrs), max(wrs)
    if st < 8:
        label = f"stable ({mn:.0f}–{mx:.0f}%)"
    elif st < 18:
        label = f"moyen ({mn:.0f}–{mx:.0f}%)"
    else:
        label = f"instable ({mn:.0f}–{mx:.0f}%)"
    return label, st


def funding_from_txs(txs: list[dict], wallet: str) -> list[tuple[str, float, int]]:
    """Inbound SOL transfers (excl. tiny tips). Returns [(from, sol, ts), ...] sorted by amount desc."""
    inbound: dict[str, list] = defaultdict(list)
    for tx in txs:
        ts = tx.get("timestamp") or 0
        for n in tx.get("nativeTransfers") or []:
            if n.get("toUserAccount") != wallet:
                continue
            frm = n.get("fromUserAccount") or ""
            if not frm or frm == wallet:
                continue
            amt = float(n.get("amount") or 0) / 1e9
            if amt < 0.05:  # skip dust/tips
                continue
            inbound[frm].append((amt, ts))
    agg = []
    for frm, items in inbound.items():
        total = sum(a for a, _ in items)
        first_ts = min(t for _, t in items)
        agg.append((frm, total, first_ts))
    agg.sort(key=lambda x: -x[1])
    return agg


def source_programs(txs: list[dict]) -> Counter:
    c = Counter()
    for tx in txs:
        src = (tx.get("source") or "").upper() or "UNKNOWN"
        # also type
        typ = (tx.get("type") or "").upper()
        key = src
        if "PUMP" in src or "PUMP" in typ:
            key = "PUMP_FUN"
        elif "RAYDIUM" in src:
            key = "RAYDIUM"
        elif "JUPITER" in src:
            key = "JUPITER"
        elif "ORCA" in src:
            key = "ORCA"
        elif "METEORA" in src:
            key = "METEORA"
        c[key] += 1
    return c


def wash_signals(closed: list[dict], txs: list[dict], wallet: str, volume_hint: float) -> str:
    if not closed:
        return "n/a"
    total_pnl = sum(c["pnl"] for c in closed)
    total_cost = sum(c["cost"] for c in closed) or 1.0
    # tiny PnL vs huge notional
    ratio = abs(total_pnl) / total_cost
    # self-ish: transfers where from==to patterns rare in enhanced; look for same mint buy+sell same second
    same_sec = 0
    by_mint_ts: dict[tuple, int] = defaultdict(int)
    for c in closed:
        key = (c["mint"], c["entry_ts"], c["exit_ts"])
        if c["hold_s"] <= 1:
            same_sec += 1
    pct_1s = same_sec / len(closed) * 100
    flags = []
    if ratio < 0.02 and total_cost > 20000:
        flags.append("PnL minuscule vs volume")
    if pct_1s > 25:
        flags.append(f"{pct_1s:.0f}% holds ≤1s")
    # mirrored: many closes with |pnl| tiny
    tiny = sum(1 for c in closed if abs(c["pnl"]) < 0.05 and c["cost"] > 5)
    if tiny / len(closed) > 0.3:
        flags.append("beaucoup de aller-retour ~0$")
    return "; ".join(flags) if flags else "non évident"


def verdict_row(r: dict) -> tuple[str, str]:
    """Return (VERDICT, pourquoi FR)."""
    age = r.get("age_days")
    span = r.get("trade_span_days") or 0
    dd = r.get("max_dd_pct") or 0
    snipe = r.get("pct_snipe") or 0
    cluster = r.get("cluster") or ""
    disposable = r.get("disposable")
    wash = r.get("wash") or ""
    wr_stab = r.get("wr_stability") or ""
    tpd = r.get("trades_per_day") or 0
    arb = r.get("arb_hint")

    # Hard avoid
    if disposable and (age is not None and age < 14 or span < 3):
        return "À ÉVITER", "Wallet très jeune / durée de vie ≈ période de trading → pattern jetable."
    if span < 1.5 and (r.get("n_closed") or 0) > 20:
        return "À ÉVITER", "Quelques heures/jours de trades seulement — stats trompeuses, pas copiable."
    if "PnL minuscule" in wash or "holds ≤1s" in wash and snipe > 40:
        return "À ÉVITER", "Signes de wash / snipes ultra-rapides impossibles à suivre en copy."
    if arb:
        return "À ÉVITER", "Taggé arbitrageur — latence copy bot trop élevée pour ce style."

    # Risky
    reasons = []
    if age is not None and age < 45 and span > 0 and age < span * 1.5 + 7:
        reasons.append("âge proche du span trading")
    if snipe > 35:
        reasons.append(f"{snipe:.0f}% snipes <30s")
    if dd > 40:
        reasons.append(f"drawdown max ~{dd:.0f}%")
    if "instable" in wr_stab:
        reasons.append("WR instable")
    if "cluster" in cluster.lower() or "oui" in cluster.lower():
        reasons.append("possible rotation de cluster")
    if tpd > 80:
        reasons.append(f"très haute fréquence (~{tpd:.0f}/j)")
    if span < 10:
        reasons.append(f"historique court ({span:.1f} j)")

    if len(reasons) >= 2 or (snipe > 45) or (dd > 55):
        return "RISQUÉ", "; ".join(reasons[:3]) + "."

    if age and age >= 20 and span >= 14 and snipe < 35 and dd < 45 and "stable" in wr_stab or (
        age and age >= 20 and span >= 14 and snipe < 40 and dd < 50
    ):
        why = f"Historique le plus long (~{span:.0f} j), âge ~{age:.0f} j"
        if snipe:
            why += f", snipes {snipe:.0f}%"
        why += " — meilleur candidat PAPER malgré caution."
        return "COPIABLE", why

    if reasons:
        return "RISQUÉ", "; ".join(reasons[:3]) + "."
    return "RISQUÉ", "Données insuffisantes pour valider un bras copy solide."


def analyze_one(api_key: str, wallet: str, tier: str, max_pages: int, sol_usd: float, prior: dict) -> dict:
    print(f"\n=== {wallet[:8]}… tier={tier} pages≤{max_pages}", flush=True)
    txs, err = fetch_all_enhanced(api_key, wallet, max_pages)
    print(f"  enhanced_txs={len(txs)} err={err}", flush=True)

    # Age via signatures (more pages for priority)
    sig_pages = 20 if tier == "priority" else (8 if tier == "mid" else 4)
    first_ts, last_ts, n_sigs = oldest_sig_age(api_key, wallet, max_pages=sig_pages)
    print(f"  sigs_seen≈{n_sigs} first_ts={first_ts} last_ts={last_ts}", flush=True)

    now = int(time.time())
    age_days = (now - first_ts) / 86400.0 if first_ts else None
    last_act_days = (now - last_ts) / 86400.0 if last_ts else None

    # Also from enhanced (may be truncated)
    enh_ts = [t.get("timestamp") for t in txs if t.get("timestamp")]
    enh_first = min(enh_ts) if enh_ts else None
    enh_last = max(enh_ts) if enh_ts else None
    if first_ts is None and enh_first:
        first_ts = enh_first
        age_days = (now - first_ts) / 86400.0
    if last_ts is None and enh_last:
        last_ts = enh_last
        last_act_days = (now - last_ts) / 86400.0

    events = parse_events(txs, wallet)
    closed = fifo_closed(events, sol_usd)
    trade_span = ((closed[-1]["exit_ts"] - closed[0]["entry_ts"]) / 86400.0) if len(closed) >= 2 else (
        ((events[-1]["ts"] - events[0]["ts"]) / 86400.0) if len(events) >= 2 else float(prior.get("span_days") or 0)
    )
    if not closed and prior.get("n_closed"):
        # fallback stats from prior shortlist
        n_closed = prior["n_closed"]
        wr = prior.get("wr") or 0
        max_dd = None
        wr_lab, wr_st = "agrégat shortlist", 0.0
        pct_snipe = None
        med_size = None
        tpd = (n_closed / trade_span) if trade_span > 0.01 else None
        total_pnl = prior.get("pnl_usd")
        total_cost = prior.get("cost_usd")
    else:
        n_closed = len(closed)
        wins = sum(1 for c in closed if c["pnl"] > 0)
        wr = wins / n_closed * 100 if n_closed else 0
        max_dd = max_drawdown_pct(closed)
        wr_lab, wr_st = wr_by_week(closed)
        snipes = sum(1 for c in closed if c["hold_s"] < SNIPE_S)
        pct_snipe = snipes / n_closed * 100 if n_closed else 0
        sizes = [c["size_usd"] for c in closed if c["size_usd"] > 0.01]
        med_size = statistics.median(sizes) if sizes else 0
        tpd = n_closed / trade_span if trade_span > 0.01 else (n_closed if n_closed else 0)
        total_pnl = sum(c["pnl"] for c in closed)
        total_cost = sum(c["cost"] for c in closed)

    funders = funding_from_txs(txs, wallet)
    top_funders = funders[:5]
    programs = source_programs(txs)
    top_prog = ", ".join(f"{k}:{v}" for k, v in programs.most_common(4)) or "?"
    wash = wash_signals(closed, txs, wallet, float(prior.get("volume_usd_vu") or 0)) if closed else "n/a (fallback)"

    # disposable heuristic
    disposable = False
    if age_days is not None and trade_span > 0:
        if age_days < 35 and abs(age_days - trade_span) < 10 and len(top_funders) <= 3:
            disposable = True
        if age_days < 10:
            disposable = True
    if trade_span < 2 and n_closed > 30:
        disposable = True

    arb_hint = "arbitrager" in (prior.get("sources") or "").lower()

    r = {
        "wallet": wallet,
        "tier": tier,
        "age_days": round(age_days, 1) if age_days is not None else None,
        "last_activity_days_ago": round(last_act_days, 2) if last_act_days is not None else None,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "n_sigs_seen": n_sigs,
        "trade_span_days": round(trade_span, 2),
        "lifetime_vs_span": (
            f"âge {age_days:.0f}j / trade {trade_span:.1f}j"
            if age_days is not None else f"trade {trade_span:.1f}j (âge inconnu)"
        ),
        "disposable": disposable,
        "top_funders": [{"from": f[:12]+"…", "sol": round(s, 3), "first_ts": t} for f, s, t in top_funders],
        "top_funder_full": [f for f, s, t in top_funders[:3]],
        "n_closed": n_closed,
        "wr": round(wr, 1),
        "max_dd_pct": round(max_dd, 1) if max_dd is not None else None,
        "wr_stability": wr_lab,
        "wr_stdev": round(wr_st, 1),
        "trades_per_day": round(tpd, 1) if tpd is not None else None,
        "median_usd": round(med_size, 2) if med_size is not None else None,
        "pct_snipe": round(pct_snipe, 1) if pct_snipe is not None else None,
        "programs": top_prog,
        "wash": wash,
        "arb_hint": arb_hint,
        "pnl_usd": round(total_pnl, 1) if total_pnl is not None else None,
        "cost_usd": round(total_cost, 1) if total_cost is not None else None,
        "enhanced_n": len(txs),
        "err": err,
        "prior_sources": prior.get("sources") or "",
    }
    return r


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    env = load_env(ROOT / ".env")
    key = env.get("HELIUS_API_KEY") or ""
    if not key:
        print("NO_HELIUS_KEY", file=sys.stderr)
        sys.exit(1)
    print(f"helius_present=True len={len(key)}")

    prior_by = {}
    for p in (OUT_DIR / "results_all.json", Path("/workspace/wallets_alpha/results_all.json")):
        if p.exists():
            data = json.loads(p.read_text())
            if isinstance(data, list):
                for r in data:
                    if r.get("wallet"):
                        prior_by[r["wallet"]] = r
            break
    print(f"prior_results={len(prior_by)}")
    sol_usd = get_sol_price()
    print(f"sol_usd={sol_usd:.2f}")

    results = []
    for wallet, tier, pages in WALLETS:
        prior = prior_by.get(wallet) or {}
        try:
            r = analyze_one(key, wallet, tier, pages, sol_usd, prior)
        except Exception as e:
            print(f"  FAIL {type(e).__name__}: {e}", flush=True)
            r = {
                "wallet": wallet, "tier": tier, "error": type(e).__name__,
                "age_days": None, "trade_span_days": prior.get("span_days"),
                "n_closed": prior.get("n_closed"), "wr": prior.get("wr"),
                "disposable": True, "wash": "erreur fetch",
                "top_funder_full": [], "arb_hint": "arbitrager" in (prior.get("sources") or "").lower(),
                "max_dd_pct": None, "wr_stability": "n/a", "trades_per_day": None,
                "median_usd": None, "pct_snipe": None, "lifetime_vs_span": "?",
            }
        results.append(r)
        # checkpoint
        (OUT_DIR / "copy_fitness_partial.json").write_text(json.dumps(results, indent=2, default=str))
        time.sleep(0.3)

    # Cross-wallet funding cluster detection
    funder_to_wallets: dict[str, list[str]] = defaultdict(list)
    for r in results:
        for f in r.get("top_funder_full") or []:
            funder_to_wallets[f].append(r["wallet"])
    shared = {f: ws for f, ws in funder_to_wallets.items() if len(ws) >= 2}
    print(f"\nshared_funders={len(shared)}")

    for r in results:
        linked = []
        for f in r.get("top_funder_full") or []:
            others = [w[:8] for w in funder_to_wallets.get(f, []) if w != r["wallet"]]
            if others:
                linked.append(f"{f[:8]}→{','.join(others)}")
        if linked:
            r["cluster"] = "oui: " + "; ".join(linked[:3])
        elif r.get("disposable"):
            r["cluster"] = "possible (jetable solo)"
        else:
            r["cluster"] = "non détecté"
        # if shared funders across shortlist → rotation yes
        r["rotation"] = "oui" if linked else ("suspect" if r.get("disposable") else "non")

        verd, why = verdict_row(r)
        # refine with cluster
        if verd == "COPIABLE" and linked:
            verd = "RISQUÉ"
            why = "Funder partagé avec d'autres wallets shortlist (rotation possible). " + why
        r["verdict"] = verd
        r["pourquoi"] = why

    # Write outputs
    cols = [
        "wallet", "verdict", "age_days", "lifetime_vs_span", "cluster", "rotation",
        "max_dd_pct", "wr_stability", "trades_per_day", "median_usd", "pct_snipe",
        "wash", "programs", "n_closed", "wr", "trade_span_days", "disposable", "pourquoi",
    ]

    def write_csv(path: Path):
        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in results:
                w.writerow({k: r.get(k) for k in cols})

    def write_md(path: Path):
        lines = []
        lines.append("# Copy-fitness PAPER — 9 wallets shortlist")
        lines.append("")
        lines.append(f"_Généré : {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')} (Europe/Zurich)_")
        lines.append("")
        lines.append("Analyse **PAPER only** — aucun live, aucune clé exposée.")
        lines.append("Méthode : Helius enhanced txs + signatures (âge), FIFO round-trips, funding SOL entrant, croisement funders.")
        lines.append("")
        lines.append("## Tableau verdict")
        lines.append("")
        lines.append("| # | Wallet | Verdict | Âge j | Lifetime vs span | Cluster/rotation | Max DD % | WR stabilité | Trades/j | Médiane $ | % snipes <30s | Wash? | Pourquoi |")
        lines.append("|---|--------|---------|-------|------------------|------------------|----------|--------------|----------|-----------|---------------|-------|----------|")
        for i, r in enumerate(results, 1):
            wshort = r["wallet"][:8] + "…" + r["wallet"][-4:]
            lines.append(
                f"| {i} | `{wshort}` | **{r.get('verdict')}** | {r.get('age_days') if r.get('age_days') is not None else '?'} | "
                f"{r.get('lifetime_vs_span','')} | {r.get('cluster','')} | "
                f"{r.get('max_dd_pct') if r.get('max_dd_pct') is not None else '?'} | {r.get('wr_stability','')} | "
                f"{r.get('trades_per_day') if r.get('trades_per_day') is not None else '?'} | "
                f"{r.get('median_usd') if r.get('median_usd') is not None else '?'} | "
                f"{r.get('pct_snipe') if r.get('pct_snipe') is not None else '?'} | "
                f"{(r.get('wash') or '')[:40]} | {r.get('pourquoi','')} |"
            )
        lines.append("")
        lines.append("## Top recommandés pour bras copy PAPER")
        lines.append("")
        copiable = [r for r in results if r.get("verdict") == "COPIABLE"]
        risky_ok = [r for r in results if r.get("verdict") == "RISQUÉ" and (r.get("trade_span_days") or 0) >= 14]
        picks = copiable[:3]
        if len(picks) < 2:
            # add least-bad RISQUÉ with longest span
            extra = sorted(
                [r for r in results if r.get("verdict") == "RISQUÉ" and r not in picks],
                key=lambda x: (-(x.get("trade_span_days") or 0), -(x.get("age_days") or 0)),
            )
            for e in extra:
                if len(picks) >= 3:
                    break
                if (e.get("trade_span_days") or 0) >= 8 and not e.get("arb_hint"):
                    picks.append(e)
        if not picks:
            lines.append("**Aucun assez solide** pour un bras copy PAPER fiable.")
            lines.append("")
            lines.append(
                "Presque tous les wallets de la shortlist ont un historique trop court, "
                "un profil jetable (âge ≈ période de trading), ou un style trop rapide (snipes) "
                "pour qu'un bot de copie suive correctement. Mieux vaut élargir la recherche "
                "(WR un peu plus bas mais span > 30–60 j) avant d'ouvrir un bras."
            )
        else:
            if not copiable:
                lines.append(
                    "**Aucun vraiment COPIABLE** au sens strict — voici les **moins risqués** "
                    "pour un test PAPER prudent (petites tailles, surveillance quotidienne) :"
                )
            else:
                lines.append("Candidats retenus (PAPER, petites tailles) :")
            lines.append("")
            for i, r in enumerate(picks, 1):
                lines.append(
                    f"{i}. `{r['wallet']}` — **{r.get('verdict')}** — {r.get('pourquoi')} "
                    f"(WR {r.get('wr')}%, span {r.get('trade_span_days')} j, âge {r.get('age_days')} j, "
                    f"snipes {r.get('pct_snipe')}%, DD {r.get('max_dd_pct')}%)."
                )
            lines.append("")
            lines.append(
                "Rappel Killian : même un wallet « COPIABLE » en PAPER peut casser dès qu'on le copie "
                "(slippage, latence, taille). Ne pas passer LIVE sans preuve PAPER positive."
            )
        lines.append("")
        lines.append("## Détail funders (anonymisé)")
        lines.append("")
        for r in results:
            tf = r.get("top_funders") or []
            lines.append(f"- `{r['wallet'][:8]}…` funders: {tf[:3] if tf else 'aucun gros SOL entrant vu dans la fenêtre'}")
        lines.append("")
        lines.append("## Fichiers")
        lines.append("- `copy_fitness_9.md` / `copy_fitness_9.csv` / `copy_fitness_9.json`")
        path.write_text("\n".join(lines) + "\n")

    json_path = OUT_DIR / "copy_fitness_9.json"
    json_path.write_text(json.dumps(results, indent=2, default=str))
    write_csv(OUT_DIR / "copy_fitness_9.csv")
    write_md(OUT_DIR / "copy_fitness_9.md")

    if WS.exists():
        write_csv(WS / "copy_fitness_9.csv")
        write_md(WS / "copy_fitness_9.md")
        (WS / "copy_fitness_9.json").write_text(json.dumps(results, indent=2, default=str))

    print("\n=== SUMMARY ===")
    for r in results:
        print(f"{r.get('verdict'):10} {r['wallet'][:12]} age={r.get('age_days')} span={r.get('trade_span_days')} "
              f"dd={r.get('max_dd_pct')} snipe={r.get('pct_snipe')} tpd={r.get('trades_per_day')} | {r.get('pourquoi')}")
    print("DONE", OUT_DIR / "copy_fitness_9.md")


if __name__ == "__main__":
    main()
