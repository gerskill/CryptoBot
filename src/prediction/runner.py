"""Un cycle de simulation : régler, suivre les prix, décider, journaliser.

INVARIANTS repris de la boucle memecoin :
  - le cycle ne meurt jamais : toute erreur est attrapée par l'appelant ;
  - le règlement des paris ouverts passe avant la recherche de nouveaux paris ;
  - une donnée absente ne s'invente pas : pas de prix, pas de bougies ou pas
    de carnet = pas de pari, et la raison est comptée.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from src.prediction import estimators
from src.prediction.markets import Market, parse_outcome_meta
from src.prediction.paper import (BetStore, _write_atomic, append_jsonl,
                                  compute_stats, net_pnl, read_json, read_jsonl,
                                  simulate_entry)

INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}
# Écart minimal entre le prix au règlement et le seuil pour trancher soi-même.
# En dessous, le pari est classé « indéterminé » : Hyperliquid règle sur un
# prix interpolé qu'une bougie d'une minute ne reproduit pas exactement.
SETTLE_MARGIN = 0.0005
CERTAIN_HIGH, CERTAIN_LOW = 0.97, 0.03


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def load_config(path: str) -> dict[str, Any]:
    config = read_json(path, None)
    if not isinstance(config, dict) or not isinstance(config.get("arms"), list):
        raise ValueError(f"configuration des bras illisible : {path}")
    return config


class PredictionRunner:
    def __init__(self, api: Any, config: dict[str, Any], data_dir: str,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.api = api
        self.config = config
        self.data_dir = data_dir
        self.now = now
        self._candles: dict[tuple[str, str], list[dict[str, float]]] = {}

    # ---------- accès fichiers ----------

    def store(self, arm: str) -> BetStore:
        return BetStore(self.data_dir, arm)

    def _price_path(self, coin: str) -> str:
        return os.path.join(self.data_dir, "prices", f"{coin.lstrip('#')}.jsonl")

    def _track(self, coin: str, moment: datetime, mid: Optional[float],
               bid: Optional[float] = None, ask: Optional[float] = None) -> None:
        if mid is None:
            return
        append_jsonl(self._price_path(coin), {"t": _iso(moment), "mid": mid, "bid": bid, "ask": ask})

    # ---------- données de marché ----------

    def _closes(self, coin: str, interval: str, count: int, end: datetime) -> list[float]:
        key = (coin, interval)
        if key not in self._candles:
            minutes = INTERVAL_MINUTES.get(interval, 1)
            start = end - timedelta(minutes=minutes * count)
            self._candles[key] = self.api.candles(
                coin, interval, int(start.timestamp() * 1000), int(end.timestamp() * 1000))
        return [row["c"] for row in self._candles[key]]

    # ---------- décision ----------

    def _estimate(self, arm: dict[str, Any], market: Market, mids: dict[str, float],
                  moment: datetime) -> tuple[Optional[float], str]:
        """Probabilité que le côté 0 (« Oui ») gagne, et la raison si absente."""
        if not market.underlying or market.target is None or market.expiry is None:
            return None, "marche_sans_seuil"
        spot = mids.get(market.underlying)
        if spot is None:
            return None, "prix_sous_jacent_absent"
        interval = arm.get("intervalle", "1m")
        closes = self._closes(market.underlying, interval, int(arm.get("bougies", 4320)), moment)
        minutes_left = (market.expiry - moment).total_seconds() / 60
        steps = max(1, round(minutes_left / INTERVAL_MINUTES.get(interval, 1)))
        kind = arm.get("estimateur")
        if kind == "empirique":
            prob = estimators.prob_above_empirical(spot, market.target, closes, steps)
        elif kind == "lognormal":
            prob = estimators.prob_above_lognormal(spot, market.target, closes, steps)
        elif kind == "touche":
            prob = estimators.prob_touch(spot, market.target, closes, steps)
        else:
            return None, "estimateur_inconnu"
        return (prob, "") if prob is not None else (None, "historique_insuffisant")

    def _eligible(self, arm: dict[str, Any], market: Market, moment: datetime) -> Optional[str]:
        if market.is_fallback:
            return "marche_de_repli"
        if market.domain not in arm.get("domaines", []):
            return "hors_domaine"
        if len(market.sides) != 2:
            return "pas_deux_cotes"
        if market.expiry is not None:
            minutes_left = (market.expiry - moment).total_seconds() / 60
            if minutes_left < arm.get("min_minutes", 3):
                return "trop_pres_de_l_echeance"
            if minutes_left > arm.get("max_jours", 7) * 1440:
                return "echeance_trop_lointaine"
        return None

    def _vote(self, arm: dict[str, Any], market: Market, mids: dict[str, float],
              moment: datetime) -> tuple[Optional[dict[str, Any]], str]:
        """Côté choisi par un bras parieur, sur l'espérance nette de frais."""
        prob_yes, reason = self._estimate(arm, market, mids, moment)
        if prob_yes is None:
            return None, reason
        fee = self.config["frais_pct"]
        settle_fee = self.config["frais_reglement_pct"]
        best = None
        for side, prob in ((market.sides[0], prob_yes), (market.sides[1], 1 - prob_yes)):
            mid = mids.get(side.coin)
            if mid is None or not 0 < mid < 1:
                continue
            # Pré-filtre au prix milieu ; l'espérance est recalculée au prix
            # vendeur réel avant d'ouvrir.
            edge = prob * (1 - settle_fee) - mid * (1 + fee)
            if edge >= arm.get("edge_min", 0.05) and (best is None or edge > best["edge"]):
                best = {"side": side, "prob": prob, "edge": edge,
                        "source": f"{arm.get('estimateur')} {arm.get('intervalle', '')}".strip()}
        return (best, "") if best else (None, "esperance_insuffisante")

    def _favorite(self, arm: dict[str, Any], market: Market,
                  mids: dict[str, float]) -> tuple[Optional[dict[str, Any]], str]:
        priced = [(mids.get(s.coin), s) for s in market.sides]
        priced = [(p, s) for p, s in priced if p is not None]
        if len(priced) != 2:
            return None, "prix_absent"
        price, side = max(priced, key=lambda pair: pair[0])
        if not arm.get("prix_min", 0.5) <= price <= arm.get("prix_max", 0.95):
            return None, "favori_hors_fourchette"
        return {"side": side, "prob": None, "edge": None, "source": "favori du marché"}, ""

    def _open(self, arm: dict[str, Any], market: Market, pick: dict[str, Any],
              moment: datetime) -> str:
        store = self.store(arm["name"])
        capital = self.config["capital_fictif_usd"]
        stake = capital * self.config["mise_pct"]
        exposure = sum(b["stake_usd"] for b in store.open_bets() if b.get("domain") == market.domain)
        if exposure + stake > capital * self.config["exposition_max_pct_par_domaine"] + 1e-9:
            return "exposition_max"
        side = pick["side"]
        book = self.api.l2_book(side.coin)
        if not book or not book["asks"]:
            return "carnet_absent"
        fill = simulate_entry(book["asks"], stake)
        if fill is None:
            return "carnet_trop_mince"
        fee, settle_fee = self.config["frais_pct"], self.config["frais_reglement_pct"]
        if pick["prob"] is not None:
            edge = pick["prob"] * (1 - settle_fee) - fill["price"] * (1 + fee)
            if edge < arm.get("edge_min", 0.05):
                return "esperance_insuffisante_au_carnet"
        bid = book["bids"][0][0] if book["bids"] else None
        ask = book["asks"][0][0]
        bet = {
            "id": f"{arm['name']}-{side.coin.lstrip('#')}-{int(moment.timestamp())}",
            "arm": arm["name"],
            "outcome": market.outcome,
            "coin": side.coin,
            "side_index": side.index,
            "side_name": side.name,
            "title": market.title,
            "domain": market.domain,
            "klass": market.klass,
            "underlying": market.underlying,
            "target": market.target,
            "expiry": _iso(market.expiry) if market.expiry else None,
            "entry_time": _iso(moment),
            "entry_price": round(fill["price"], 6),
            "shares": round(fill["shares"], 6),
            "stake_usd": stake,
            "fee_entry_usd": round(stake * fee, 6),
            "spread_at_entry": round(ask - bid, 6) if bid is not None else None,
            "prob_estimee": round(pick["prob"], 6) if pick["prob"] is not None else None,
            "source_estimation": pick["source"],
            "status": "ouvert",
            "exemple_fictif": False,
        }
        store.add(bet)
        # Point de prix à l'instant exact de l'entrée : le graphique part du
        # même horodatage que la ligne du tableau.
        self._track(side.coin, moment, (ask + bid) / 2 if bid is not None else ask, bid, ask)
        return "ouvert"

    # ---------- règlement ----------

    def _last_mid(self, coin: str) -> Optional[float]:
        rows = read_jsonl(self._price_path(coin))
        return rows[-1]["mid"] if rows else None

    def _yes_value(self, bet: dict[str, Any], mids: dict[str, float],
                   live_outcomes: set[int], moment: datetime) -> Optional[float]:
        """1.0 si le côté 0 a gagné, 0.0 s'il a perdu, None si pas encore établi,
        -1.0 si le marché est clos sans que l'issue soit établie."""
        expiry = datetime.fromisoformat(bet["expiry"]) if bet.get("expiry") else None
        gone = bet["outcome"] not in live_outcomes
        expired = expiry is not None and moment >= expiry
        if not gone and not expired:
            return None

        yes_coin = f"#{10 * bet['outcome']}"
        last = mids.get(yes_coin)
        if last is None and bet["side_index"] == 0:
            last = self._last_mid(bet["coin"])
        elif last is None:
            other = self._last_mid(bet["coin"])
            last = None if other is None else 1 - other
        from_price = None
        if last is not None and (last >= CERTAIN_HIGH or last <= CERTAIN_LOW):
            from_price = 1.0 if last >= CERTAIN_HIGH else 0.0

        from_candle = None
        if (expired and bet.get("domain") in ("crypto_seuil", "actions_seuil")
                and bet.get("underlying") and bet.get("target")):
            end_ms = int(expiry.timestamp() * 1000)
            rows = self.api.candles(bet["underlying"], "1m", end_ms - 120_000, end_ms + 60_000)
            before = [r for r in rows if r["t"] < end_ms]
            if before:
                close = before[-1]["c"]
                if abs(close / bet["target"] - 1) >= SETTLE_MARGIN:
                    from_candle = 1.0 if close > bet["target"] else 0.0

        if from_candle is not None and from_price is not None and from_candle != from_price:
            return -1.0  # deux sources en désaccord : on ne tranche pas
        value = from_candle if from_candle is not None else from_price
        if value is not None:
            return value
        # Clos ou échu depuis plus d'une heure sans issue lisible : indéterminé.
        if gone or (expiry is not None and moment - expiry > timedelta(hours=1)):
            return -1.0
        return None

    def _settle_all(self, arms: list[dict[str, Any]], mids: dict[str, float],
                    live_outcomes: set[int], moment: datetime) -> int:
        settled = 0
        for arm in arms:
            store = self.store(arm["name"])
            for bet in store.open_bets():
                self._track(bet["coin"], moment, mids.get(bet["coin"]))
                yes_value = self._yes_value(bet, mids, live_outcomes, moment)
                if yes_value is None:
                    continue
                closed = dict(bet, settle_time=_iso(moment))
                if yes_value < 0:
                    closed.update(status="indetermine", settle_value=None, pnl_net=None, won=None)
                else:
                    value = yes_value if bet["side_index"] == 0 else 1 - yes_value
                    pnl = net_pnl(bet["stake_usd"], bet["shares"], value,
                                  self.config["frais_pct"], self.config["frais_reglement_pct"])
                    closed.update(status="regle", settle_value=value,
                                  pnl_net=round(pnl, 6), won=value == 1.0)
                    # Dernier point du graphique = valeur de règlement.
                    self._track(bet["coin"], moment, value)
                store.close(bet["id"], closed)
                settled += 1
        return settled

    # ---------- incohérences ----------

    def _coherence(self, markets: list[Market], mids: dict[str, float], moment: datetime) -> int:
        found = []
        fee = self.config["frais_pct"]
        by_question: dict[str, list[Market]] = {}
        for market in markets:
            if market.question and not market.is_fallback:
                by_question.setdefault(market.question, []).append(market)
        for question, group in by_question.items():
            prices = [mids.get(m.sides[0].coin) for m in group]
            if len(group) >= 2 and all(p is not None for p in prices):
                total = sum(prices)
                if total * (1 + fee) < 0.995:
                    found.append({"type": "somme_sous_100", "question": question,
                                  "somme_milieux": round(total, 4), "issues": len(group)})
        ladders: dict[tuple, list[Market]] = {}
        for market in markets:
            if market.domain in ("crypto_seuil", "actions_seuil") and market.target is not None:
                ladders.setdefault((market.underlying, market.expiry), []).append(market)
        for (underlying, expiry), group in ladders.items():
            group.sort(key=lambda m: m.target)
            for low, high in zip(group, group[1:]):
                p_low, p_high = mids.get(low.sides[0].coin), mids.get(high.sides[0].coin)
                if p_low is not None and p_high is not None and p_high > p_low + 0.02:
                    found.append({"type": "echelle_non_monotone", "sous_jacent": underlying,
                                  "echeance": _iso(expiry) if expiry else None,
                                  "seuil_bas": low.target, "prix_bas": p_low,
                                  "seuil_haut": high.target, "prix_haut": p_high})
        for row in found:
            append_jsonl(os.path.join(self.data_dir, "coherence.jsonl"),
                         dict(row, t=_iso(moment), arm="pm_coherence", base="prix milieux"))
        return len(found)

    # ---------- cycle ----------

    def cycle(self) -> dict[str, Any]:
        moment = self.now()
        self._candles = {}
        meta = self.api.outcome_meta()
        if meta is None:
            # Sans la liste des marchés on ne sait pas lesquels sont clos : ne
            # rien régler plutôt que de régler à tort.
            return {"ok": False, "raison": "outcomeMeta indisponible"}
        mids = self.api.all_mids()
        markets = parse_outcome_meta(meta)
        arms = [a for a in self.config["arms"] if a.get("enabled", True)]
        bettors = [a for a in arms if a.get("role") in ("temoin", "parieur", "consensus")]

        settled = self._settle_all(bettors, mids, {m.outcome for m in markets}, moment)

        # Marchés déjà pariés, lus une fois par cycle puis tenus à jour en mémoire.
        seen = {a["name"]: self.store(a["name"]).seen_outcomes() for a in bettors}
        opened = 0
        reasons: dict[str, int] = {}
        consensus = [a for a in arms if a.get("role") == "consensus"]
        for market in markets:
            votes: dict[int, list[dict[str, Any]]] = {}
            for arm in arms:
                role = arm.get("role")
                if role not in ("temoin", "parieur"):
                    continue
                reason = self._eligible(arm, market, moment)
                pick = None
                if reason is None:
                    pick, reason = (self._favorite(arm, market, mids) if role == "temoin"
                                    else self._vote(arm, market, mids, moment))
                if pick is not None:
                    if role == "parieur":
                        votes.setdefault(pick["side"].index, []).append(pick)
                    if market.outcome in seen[arm["name"]]:
                        reason = "deja_parie"
                    else:
                        reason = self._open(arm, market, pick, moment)
                        if reason == "ouvert":
                            opened += 1
                            seen[arm["name"]].add(market.outcome)
                reasons[f"{arm['name']}:{reason}"] = reasons.get(f"{arm['name']}:{reason}", 0) + 1
            for arm in consensus:
                if self._eligible(arm, market, moment) is not None:
                    continue
                for picks in votes.values():
                    if len(picks) < arm.get("min_confluence", 2):
                        continue
                    if market.outcome in seen[arm["name"]]:
                        continue
                    pick = dict(picks[0], prob=sum(p["prob"] for p in picks) / len(picks),
                                source=f"consensus de {len(picks)} bras")
                    if self._open(dict(arm, edge_min=0.0), market, pick, moment) == "ouvert":
                        opened += 1
                        seen[arm["name"]].add(market.outcome)

        observers = [a for a in arms if a.get("role") == "observateur"]
        anomalies = self._coherence(markets, mids, moment) if observers else 0

        by_domain: dict[str, int] = {}
        for market in markets:
            if not market.is_fallback:
                by_domain[market.domain] = by_domain.get(market.domain, 0) + 1
        state = {
            "updated_at": moment.timestamp(),
            "updated_at_iso": _iso(moment),
            "mode": "PAPER",
            "marches_par_domaine": by_domain,
            "ouverts_ce_cycle": opened,
            "regles_ce_cycle": settled,
            "incoherences_ce_cycle": anomalies,
            "raisons": reasons,
        }
        _write_atomic(os.path.join(self.data_dir, "state.json"), state)
        return dict(state, ok=True)


def summary(config: dict[str, Any], data_dir: str) -> list[dict[str, Any]]:
    """Une ligne par bras, chiffres attribués à CE bras."""
    rows = []
    for arm in config["arms"]:
        store = BetStore(data_dir, arm["name"])
        rows.append({
            "arm": arm["name"],
            "role": arm.get("role"),
            "enabled": arm.get("enabled", True),
            "domaines": arm.get("domaines", []),
            "description": arm.get("description", ""),
            "ouverts": len(store.open_bets()),
            "stats": compute_stats(store.settled(), config["capital_fictif_usd"],
                                   config["min_paris_verdict"]),
        })
    return rows
