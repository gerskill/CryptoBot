#!/usr/bin/env python3
"""Paper-only: reconstruct ~90d closed round-trips for candidate wallets via Chainstack/Helius.
Never prints RPC URLs or API keys.
"""
from __future__ import annotations
import csv, json, os, sys, time, urllib.request
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Optional

QUOTE = {
    "So11111111111111111111111111111111111111112",  # WSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "pumpCmXqMfrsAkQ5r49WcJnRayYRqmXz6ae8H7H9Dfn",
}
WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
DAYS = 90
CUTOFF = int(time.time()) - DAYS * 86400
MAX_SIGS = 800  # per wallet cap
BATCH = 15
SLEEP = 0.12

def load_env(path: Path) -> dict:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env

class Rpc:
    def __init__(self, url: str):
        self.url = url
        self.n = 0
        self.errors = 0

    def call(self, method: str, params: list, timeout: float = 30) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        self.n += 1
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read())
            if "error" in data:
                self.errors += 1
                return None
            return data.get("result")
        except Exception:
            self.errors += 1
            return None

    def batch(self, calls: list[tuple[str, list]]) -> list[Any]:
        payload = [{"jsonrpc": "2.0", "id": i, "method": m, "params": p} for i, (m, p) in enumerate(calls)]
        body = json.dumps(payload).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        self.n += 1
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
            if not isinstance(data, list):
                self.errors += 1
                return [None] * len(calls)
            by_id = {d.get("id"): d.get("result") if "error" not in d else None for d in data}
            return [by_id.get(i) for i in range(len(calls))]
        except Exception:
            self.errors += 1
            return [None] * len(calls)

def helius_txs(api_key: str, wallet: str, before: Optional[str] = None) -> list[dict]:
    """Enhanced SWAP transactions — dollar amounts when available."""
    q = f"api-key={api_key}&limit=100"
    if before:
        q += f"&before={before}"
    url = f"https://api.helius.xyz/v0/addresses/{wallet}/transactions?{q}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read())
    except Exception as e:
        return []

def parse_helius_swaps(txs: list[dict], wallet: str) -> list[dict]:
    """Extract buy/sell from Helius enhanced txs. Prefer events.swap; else largest non-quote mint delta."""
    events = []
    for tx in txs:
        ts = tx.get("timestamp") or 0
        if ts and ts < CUTOFF:
            continue
        if tx.get("transactionError"):
            continue
        sig = tx.get("signature")
        sw = (tx.get("events") or {}).get("swap") or {}
        # Native SOL deltas for wallet
        native = 0.0
        for n in tx.get("nativeTransfers") or []:
            amt = float(n.get("amount") or 0) / 1e9
            if n.get("toUserAccount") == wallet:
                native += amt
            if n.get("fromUserAccount") == wallet:
                native -= amt
        if tx.get("feePayer") == wallet:
            native -= float(tx.get("fee") or 0) / 1e9

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

        quote_usd = 0.0
        sol_delta = native
        for mint in (USDC, USDT):
            if mint in token_deltas:
                quote_usd += token_deltas.pop(mint)
        if WSOL in token_deltas:
            sol_delta += token_deltas.pop(WSOL)
        for mint in list(token_deltas):
            if mint in QUOTE:
                token_deltas.pop(mint, None)

        # If swap event lists inputs/outputs, restrict to those mints (drop intermediates)
        if sw:
            focus = set()
            for side in ("tokenInputs", "tokenOutputs", "nativeInput", "nativeOutput"):
                pass
            for arr_key in ("tokenInputs", "tokenOutputs"):
                for item in sw.get(arr_key) or []:
                    m = item.get("mint")
                    if m and m not in QUOTE:
                        focus.add(m)
            if focus:
                token_deltas = {m: d for m, d in token_deltas.items() if m in focus}

        if not token_deltas:
            continue
        # Keep only the mint with largest |delta| (primary leg); avoids hop dust
        mint, d = max(token_deltas.items(), key=lambda kv: abs(kv[1]))
        if abs(d) < 1e-12:
            continue
        events.append({
            "mint": mint,
            "side": "buy" if d > 0 else "sell",
            "amount": abs(d),
            "quote_usd": quote_usd,
            "sol_delta": sol_delta,
            "ts": ts,
            "sig": sig,
            "source": "helius",
        })
    return events

def signatures(rpc: Rpc, wallet: str) -> list[dict]:
    out = []
    before = None
    while len(out) < MAX_SIGS:
        opts: dict[str, Any] = {"limit": 1000}
        if before:
            opts["before"] = before
        batch = rpc.call("getSignaturesForAddress", [wallet, opts]) or []
        if not batch:
            break
        stop = False
        for s in batch:
            bt = s.get("blockTime") or 0
            if bt and bt < CUTOFF:
                stop = True
                break
            if s.get("err") is None:
                out.append(s)
        before = batch[-1]["signature"]
        if stop or len(batch) < 1000:
            break
        time.sleep(SLEEP)
    return out[:MAX_SIGS]

def trades_from_tx(tx: Optional[dict], wallet: str, sig: str, bt: int) -> list[dict]:
    if not tx or not isinstance(tx.get("meta"), dict) or tx["meta"].get("err") is not None:
        return []
    meta = tx["meta"]
    # SOL native change
    try:
        idx = None
        keys = (tx.get("transaction") or {}).get("message", {}).get("accountKeys") or []
        for i, k in enumerate(keys):
            pubkey = k.get("pubkey") if isinstance(k, dict) else k
            if pubkey == wallet:
                idx = i
                break
        sol_delta = 0.0
        if idx is not None:
            pre = (meta.get("preBalances") or [0])[idx]
            post = (meta.get("postBalances") or [0])[idx]
            sol_delta = (post - pre) / 1e9
    except Exception:
        sol_delta = 0.0

    token_delta: dict[str, float] = defaultdict(float)
    quote_usd = 0.0
    for sign, key in ((-1.0, "preTokenBalances"), (1.0, "postTokenBalances")):
        for b in meta.get(key) or []:
            if b.get("owner") != wallet:
                continue
            mint = b.get("mint")
            amt = float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0.0)
            if mint in (USDC, USDT):
                quote_usd += sign * amt
            elif mint == WSOL:
                sol_delta += sign * amt
            elif mint in QUOTE:
                continue
            else:
                token_delta[mint] += sign * amt

    events = []
    for mint, d in token_delta.items():
        if abs(d) < 1e-12:
            continue
        events.append({
            "mint": mint,
            "side": "buy" if d > 0 else "sell",
            "amount": abs(d),
            "quote_usd": quote_usd,
            "sol_delta": sol_delta,
            "ts": bt or tx.get("blockTime") or 0,
            "sig": sig,
            "source": "rpc",
        })
    return events

def fetch_trades_rpc(rpc: Rpc, wallet: str) -> list[dict]:
    sigs = signatures(rpc, wallet)
    events = []
    for i in range(0, len(sigs), BATCH):
        chunk = sigs[i:i+BATCH]
        calls = [("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]) for s in chunk]
        results = rpc.batch(calls)
        for s, tx in zip(chunk, results):
            events.extend(trades_from_tx(tx, wallet, s["signature"], s.get("blockTime") or 0))
        time.sleep(SLEEP)
    # chronological
    events.sort(key=lambda e: e["ts"])
    return events

def fetch_trades_helius(api_key: str, wallet: str) -> list[dict]:
    all_txs = []
    before = None
    for _ in range(60):  # up to 6000 txs
        batch = helius_txs(api_key, wallet, before)
        if not batch:
            break
        # stop if older than cutoff
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
        time.sleep(0.15)
    events = parse_helius_swaps(all_txs, wallet)
    events.sort(key=lambda e: e["ts"])
    return events

def fifo_pnl(events: list[dict], sol_usd: float) -> dict:
    """FIFO round-trips per mint. Cost in USD = -quote_usd + (-sol_delta)*sol_usd attributed proportionally.
    Multi-mint txs share quote — approximate by assigning full quote/sol of tx to the non-quote mint change
    when only one meme mint moved; if multiple, split by |amount| weight (weak).
    """
    # Group events that share same sig — re-attribute quote across meme mints in that tx
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

    lots: dict[str, deque] = defaultdict(deque)  # mint -> deque of (amount, cost_usd)
    closed = []  # (pnl_usd, cost_usd, mint)
    for e in adjusted:
        mint = e["mint"]
        # USD spent/received for this leg
        # buy: wallet loses stables/SOL → quote_usd usually negative, sol_delta negative
        # cost_usd = -(quote_usd) - sol_delta*sol_usd when buying (money out)
        # When selling: money in = quote_usd + sol_delta*sol_usd
        flow_usd = e["quote_usd"] + e["sol_delta"] * sol_usd
        if e["side"] == "buy":
            cost = max(0.0, -flow_usd)
            if cost <= 0 and e["amount"] > 0:
                # fallback unknown cost — skip cost tracking but keep lot with 0
                cost = 0.0
            lots[mint].append([e["amount"], cost, e["ts"]])
        else:
            # sell
            rem = e["amount"]
            proceeds = max(0.0, flow_usd)
            proceeds_left = proceeds
            amt_total = e["amount"] or 1.0
            while rem > 1e-12 and lots[mint]:
                lot_amt, lot_cost, lot_ts = lots[mint][0]
                take = min(rem, lot_amt)
                frac_lot = take / lot_amt if lot_amt else 0
                cost_take = lot_cost * frac_lot
                # allocate proceeds proportional to amount sold this iteration vs total sell
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
            # leftover sell without buy (airdrop) ignored for WR
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
        req = urllib.request.Request("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd")
        with urllib.request.urlopen(req, timeout=15) as r:
            return float(json.loads(r.read())["solana"]["usd"])
    except Exception:
        return 160.0  # fallback

def main():
    root = Path.home() / "Projects" / "CryptobBot"
    env = load_env(root / ".env")
    rpc_url = env.get("CHAINSTACK_RPC_URL") or ""
    helius_key = env.get("HELIUS_API_KEY") or ""
    if not rpc_url:
        print("NO_RPC", file=sys.stderr)
        sys.exit(1)
    # mask host only
    import re
    m = re.match(r"(https?://[^/]+)", rpc_url)
    print(f"rpc_host={m.group(1) if m else '?'}")
    print(f"helius_key_present={bool(helius_key)}")

    candidates_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/wallets_alpha/top30.json")
    wallets = json.loads(candidates_path.read_text())
    # prefer non-arb first for first pass — take up to 25
    wallets = sorted(wallets, key=lambda r: -float(r["volume_usd_vu"]))[:25]
    sol_usd = get_sol_price()
    print(f"sol_usd={sol_usd:.2f} wallets={len(wallets)} period_days={DAYS}")

    rpc = Rpc(rpc_url)
    results = []
    for i, row in enumerate(wallets):
        w = row["wallet"]
        print(f"\n[{i+1}/{len(wallets)}] {w[:8]}… vol_vu={row['volume_usd_vu']}", flush=True)
        events = []
        method = "rpc"
        if helius_key:
            events = fetch_trades_helius(helius_key, w)
            method = "helius"
            if len(events) < 30:
                print(f"  helius sparse ({len(events)}), fallback rpc", flush=True)
                events = fetch_trades_rpc(rpc, w)
                method = "rpc"
        else:
            events = fetch_trades_rpc(rpc, w)
        stats = fifo_pnl(events, sol_usd)
        span = ((events[-1]["ts"] - events[0]["ts"]) / 86400.0) if len(events) >= 2 else 0.0
        stats.update({
            "span_days": span,
            "wallet": w,
            "volume_usd_vu": float(row["volume_usd_vu"]),
            "buys_vus": int(row["buys_vus"]),
            "sells_vus": int(row["sells_vus"]),
            "sources": row.get("sources", ""),
            "method": method,
            "arb": "arbitrager" in (row.get("sources") or "").lower(),
        })
        # annualize crudely: roi over period * (365/DAYS) — note as estimate
        span_for_ann = stats.get("span_days") or DAYS
        span_for_ann = max(span_for_ann, 7.0)  # avoid insane annualization on <1w
        stats["roi_ann_est"] = stats["roi_pct"] * (365.0 / span_for_ann) if stats["n_closed"] else 0.0
        stats["pass_wr"] = stats["wr"] > 60 and stats["n_closed"] >= 5
        stats["pass_perf"] = stats["roi_pct"] > 100 or stats["roi_ann_est"] > 100
        stats["pass_both"] = stats["pass_wr"] and stats["pass_perf"] and stats["n_closed"] >= 5
        results.append(stats)
        print(f"  method={method} events={stats['n_events']} closed={stats['n_closed']} WR={stats['wr']:.1f}% PnL=${stats['pnl_usd']:.0f} ROI={stats['roi_pct']:.1f}% ann≈{stats['roi_ann_est']:.0f}% span={stats.get('span_days',0):.1f}d pass={stats['pass_both']}", flush=True)
        # checkpoint
        Path("scripts/out/results_partial.json").write_text(json.dumps(results, indent=2))

    Path("scripts/out/results.json").write_text(json.dumps(results, indent=2))
    print(f"\nDONE rpc_calls≈{rpc.n} errors={rpc.errors}")
    passed = [r for r in results if r["pass_both"]]
    print(f"PASSED both thresholds: {len(passed)}")
    for r in sorted(results, key=lambda x: (-x["pass_both"], -x["wr"], -x["roi_pct"])):
        print(f"{'OK' if r['pass_both'] else '..'} {r['wallet'][:12]} WR={r['wr']:.1f}% ROI={r['roi_pct']:.1f}% n={r['n_closed']} PnL=${r['pnl_usd']:.0f}")

if __name__ == "__main__":
    main()
