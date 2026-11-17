#!/usr/bin/env python3
"""Paper-only shortlist: reconstruct ~90d closed round-trips via Helius for 100 candidates.
Loads HELIUS_API_KEY from CryptobBot .env without printing secrets.
Resumes from scripts/out/results_all_partial.json if present.
"""
from __future__ import annotations
import csv, json, os, sys, time, urllib.request, urllib.error
from collections import defaultdict, deque, Counter
from pathlib import Path
from typing import Any, Optional

QUOTE = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn",
}
WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
DAYS = 90
CUTOFF = int(time.time()) - DAYS * 86400
MAX_PAGES = 25  # up to 2500 enhanced txs
SLEEP = 0.18
MIN_CLOSED = 5

ROOT = Path.home() / "Projects" / "CryptobBot"
OUT = ROOT / "scripts" / "out"
PARTIAL = OUT / "results_all_partial.json"
FINAL = OUT / "results_all.json"


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


def helius_txs(api_key: str, wallet: str, before: Optional[str] = None) -> tuple[list[dict], Optional[str]]:
    q = f"api-key={api_key}&limit=100"
    if before:
        q += f"&before={before}"
    url = f"https://api.helius.xyz/v0/addresses/{wallet}/transactions?{q}"
    if not BUDGET.charge():
        return [], "budget_credits_epuise"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        return [], f"http_{e.code}"
    except Exception as e:
        return [], type(e).__name__


def parse_helius_swaps(txs: list[dict], wallet: str) -> list[dict]:
    events = []
    for tx in txs:
        ts = tx.get("timestamp") or 0
        if ts and ts < CUTOFF:
            continue
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
            if mint == USDC or mint == USDT:
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
                "mint": mint,
                "side": side,
                "amount": abs(d),
                "quote_usd": quote_usd,
                "sol_delta": sol_delta,
                "ts": ts,
                "sig": tx.get("signature"),
                "source": "helius",
            })
    return events


def fetch_trades_helius(api_key: str, wallet: str) -> tuple[list[dict], str, Optional[str]]:
    all_txs: list[dict] = []
    before = None
    last_err = None
    for _ in range(MAX_PAGES):
        batch, err = helius_txs(api_key, wallet, before)
        if err:
            last_err = err
            if err.startswith("http_429") or err == "http_429":
                time.sleep(2.0)
                batch, err = helius_txs(api_key, wallet, before)
                if err:
                    last_err = err
                    break
            else:
                break
        if not batch:
            break
        keep = []
        stop = False
        for tx in batch:
            ts = tx.get("timestamp") or 0
            if ts and ts < CUTOFF:
                stop = True
                break
            keep.append(tx)
        all_txs.extend(keep)
        before = batch[-1].get("signature")
        if stop or len(batch) < 100:
            break
        time.sleep(SLEEP)
    events = parse_helius_swaps(all_txs, wallet)
    events.sort(key=lambda e: e["ts"])
    return events, "helius", last_err


def fifo_pnl(events: list[dict], sol_usd: float) -> dict:
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
                closed.append({"pnl": pnl, "cost": cost_take, "mint": mint, "hold_s": e["ts"] - lot_ts})
                lot_amt -= take
                lot_cost -= cost_take
                rem -= take
                if lot_amt <= 1e-12:
                    lots[mint].popleft()
                else:
                    lots[mint][0] = [lot_amt, lot_cost, lot_ts]
    wins = sum(1 for c in closed if c["pnl"] > 0)
    n = len(closed)
    total_pnl = sum(c["pnl"] for c in closed)
    total_cost = sum(c["cost"] for c in closed)
    wr = (wins / n * 100) if n else 0.0
    roi = (total_pnl / total_cost * 100) if total_cost > 1e-6 else 0.0
    open_lots = sum(len(v) for v in lots.values())
    return {
        "n_closed": n,
        "wins": wins,
        "wr": wr,
        "pnl_usd": total_pnl,
        "cost_usd": total_cost,
        "roi_pct": roi,
        "n_events": len(events),
        "open_lots": open_lots,
    }


def get_sol_price() -> float:
    try:
        req = urllib.request.Request(
            "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return float(json.loads(r.read())["solana"]["usd"])
    except Exception:
        return 160.0


def load_candidates(csv_path: Path) -> list[dict]:
    rows = []
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            rows.append({
                "wallet": row["wallet"].strip(),
                "buys_vus": int(float(row.get("buys_vus") or 0)),
                "sells_vus": int(float(row.get("sells_vus") or 0)),
                "volume_usd_vu": float(row.get("volume_usd_vu") or 0),
                "sources": row.get("sources") or "",
            })
    return rows


def score_row(row: dict, events: list[dict], method: str, err: Optional[str], sol_usd: float) -> dict:
    stats = fifo_pnl(events, sol_usd)
    span = ((events[-1]["ts"] - events[0]["ts"]) / 86400.0) if len(events) >= 2 else 0.0
    stats.update({
        "span_days": span,
        "period_days": DAYS,
        "wallet": row["wallet"],
        "volume_usd_vu": float(row["volume_usd_vu"]),
        "buys_vus": int(row["buys_vus"]),
        "sells_vus": int(row["sells_vus"]),
        "sources": row.get("sources", ""),
        "method": method,
        "error": err,
        "arb": "arbitrager" in (row.get("sources") or "").lower(),
        "source_note": "Helius reconstruct (~90d FIFO round-trips)",
    })
    # Annualize on observed span floored at 14d to avoid insane short-window inflation;
    # also report fixed-90d annualization.
    span_for_ann = max(stats.get("span_days") or DAYS, 14.0)
    stats["roi_ann_est"] = stats["roi_pct"] * (365.0 / span_for_ann) if stats["n_closed"] else 0.0
    stats["roi_ann_90"] = stats["roi_pct"] * (365.0 / DAYS) if stats["n_closed"] else 0.0
    # Filter: WR > 60 AND annualized perf > 100%/year (use span-based ann, require min closed)
    stats["pass_wr"] = stats["wr"] > 60 and stats["n_closed"] >= MIN_CLOSED
    stats["pass_perf"] = stats["roi_ann_est"] > 100
    stats["pass_both"] = bool(stats["pass_wr"] and stats["pass_perf"])
    return stats


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    env = load_env(ROOT / ".env")
    helius_key = env.get("HELIUS_API_KEY") or ""
    if not helius_key:
        print("NO_HELIUS_KEY", file=sys.stderr)
        sys.exit(1)
    print(f"helius_key_present=True key_len={len(helius_key)}")

    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "scripts" / "candidats_a_verifier.csv"
    wallets = load_candidates(csv_path)
    print(f"candidates={len(wallets)}")

    # Resume: merge prior partials (results_all_partial, results.json batch20)
    done: dict[str, dict] = {}
    for p in (PARTIAL, OUT / "results.json", OUT / "results_partial.json"):
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
            if isinstance(data, list):
                for r in data:
                    if r.get("wallet") and "wr" in r:
                        # Prefer newer all-partial over batch
                        if r["wallet"] not in done or p == PARTIAL:
                            done[r["wallet"]] = r
        except Exception:
            pass
    print(f"resume_done={len(done)}")

    sol_usd = get_sol_price()
    print(f"sol_usd={sol_usd:.2f} period_days={DAYS}")

    results: list[dict] = []
    # Keep resume order: process missing first by volume desc
    pending = [r for r in wallets if r["wallet"] not in done]
    pending = sorted(pending, key=lambda r: -float(r["volume_usd_vu"]))
    # Seed results with done (re-score pass flags for consistency)
    for w, r in done.items():
        # refresh pass flags with current criteria without refetch
        r = dict(r)
        r["pass_wr"] = r.get("wr", 0) > 60 and r.get("n_closed", 0) >= MIN_CLOSED
        # Prefer existing roi_ann_est; recompute if missing
        if "roi_ann_est" not in r or r.get("span_days") is not None:
            span_for_ann = max(r.get("span_days") or DAYS, 14.0)
            r["roi_ann_est"] = (r.get("roi_pct") or 0) * (365.0 / span_for_ann) if r.get("n_closed") else 0.0
        r["roi_ann_90"] = (r.get("roi_pct") or 0) * (365.0 / DAYS) if r.get("n_closed") else 0.0
        r["pass_perf"] = (r.get("roi_ann_est") or 0) > 100
        r["pass_both"] = bool(r["pass_wr"] and r["pass_perf"])
        r.setdefault("source_note", "Helius reconstruct (~90d FIFO round-trips)")
        r.setdefault("period_days", DAYS)
        r.setdefault("error", None)
        results.append(r)

    scanned_new = 0
    errors_new = 0
    for i, row in enumerate(pending):
        w = row["wallet"]
        print(f"\n[{i+1}/{len(pending)}] {w[:8]}… vol_vu={row['volume_usd_vu']}", flush=True)
        events, method, err = fetch_trades_helius(helius_key, w)
        if err and not events:
            errors_new += 1
        stats = score_row(row, events, method, err, sol_usd)
        results.append(stats)
        scanned_new += 1
        print(
            f"  method={method} events={stats['n_events']} closed={stats['n_closed']} "
            f"WR={stats['wr']:.1f}% PnL=${stats['pnl_usd']:.0f} ROI={stats['roi_pct']:.1f}% "
            f"ann≈{stats['roi_ann_est']:.0f}% span={stats.get('span_days',0):.1f}d "
            f"err={err} pass={stats['pass_both']}",
            flush=True,
        )
        PARTIAL.write_text(json.dumps(results, indent=2))
        time.sleep(0.05)

    # Deduplicate by wallet (keep last)
    by_w: dict[str, dict] = {}
    for r in results:
        by_w[r["wallet"]] = r
    results = list(by_w.values())
    FINAL.write_text(json.dumps(results, indent=2))

    passed = [r for r in results if r.get("pass_both")]
    ok = sum(1 for r in results if r.get("n_closed", 0) > 0 and not r.get("error"))
    err_n = sum(1 for r in results if r.get("error") and r.get("n_closed", 0) == 0)
    zero = sum(1 for r in results if r.get("n_closed", 0) == 0)
    print(f"\nDONE total={len(results)} ok_closed={ok} zero_closed={zero} hard_errors={err_n} new_scanned={scanned_new} new_errors={errors_new}")
    print(f"PASSED both thresholds: {len(passed)}")
    for r in sorted(results, key=lambda x: (-bool(x.get("pass_both")), -x.get("wr", 0), -x.get("roi_ann_est", 0)))[:20]:
        print(
            f"{'OK' if r.get('pass_both') else '..'} {r['wallet'][:12]} "
            f"WR={r.get('wr',0):.1f}% ROI={r.get('roi_pct',0):.1f}% ann={r.get('roi_ann_est',0):.0f}% "
            f"n={r.get('n_closed',0)} PnL=${r.get('pnl_usd',0):.0f}"
        )


if __name__ == "__main__":
    main()
