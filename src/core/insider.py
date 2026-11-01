"""DÉTECTEUR « INSIDER FRONT-RUN » façon Bubblemaps (2026-10-18) — OBSERVATION SEULE.

POURQUOI. Bubblemaps a montré que des wallets liés à certains callers achètent
quelques secondes / minutes avant un pic, puis revendent quelques minutes
après. Killian a validé de MESURER ce schéma sur nos entrées avant toute
décision. Aucun bras, aucune porte, aucun paramètre ne lit ces champs : ils
sont recopiés dans `entry_context` (donc `trades_log.jsonl`) et c'est tout.
Une éventuelle activation dans `anti_rug` reste la décision de Killian, après
évaluation aux bilans du 24 oct / 7 nov.

CE QUE FAIT CE MODULE (CPU pur, aucun appel réseau). À partir des données
Helius DÉJÀ récupérées par `OnchainObserver` (transactions de lancement, 15
dernières minutes, signatures des wallets vérifiés « frais ») et d'au plus
`MAX_FUNDER_LOOKUPS` lectures `getTransaction` (1 crédit chacune) pour trouver
QUI a financé chaque wallet frais :

  1. Grappes de wallets liés (union-find) parmi top 10 porteurs + 10 premiers
     acheteurs + créateur. Liens :
       - même source de financement (premier transfert SOL entrant) ;
       - financé directement par le créateur du token ;
       - wallets frais créés à moins de `CREATION_WINDOW_SEC` l'un de l'autre ;
       - achat dans le même slot (bundle) au lancement.
  2. Achat des wallets liés dans les `PRE_SPIKE_SEC` avant un pic d'achats
     (minute avec >= `SPIKE_MIN_BUYS` et >= `SPIKE_RATIO` × la médiane) ;
     achat dans les 15 min précédant le calcul (donc avant l'entrée du bot).
  3. Ventes précoces : un wallet lié revend moins de `EARLY_SELL_SEC` après
     son premier achat observé.
  4. Part de l'offre détenue par les wallets liés (borne BASSE : seuls les
     avoirs du top 20 sont connus).

Valeur inconnue = None, jamais 0. Faux positifs connus : deux wallets
financés par le même hot wallet d'exchange sont « liés » (pas de liste
d'exchanges fiable à ce jour : `KNOWN_HUB_FUNDERS` vide, à compléter).
"""

from __future__ import annotations

import statistics
from typing import Any, Iterable, Optional

INSIDER_VERSION = 1

MAX_FUNDER_LOOKUPS = 6          # crédits max ajoutés par token (getTransaction = 1)
CREATION_WINDOW_SEC = 300       # deux wallets frais créés à < 5 min = liés
PRE_SPIKE_SEC = 300             # « juste avant le pic » = 5 min
EARLY_SELL_SEC = 600            # revente < 10 min après l'achat
SPIKE_MIN_BUYS = 5
SPIKE_RATIO = 3.0
SPIKE_MIN_COVERED_MIN = 3       # il faut au moins 3 minutes observées
FLAG_MIN_CLUSTER = 3
FLAG_MIN_PCT_SUPPLY = 5.0
KNOWN_HUB_FUNDERS: frozenset[str] = frozenset()
SYSTEM_PROGRAM = "11111111111111111111111111111111"

INSIDER_FIELDS = (
    "insider_version", "insider_status", "insider_credits_used",
    "insider_wallets_checked", "insider_funders_found",
    "insider_links_funder", "insider_links_creator", "insider_links_created", "insider_links_slot",
    "insider_cluster_size", "insider_linked_wallets", "insider_clusters_n",
    "insider_pct_supply",
    "insider_spike_detected", "insider_spike_time", "insider_spike_buys",
    "insider_bought_before_spike", "insider_pre_spike_buyers", "insider_spike_lead_sec",
    "insider_buyers_before_entry_15m",
    "insider_early_sellers", "insider_sellers_15m", "insider_selling",
    "insider_flag",
)


def empty_insider(status: Optional[str] = None) -> dict[str, Any]:
    out: dict[str, Any] = {k: None for k in INSIDER_FIELDS}
    out["insider_version"] = INSIDER_VERSION
    out["insider_status"] = status
    return out


# ------------------------------------------------------------ transactions

def _keys(tx: dict) -> list[str]:
    try:
        keys = tx["transaction"]["message"]["accountKeys"]
    except (KeyError, TypeError):
        return []
    return [k.get("pubkey") if isinstance(k, dict) else k for k in keys]


def _sig(tx: dict) -> Optional[str]:
    try:
        sigs = tx["transaction"]["signatures"]
        return sigs[0] if sigs else None
    except (KeyError, TypeError, IndexError):
        return None


def _payer_delta(tx: dict, mint: str) -> tuple[Optional[str], float]:
    keys = _keys(tx)
    if not keys:
        return None, 0.0
    payer = keys[0]
    meta = tx.get("meta") or {}
    pre = post = 0.0
    for rows, sign in ((meta.get("preTokenBalances"), -1), (meta.get("postTokenBalances"), 1)):
        for row in rows or []:
            if row.get("mint") == mint and row.get("owner") == payer:
                amount = float(((row.get("uiTokenAmount") or {}).get("uiAmount")) or 0)
                if sign < 0:
                    pre += amount
                else:
                    post += amount
    return payer, post - pre


def swaps(txs: Iterable[dict], mint: str) -> list[tuple[int, int, str, float]]:
    """(slot, blockTime, wallet, delta) ; delta > 0 achat, < 0 vente. Dédoublonné."""
    out, seen = [], set()
    for tx in txs or []:
        sig = _sig(tx)
        key = sig or (tx.get("slot"), tuple(_keys(tx)[:1]), tx.get("blockTime"))
        if key in seen:
            continue
        seen.add(key)
        payer, delta = _payer_delta(tx, mint)
        if payer and delta != 0:
            out.append((int(tx.get("slot") or 0), int(tx.get("blockTime") or 0), payer, delta))
    return out


def funder_of(tx: Optional[dict], wallet: str) -> Optional[str]:
    """Source du premier SOL reçu : transfert/création système vers `wallet`."""
    if not tx:
        return None
    instructions = list(((tx.get("transaction") or {}).get("message") or {}).get("instructions") or [])
    for inner in (tx.get("meta") or {}).get("innerInstructions") or []:
        instructions += list(inner.get("instructions") or [])
    for ins in instructions:
        parsed = ins.get("parsed") if isinstance(ins, dict) else None
        if not isinstance(parsed, dict) or ins.get("program") not in (None, "system"):
            continue
        info = parsed.get("info") or {}
        kind = parsed.get("type")
        if kind in ("transfer", "transferWithSeed") and info.get("destination") == wallet:
            src = info.get("source")
            if src and src != wallet:
                return src
        if kind in ("createAccount", "createAccountWithSeed") and info.get("newAccount") == wallet:
            src = info.get("source")
            if src and src != wallet:
                return src
    keys = _keys(tx)
    if keys and keys[0] and keys[0] != wallet:
        return keys[0]  # repli : payeur des frais de la 1re transaction
    return None


def first_activity(signatures: Optional[list[dict]]) -> tuple[Optional[str], Optional[int]]:
    """(signature, blockTime) de la plus ancienne signature connue."""
    best = None
    for s in signatures or []:
        t = s.get("blockTime")
        if t and (best is None or t < best[1]):
            best = (s.get("signature"), int(t))
    return best if best else (None, None)


# ------------------------------------------------------------------- pic

def detect_spike(
    buy_times: list[int], window_start: Optional[int], window_end: Optional[int]
) -> Optional[tuple[int, int]]:
    """(début de la minute du pic, nb d'achats) ou None."""
    if not buy_times or window_start is None or window_end is None:
        return None
    n_min = int((window_end - window_start) // 60) + 1
    if n_min < SPIKE_MIN_COVERED_MIN:
        return None
    counts = [0] * n_min
    for t in buy_times:
        idx = int((t - window_start) // 60)
        if 0 <= idx < n_min:
            counts[idx] += 1
    peak = max(range(n_min), key=lambda i: counts[i])
    others = counts[:peak] + counts[peak + 1:]
    median = statistics.median(others) if others else 0
    if counts[peak] >= SPIKE_MIN_BUYS and counts[peak] >= SPIKE_RATIO * max(median, 1):
        return window_start + 60 * peak, counts[peak]
    return None


# --------------------------------------------------------------- grappes

class _UF:
    def __init__(self) -> None:
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.p[ra] = rb
        return True


def insider_metrics(
    *,
    mint: str,
    supply: Optional[float],
    holdings: dict[str, float],
    candidates: list[str],
    creator: Optional[str],
    fresh: dict[str, Optional[bool]],
    signatures: dict[str, Optional[list[dict]]],
    funders: dict[str, Optional[str]],
    early_txs: Optional[list[dict]],
    recent_txs: Optional[list[dict]],
    now: float,
    recent_window_sec: int = 900,
) -> dict[str, Any]:
    out = empty_insider("ok")
    candidates = [w for w in dict.fromkeys(candidates) if w and w != creator]
    out["insider_wallets_checked"] = len(candidates)
    out["insider_funders_found"] = sum(1 for w in candidates if funders.get(w))
    if not candidates:
        out["insider_status"] = "no_wallets"
        return out

    uf = _UF()
    for w in candidates:
        uf.find(w)
    links = {"funder": 0, "creator": 0, "created": 0, "slot": 0}

    # 1a. Même source de financement / financé par le créateur.
    by_funder: dict[str, list[str]] = {}
    for w in candidates:
        f = funders.get(w)
        if not f or f in KNOWN_HUB_FUNDERS or f == SYSTEM_PROGRAM:
            continue
        if creator and f == creator:
            uf.find(creator)
            links["creator"] += int(uf.union(w, creator))
        by_funder.setdefault(f, []).append(w)
    for group in by_funder.values():
        for other in group[1:]:
            links["funder"] += int(uf.union(group[0], other))

    # 1b. Wallets frais créés dans la même fenêtre courte.
    born = sorted(
        (first_activity(signatures.get(w))[1], w) for w in candidates
        if fresh.get(w) and first_activity(signatures.get(w))[1]
    )
    for i in range(len(born)):
        for j in range(i + 1, len(born)):
            if born[j][0] - born[i][0] > CREATION_WINDOW_SEC:
                break
            links["created"] += int(uf.union(born[i][1], born[j][1]))

    # 1c. Achat dans le même slot (bundle) au lancement.
    early_swaps = swaps(early_txs or [], mint)
    cand_set = set(candidates)
    slot_buyers: dict[int, list[str]] = {}
    for slot, _t, w, d in early_swaps:
        if d > 0 and w in cand_set and w not in slot_buyers.setdefault(slot, []):
            slot_buyers[slot].append(w)
    for group in slot_buyers.values():
        for other in group[1:]:
            links["slot"] += int(uf.union(group[0], other))

    comps: dict[str, list[str]] = {}
    members = candidates + ([creator] if creator and creator in uf.p else [])
    for w in members:  # le créateur ne compte que s'il a financé un wallet
        comps.setdefault(uf.find(w), []).append(w)
    clusters = [ws for ws in comps.values() if len(ws) >= 2]
    linked = {w for ws in clusters for w in ws}
    out["insider_links_funder"] = links["funder"]
    out["insider_links_creator"] = links["creator"]
    out["insider_links_created"] = links["created"]
    out["insider_links_slot"] = links["slot"]
    out["insider_clusters_n"] = len(clusters)
    out["insider_cluster_size"] = max((len(ws) for ws in clusters), default=0)
    out["insider_linked_wallets"] = len(linked)
    if supply:
        out["insider_pct_supply"] = round(100 * sum(holdings.get(w, 0.0) for w in linked) / supply, 2)

    # 2. Pic d'achats et achats des wallets liés juste avant.
    recent_swaps = swaps(recent_txs or [], mint) if recent_txs is not None else None
    spike = None
    if recent_swaps is not None:
        times = [t for _s, t, _w, _d in recent_swaps if t]
        truncated = len(recent_txs or []) >= 100
        start = (min(times) if times else None) if truncated else int(now - recent_window_sec)
        spike = detect_spike([t for _s, t, _w, d in recent_swaps if d > 0 and t], start, int(now))
    if spike is None and early_swaps:
        times = [t for _s, t, _w, _d in early_swaps if t]
        if times:
            spike = detect_spike([t for _s, t, _w, d in early_swaps if d > 0 and t],
                                 min(times), max(times))
    all_swaps = early_swaps + (recent_swaps or [])
    out["insider_spike_detected"] = spike is not None if (recent_swaps is not None or early_swaps) else None
    if spike:
        s_t, s_n = spike
        out["insider_spike_time"] = s_t
        out["insider_spike_buys"] = s_n
        pre = [(t, w) for _s, t, w, d in all_swaps
               if d > 0 and w in linked and s_t - PRE_SPIKE_SEC <= t < s_t]
        out["insider_pre_spike_buyers"] = len({w for _t, w in pre})
        out["insider_bought_before_spike"] = bool(pre)
        out["insider_spike_lead_sec"] = (s_t - min(t for t, _w in pre)) if pre else None
    elif out["insider_spike_detected"] is False:
        out["insider_bought_before_spike"] = False
        out["insider_pre_spike_buyers"] = 0

    if recent_swaps is not None:
        out["insider_buyers_before_entry_15m"] = len(
            {w for _s, _t, w, d in recent_swaps if d > 0 and w in linked})
        out["insider_sellers_15m"] = len(
            {w for _s, _t, w, d in recent_swaps if d < 0 and w in linked})

    # 3. Ventes précoces (après le 1er achat observé).
    first_buy: dict[str, int] = {}
    for _s, t, w, d in sorted(all_swaps, key=lambda r: r[1]):
        if d > 0 and w in linked and t:
            first_buy.setdefault(w, t)
    early_sellers = {
        w for _s, t, w, d in all_swaps
        if d < 0 and w in first_buy and 0 <= t - first_buy[w] <= EARLY_SELL_SEC
    }
    out["insider_early_sellers"] = len(early_sellers)
    out["insider_selling"] = bool(early_sellers) or bool(out["insider_sellers_15m"])

    size = out["insider_cluster_size"] or 0
    pct = out["insider_pct_supply"] or 0.0
    out["insider_flag"] = bool(size >= FLAG_MIN_CLUSTER and (
        pct >= FLAG_MIN_PCT_SUPPLY or out["insider_bought_before_spike"] or out["insider_selling"]))
    return out
