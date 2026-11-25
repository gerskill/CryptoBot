"""Un cycle de simulation : régler, suivre les prix, décider, journaliser.

INVARIANTS repris de la boucle memecoin :
  - le cycle ne meurt jamais : toute erreur est attrapée par l'appelant ;
  - le règlement des paris ouverts passe avant la recherche de nouveaux paris ;
  - une donnée absente ne s'invente pas : pas de prix, pas de bougies ou pas
    de carnet = pas de pari, et la raison est comptée.
"""

import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from src.prediction import estimators
from src.prediction import learning
from src.prediction import overlay
from src.prediction.markets import (DOMAINES_SEUIL, Market, parse_outcome_meta,
                                    settled_outcomes)
from src.prediction.paper import (BetStore, _write_atomic, account, append_jsonl,
                                  buy_shares, compute_stats, engagement_limit, equity_drawdown, mark_bet,
                                  min_shares, net_pnl, read_json, read_jsonl,
                                  remaining_asks, simulate_entry)

MARKS_FILE = "marks.json"

INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}
# Écart minimal entre le prix au règlement et le seuil pour trancher soi-même.
# En dessous, le pari est classé « indéterminé » : Hyperliquid règle sur un
# prix interpolé qu'une bougie d'une minute ne reproduit pas exactement.
SETTLE_MARGIN = 0.0005
CERTAIN_HIGH, CERTAIN_LOW = 0.97, 0.03
# Écart maximal toléré entre le prix payé au carnet et le prix milieu affiché.
# Relevé le 2026-11-16 : des marchés affichés à 50 % ont un carnet à 1 % / 99 %.
# Sans ce garde-fou, le témoin y achetait un « favori » à 0,99.
ECART_MAX_CARNET = 0.05
PRIX_NON_COTE = 0.006


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
        self.base_config = config
        self.config = overlay.effective_config(config, data_dir)
        self.data_dir = data_dir
        self.now = now
        self._candles: dict[tuple[str, str], list[dict[str, float]]] = {}
        # Carnets lus CE cycle, par pièce (absent compris) : sans ça chaque bras
        # relisait le même carnet, 252 lectures et 146 s par cycle (2026-11-21).
        self._books: dict[str, Optional[dict[str, list[tuple[float, float]]]]] = {}
        # Parts achetées CE cycle, par pièce, tous bras confondus : un même
        # carnet ne se vend pas deux fois.
        self._taken: dict[str, float] = {}
        # Valeurs au marché du cycle précédent (limite d'engagement total).
        self._marks_prev: dict[str, Any] = {}

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
        interval = arm.get("intervalle", "1m")
        closes = self._closes(market.underlying, interval, int(arm.get("bougies", 4320)), moment)
        # `allMids` ne couvre que la bourse principale : pour une pièce
        # `xyz:…` (action, indice, matière première) le prix courant est la
        # dernière clôture des bougies, si elle est récente.
        spot = mids.get(market.underlying)
        if spot is None and closes and ":" in market.underlying:
            spot = closes[-1]
        if spot is None:
            return None, "prix_sous_jacent_absent"
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
        horizon = market.event_time
        if horizon is not None:
            minutes_left = (horizon - moment).total_seconds() / 60
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
                best = {"side": side, "prob": prob, "edge": edge, "mid": mid,
                        "source": f"{arm.get('estimateur')} {arm.get('intervalle', '')}".strip()}
        return (best, "") if best else (None, "esperance_insuffisante")

    def _favorite(self, arm: dict[str, Any], market: Market,
                  mids: dict[str, float]) -> tuple[Optional[dict[str, Any]], str]:
        priced = [(mids.get(s.coin), s) for s in market.sides]
        priced = [(p, s) for p, s in priced if p is not None]
        if len(priced) != 2:
            return None, "prix_absent"
        # `cote` : « favori » achète le côté le plus cher, « outsider » le
        # moins cher. La fourchette de prix borne ce que la règle accepte.
        outsider = arm.get("cote") == "outsider"
        price, side = (min if outsider else max)(priced, key=lambda pair: pair[0])
        if not arm.get("prix_min", 0.5) <= price <= arm.get("prix_max", 0.95):
            return None, "prix_hors_fourchette"
        source = "outsider du marché" if outsider else "favori du marché"
        return {"side": side, "prob": None, "edge": None, "mid": price, "source": source}, ""

    def _open(self, arm: dict[str, Any], market: Market, pick: dict[str, Any],
              moment: datetime) -> str:
        """Achat simulé comme sur un vrai compte (ADR 018).

        Le compte du bras a un solde : une mise qu'il ne peut pas payer est
        refusée. Hyperliquid impose une taille minimale d'ordre : en dessous,
        l'ordre réel serait rejeté, donc le papier aussi. Les parts déjà
        achetées ce cycle par un autre bras ne sont plus dans le carnet.
        """
        cfg = self.config
        store = self.store(arm["name"])
        open_bets = store.open_bets()
        compte = account(cfg["capital_fictif_usd"], store.settled(), open_bets)
        valeur = compte["valeur"]
        target = valeur * cfg["mise_pct"]
        # Limite d'engagement total (25 % du compte, base la plus prudente).
        # Vérifiée avant le carnet (pas de lecture inutile) puis avec le coût réel.
        long_hours = float(cfg.get("engagement_long_heures", 24.0))
        limite = engagement_limit(
            account(cfg["capital_fictif_usd"], store.settled(), open_bets,
                    getattr(self, "_marks_prev", None) or {}),
            cfg.get("engagement_total_max_pct"), open_bets,
            cfg.get("engagement_long_max_pct"), long_hours)
        # Long = règlement plus de 24 h après maintenant ; échéance inconnue = long.
        is_long = (market.expiry is None
                   or (market.expiry - moment).total_seconds() > long_hours * 3600)
        refus = self._engagement_refus(limite, target * (1 + cfg["frais_pct"]), is_long)
        if refus:
            return refus
        exposure = sum(b["stake_usd"] for b in open_bets if b.get("domain") == market.domain)
        exposure_cap = valeur * cfg["exposition_max_pct_par_domaine"] + 1e-9
        if exposure + target > exposure_cap:
            return "exposition_max"
        side = pick["side"]
        book = self._book(side.coin)
        if not book or not book["asks"]:
            return "carnet_absent"
        asks = remaining_asks(book["asks"], self._taken.get(side.coin, 0.0))
        if not asks:
            return "carnet_deja_pris"
        min_usd = float(cfg.get("ordre_min_usd", 0) or 0)
        whole = bool(cfg.get("parts_entieres", False))
        sized_by_minimum = False
        if min_usd > 0 or whole:
            best = next((p for p, _ in asks if 0 < p < 1), None)
            if best is None:
                return "carnet_absent"
            wanted = target / best
            wanted = float(math.floor(wanted)) if whole else wanted
            minimum = min_shares(best, min_usd, cfg.get("regle_taille_min", "min_cote"))
            if minimum > wanted:
                wanted, sized_by_minimum = minimum, True
            fill = buy_shares(asks, wanted)
        else:
            fill = simulate_entry(asks, target)
            if fill is not None:
                fill["cost"] = target
        if fill is None:
            return "carnet_trop_mince"
        stake = fill["cost"]
        if stake > valeur * float(cfg.get("mise_max_pct", 1.0)) + 1e-9:
            # Le minimum d'Hyperliquid exige plus que ce que le bras accepte de
            # risquer sur un pari : avec 46 $, un favori à 0,90 coûterait 90 $.
            return "sous_taille_minimale" if sized_by_minimum else "mise_trop_grande"
        fee, settle_fee = cfg["frais_pct"], cfg["frais_reglement_pct"]
        if stake * (1 + fee) > compte["solde"] + 1e-9:
            return "solde_insuffisant"
        refus = self._engagement_refus(limite, stake * (1 + fee), is_long)
        if refus:
            return refus
        if exposure + stake > exposure_cap:
            return "exposition_max"
        # Dans les DEUX sens (juge, 2026-11-17) : un carnet très en dessous du
        # prix affiché faisait acheter au témoin un « favori » à 0,24 alors que
        # sa fourchette est 0,50–0,95. L'une des deux sources est en retard, et
        # le papier ne doit pas parier sur celle qui l'arrange.
        if pick.get("mid") is not None and abs(fill["price"] - pick["mid"]) > ECART_MAX_CARNET:
            return "carnet_trop_large"
        # La fourchette d'un bras témoin/règle porte sur le prix PAYÉ, pas
        # seulement sur le prix affiché qui a servi à choisir le côté.
        if pick["prob"] is None and not (arm.get("prix_min", 0.0) - 1e-9 <= fill["price"]
                                         <= arm.get("prix_max", 1.0) + 1e-9):
            return "prix_hors_fourchette_au_carnet"
        edge = None
        if pick["prob"] is not None:
            edge = pick["prob"] * (1 - settle_fee) - fill["price"] * (1 + fee)
            if edge < arm.get("edge_min", 0.05):
                return "esperance_insuffisante_au_carnet"
        self._taken[side.coin] = self._taken.get(side.coin, 0.0) + fill["shares"]
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
            "famille": market.famille,
            "venue": market.venue,
            "question": market.question,
            "mid_at_entry": pick.get("mid"),
            "klass": market.klass,
            "underlying": market.underlying,
            "target": market.target,
            "expiry": _iso(market.expiry) if market.expiry else None,
            "entry_time": _iso(moment),
            "entry_price": round(fill["price"], 6),
            "shares": round(fill["shares"], 6),
            "stake_usd": round(stake, 6),
            "fee_entry_usd": round(stake * fee, 6),
            "edge_au_carnet": round(edge, 6) if edge is not None else None,
            "taille_au_minimum": sized_by_minimum,
            "solde_avant": compte["solde"],
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

    @staticmethod
    def _engagement_refus(limite: Optional[dict[str, Any]], cout: float, is_long: bool) -> str:
        """Motif de refus si ce coût ferait dépasser une limite d'engagement."""
        if not limite:
            return ""
        if "limite" in limite and limite["engage"] + cout > limite["limite"] + 1e-9:
            return "engagement_total_max"
        if is_long and "limite_long" in limite and limite["engage_long"] + cout > limite["limite_long"] + 1e-9:
            return "engagement_long_max"
        return ""

    # ---------- règlement ----------

    def _book(self, coin: str) -> Optional[dict[str, list[tuple[float, float]]]]:
        """Carnet d'une pièce, lu une seule fois par cycle.

        Les parts déjà prises par un autre bras sont retirées à part
        (`_taken`) : le carnet mémorisé reste celui de l'API, jamais modifié.
        """
        if coin not in self._books:
            self._books[coin] = self.api.l2_book(coin)
        return self._books[coin]

    def _last_mid(self, coin: str) -> Optional[float]:
        rows = read_jsonl(self._price_path(coin))
        return rows[-1]["mid"] if rows else None

    def _yes_value(self, bet: dict[str, Any], mids: dict[str, float],
                   live_outcomes: set[int], moment: datetime,
                   official: Optional[dict[int, bool]] = None) -> Optional[float]:
        """1.0 si le côté 0 a gagné, 0.0 s'il a perdu, None si pas encore établi,
        -1.0 si le marché est clos sans que l'issue soit établie."""
        expiry = datetime.fromisoformat(bet["expiry"]) if bet.get("expiry") else None
        gone = bet["outcome"] not in live_outcomes
        expired = expiry is not None and moment >= expiry
        declared = (official or {}).get(bet["outcome"])
        if not gone and not expired and declared is None:
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
        if (expired and bet.get("domain") in DOMAINES_SEUIL
                and bet.get("underlying") and bet.get("target")):
            end_ms = int(expiry.timestamp() * 1000)
            rows = self.api.candles(bet["underlying"], "1m", end_ms - 120_000, end_ms + 60_000)
            before = [r for r in rows if r["t"] < end_ms]
            if before:
                close = before[-1]["c"]
                if abs(close / bet["target"] - 1) >= SETTLE_MARGIN:
                    from_candle = 1.0 if close > bet["target"] else 0.0

        from_official = None if declared is None else (1.0 if declared else 0.0)
        sources = {v for v in (from_candle, from_price, from_official) if v is not None}
        if len(sources) > 1:
            return -1.0  # deux sources en désaccord : on ne tranche pas
        if from_official is not None and from_price is None and from_candle is None:
            # La liste officielle seule ne suffit pas tant que son sens n'a
            # pas été vu sur un vrai règlement : attendre le prix.
            return None
        value = sources.pop() if sources else None
        if value is not None:
            return value
        # Clos ou échu depuis plus d'une heure sans issue lisible : indéterminé.
        if gone or (expiry is not None and moment - expiry > timedelta(hours=1)):
            return -1.0
        return None

    def _settle_all(self, arms: list[dict[str, Any]], mids: dict[str, float],
                    live_outcomes: set[int], moment: datetime,
                    official: Optional[dict[int, bool]] = None,
                    data_dir: Optional[str] = None) -> int:
        settled = 0
        for arm in arms:
            # `data_dir` : dossier d'une saison archivée dont les paris encore
            # ouverts doivent finir proprement (voir `_settle_previous_seasons`).
            store = BetStore(data_dir, arm["name"]) if data_dir else self.store(arm["name"])
            for bet in store.open_bets():
                self._track(bet["coin"], moment, mids.get(bet["coin"]))
                yes_value = self._yes_value(bet, mids, live_outcomes, moment, official)
                if yes_value is None:
                    continue
                closed = dict(bet, settle_time=_iso(moment))
                prudence = self.config.get("delai_prudence_heures")
                if yes_value < 0 and prudence is not None:
                    # Un vrai compte n'a pas de pari « indéterminé » : il est
                    # gagné ou perdu. Tant que l'issue reste illisible, le pari
                    # reste ouvert (sa mise reste engagée) ; passé le délai, il
                    # est compté PERDU — l'hypothèse qui protège le capital.
                    since = bet.get("a_regler_depuis")
                    if since is None:
                        store.update(bet["id"], {"a_regler_depuis": _iso(moment)})
                        continue
                    if moment - datetime.fromisoformat(since) < timedelta(hours=float(prudence)):
                        continue
                    pnl = net_pnl(bet["stake_usd"], bet["shares"], 0.0,
                                  self.config["frais_pct"], self.config["frais_reglement_pct"])
                    closed.update(status="regle", settle_value=0.0, pnl_net=round(pnl, 6), won=False,
                                  regle_par_prudence=True,
                                  motif_reglement=(f"issue illisible {float(prudence):g} h après la "
                                                   "première tentative : comptée perdue par prudence"))
                    self._track(bet["coin"], moment, 0.0)
                elif yes_value < 0:
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

    def _settle_previous_seasons(self, mids: dict[str, float], live_outcomes: set[int],
                                 moment: datetime, official: Optional[dict[int, bool]]) -> int:
        """Règle les paris encore ouverts des saisons archivées.

        `saisons_a_terminer` (config) liste des dossiers relatifs à
        `data_dir`, ex. « archive/saison2_46usd_20261122 ». Les paris y sont
        réglés avec les mêmes règles qu'avant, dans LEUR saison : ils ne
        touchent jamais les comptes de la saison en cours. Aucun nouveau pari
        n'y est ouvert.
        """
        total = 0
        for rel in self.config.get("saisons_a_terminer") or []:
            directory = os.path.normpath(os.path.join(self.data_dir, str(rel)))
            if not directory.startswith(os.path.normpath(self.data_dir) + os.sep):
                continue  # jamais en dehors du dossier de données
            arms_dir = os.path.join(directory, "arms")
            if not os.path.isdir(arms_dir):
                continue
            names = sorted(n for n in os.listdir(arms_dir)
                           if os.path.isdir(os.path.join(arms_dir, n))
                           and BetStore(directory, n).open_bets())
            if names:
                total += self._settle_all([{"name": n} for n in names], mids, live_outcomes,
                                          moment, official, data_dir=directory)
        return total

    def _marks(self, arms: list[dict[str, Any]], mids: dict[str, float],
               moment: datetime) -> int:
        """Valeur de revente de chaque pari ouvert de la saison, au carnet.

        Écrit `marks.json` (lu par le tableau de bord). Un carnet par pièce,
        lu une fois par cycle (cache `_books`). Affichage seulement.
        """
        sell_fee = float(self.config.get("frais_vente_pct", self.config.get("frais_pct", 0.0)))
        # Plafond de lectures de carnet NOUVELLES pour la valorisation : au-delà,
        # le prix milieu sert (méthode « milieu », signalée). Un cycle ne doit
        # pas s'allonger sans limite quand il y a beaucoup de paris ouverts.
        budget = int(self.config.get("valorisation_carnets_max", 60))
        marks: dict[str, dict[str, Any]] = {}
        for arm in arms:
            for bet in self.store(arm["name"]).open_bets():
                coin = bet.get("coin")
                if not coin:
                    continue
                if coin in self._books:
                    book = self._books[coin]
                elif budget > 0:
                    budget -= 1
                    book = self._book(coin)
                else:
                    book = None
                mark = mark_bet(bet, book, mids.get(coin), sell_fee)
                mark["arm"] = arm["name"]
                marks[str(bet.get("id"))] = mark
        _write_atomic(os.path.join(self.data_dir, MARKS_FILE),
                      {"updated_at_iso": _iso(moment), "frais_vente_pct": sell_fee, "bets": marks})
        return len(marks)

    # ---------- incohérences ----------

    def _coherence(self, markets: list[Market], mids: dict[str, float], moment: datetime) -> int:
        """Incohérences de prix relevées sur les prix milieux. Rien n'est parié."""
        found = []
        fee = self.config["frais_pct"]
        # Un marché échu reste listé quelques heures avec un prix figé : l'écarter.
        live = [m for m in markets if not m.is_fallback
                and (m.expiry is None or m.expiry > moment)]

        def yes(market: Market) -> Optional[float]:
            # Un marché sans cotation s'affiche à 50 % pile (relevé du
            # 2026-11-16 : carnet à 1 % / 99 %). Ce n'est pas un prix : sans
            # ce filtre, presque toutes les « incohérences » étaient ce 50 %.
            price = mids.get(market.sides[0].coin)
            if price is None or not 0 < price < 1 or abs(price - 0.5) < PRIX_NON_COTE:
                return None
            return price

        # 1. Issues exclusives d'une même question : la somme devrait faire 100 %.
        by_question: dict[str, list[Market]] = {}
        for market in live:
            if market.question:
                by_question.setdefault(market.question, []).append(market)
        for question, group in by_question.items():
            prices = [yes(m) for m in group]
            # Seul un match a des issues qui couvrent tout (A, nul, B). Un
            # tournoi ne liste que quelques équipes, le reste est dans le
            # repli ; l'ordre des tranches de prix n'est pas documenté.
            if group[0].domain != "sport_match":
                continue
            if len(group) < 2 or any(p is None for p in prices):
                continue
            total = sum(prices)
            kind = ("somme_sous_100" if total * (1 + fee) < 0.995
                    else "somme_sur_100" if total > 1.03 else None)
            if kind:
                found.append({"type": kind, "question": question, "domain": group[0].domain,
                              "libelle": group[0].group, "somme_milieux": round(total, 4),
                              "issues": len(group)})

        # 2. Échelle de seuils : « > 2000 » ne peut pas valoir plus que « > 1500 ».
        ladders: dict[tuple, list[Market]] = {}
        for market in live:
            if market.target is not None and (market.domain in DOMAINES_SEUIL
                                              or market.domain == "evenement"):
                key = (market.klass, market.underlying, market.expiry, market.venue)
                ladders.setdefault(key, []).append(market)
        for (_, underlying, expiry, venue), group in ladders.items():
            group.sort(key=lambda m: m.target)
            for low, high in zip(group, group[1:]):
                p_low, p_high = yes(low), yes(high)
                if p_low is not None and p_high is not None and p_high > p_low + 0.02:
                    found.append({"type": "echelle_non_monotone", "sous_jacent": underlying,
                                  "domain": low.domain, "venue": venue,
                                  "echeance": _iso(expiry) if expiry else None,
                                  "seuil_bas": low.target, "prix_bas": p_low,
                                  "seuil_haut": high.target, "prix_haut": p_high})

        # 3. Même marché coté sur deux déployeurs (venues) à des prix différents.
        twins: dict[tuple, list[Market]] = {}
        for market in live:
            if market.target is not None and market.venue:
                twins.setdefault((market.klass, market.underlying, market.expiry, market.target),
                                 []).append(market)
        for (_, underlying, expiry, target), group in twins.items():
            priced = [(yes(m), m) for m in group if yes(m) is not None]
            if len({m.venue for _, m in priced}) < 2:
                continue
            (p_low, low), (p_high, high) = min(priced, key=lambda x: x[0]), max(priced, key=lambda x: x[0])
            if p_high - p_low > 0.03 + 2 * fee:
                found.append({"type": "ecart_entre_venues", "sous_jacent": underlying,
                              "domain": low.domain, "seuil": target,
                              "echeance": _iso(expiry) if expiry else None,
                              "venue_basse": low.venue, "prix_bas": p_low,
                              "venue_haute": high.venue, "prix_haut": p_high})
        for row in found:
            append_jsonl(os.path.join(self.data_dir, "coherence.jsonl"),
                         dict(row, t=_iso(moment), arm="pm_coherence", base="prix milieux"))
        return len(found)

    # ---------- tout enregistrer ----------

    def _catalogue(self, markets: list[Market], mids: dict[str, float],
                   moment: datetime) -> list[dict[str, Any]]:
        rows = []
        for market in markets:
            if market.is_fallback:
                continue
            rows.append({
                "outcome": market.outcome, "coin": market.sides[0].coin,
                "title": market.title, "group": market.group, "label": market.label,
                "domain": market.domain, "famille": market.famille, "venue": market.venue,
                "question": market.question, "underlying": market.symbol, "target": market.target,
                "expiry": _iso(market.expiry) if market.expiry else None,
                "start": _iso(market.start) if market.start else None,
                "sides": [{"name": s.name, "coin": s.coin, "mid": mids.get(s.coin)}
                          for s in market.sides],
            })
        _write_atomic(os.path.join(self.data_dir, "catalogue.json"),
                      {"updated_at_iso": _iso(moment), "markets": rows})
        return rows

    def _snapshot(self, rows: list[dict[str, Any]], moment: datetime) -> bool:
        """Relevé périodique du prix de TOUS les marchés, pariés ou non.

        C'est la matière première des futurs bras : on ne peut pas mesurer si
        un prix de 80 % gagne 80 % du temps sans avoir gardé les prix.
        """
        every = int(self.config.get("instantane_minutes", 15))
        marker = os.path.join(self.data_dir, "snapshots", "dernier.json")
        last = read_json(marker, {}).get("t", 0)
        if every <= 0 or moment.timestamp() - last < every * 60:
            return False
        path = os.path.join(self.data_dir, "snapshots", f"{moment.strftime('%Y-%m-%d')}.jsonl")
        append_jsonl(path, {"t": _iso(moment), "prix": {
            str(r["outcome"]): r["sides"][0]["mid"] for r in rows if r["sides"][0]["mid"] is not None}})
        _write_atomic(marker, {"t": moment.timestamp()})
        return True

    def _perps(self, moment: datetime) -> int:
        """Relevé des marchés à terme (perpétuels) de Liquid. Affichage seul :
        aucun bras ne trade les perpétuels."""
        reader = getattr(self.api, "perp_markets", None)
        if reader is None:
            return 0
        rows = []
        for dex in self.config.get("perps_dex", ["", "xyz"]):
            rows.extend(reader(dex))
        if not rows:
            return 0  # ne pas écraser le dernier relevé par un fichier vide
        rows.sort(key=lambda r: r.get("volume_24h_usd") or 0, reverse=True)
        _write_atomic(os.path.join(self.data_dir, "perps.json"),
                      {"updated_at_iso": _iso(moment), "markets": rows[:120]})
        return len(rows)

    # ---------- cycle ----------

    def cycle(self) -> dict[str, Any]:
        moment = self.now()
        self._candles = {}
        self._books = {}
        self._taken = {}
        # Valeurs au marché du cycle précédent : servent à la limite d'engagement
        # total (base la plus prudente). Absentes = capital et réalisé seuls.
        marks_file = read_json(os.path.join(self.data_dir, MARKS_FILE), {}) or {}
        self._marks_prev = marks_file.get("bets") if isinstance(marks_file.get("bets"), dict) else {}
        meta = self.api.outcome_meta()
        if meta is None:
            # Sans la liste des marchés on ne sait pas lesquels sont clos : ne
            # rien régler plutôt que de régler à tort.
            return {"ok": False, "raison": "outcomeMeta indisponible"}
        mids = self.api.all_mids()
        markets = parse_outcome_meta(meta)
        # Demandes venues du site, puis configuration effective de CE cycle.
        # Une demande qui ferait planter le code ne doit pas bloquer les
        # règlements : le cycle continue avec la configuration existante.
        try:
            demandes = overlay.apply_requests(self.base_config, self.data_dir, moment)
        except Exception as exc:  # noqa: BLE001 — le cycle ne meurt jamais
            demandes = {"erreur": type(exc).__name__}
        self.config = overlay.effective_config(self.base_config, self.data_dir)
        roles = ("temoin", "regle", "parieur", "consensus")
        arms = [a for a in self.config["arms"] if a.get("enabled", True)]
        bettors = [a for a in arms if a.get("role") in roles]

        # Le règlement porte sur TOUS les bras, même désactivés : désactiver un
        # bras arrête ses nouveaux paris, pas le dénouement de ceux déjà pris.
        settled = self._settle_all([a for a in self.config["arms"] if a.get("role") in roles],
                                   mids, {m.outcome for m in markets}, moment,
                                   settled_outcomes(meta))
        # Paris des saisons archivées : ils finissent dans LEUR saison.
        try:
            anciens = self._settle_previous_seasons(mids, {m.outcome for m in markets}, moment,
                                                    settled_outcomes(meta))
        except Exception as exc:  # noqa: BLE001 — le cycle ne meurt jamais
            anciens = {"erreur": type(exc).__name__}

        # Marchés déjà pariés, lus une fois par cycle puis tenus à jour en mémoire.
        seen = {a["name"]: self.store(a["name"]).seen_outcomes() for a in bettors}
        opened = 0
        reasons: dict[str, int] = {}
        consensus = [a for a in arms if a.get("role") == "consensus"]
        for market in markets:
            votes: dict[int, list[dict[str, Any]]] = {}
            for arm in arms:
                role = arm.get("role")
                if role not in ("temoin", "regle", "parieur"):
                    continue
                reason = self._eligible(arm, market, moment)
                pick = None
                if reason is None:
                    pick, reason = (self._favorite(arm, market, mids) if role != "parieur"
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

        # Agent d'apprentissage : mesure chaque bras et PROPOSE des forks
        # (boîte de demandes, appliqués au cycle suivant). Il ne modifie
        # jamais un bras existant. Une erreur ici n'arrête pas le cycle.
        try:
            appris = learning.run(self.config, self.data_dir, moment)
        except Exception as exc:  # noqa: BLE001 — le cycle ne meurt jamais
            appris = {"erreur": type(exc).__name__}

        # Valeur au marché des paris ouverts (affichage seulement).
        try:
            marques = self._marks([a for a in self.config["arms"] if a.get("role") in roles],
                                  mids, moment)
        except Exception as exc:  # noqa: BLE001 — le cycle ne meurt jamais
            marques = {"erreur": type(exc).__name__}

        by_domain: dict[str, int] = {}
        for market in markets:
            if not market.is_fallback:
                by_domain[market.domain] = by_domain.get(market.domain, 0) + 1
        catalogue = self._catalogue(markets, mids, moment)
        snapshot = self._snapshot(catalogue, moment)
        perps = self._perps(moment)
        state = {
            "updated_at": moment.timestamp(),
            "updated_at_iso": _iso(moment),
            "mode": "PAPER",
            "marches_par_domaine": by_domain,
            "ouverts_ce_cycle": opened,
            "regles_ce_cycle": settled,
            "regles_saisons_precedentes": anciens,
            "paris_valorises": marques,
            "incoherences_ce_cycle": anomalies,
            "instantane_ecrit": snapshot,
            "perps_releves": perps,
            "demandes_traitees": demandes,
            "apprentissage": appris,
            "raisons": reasons,
        }
        _write_atomic(os.path.join(self.data_dir, "state.json"), state)
        return dict(state, ok=True)


def summary(config: dict[str, Any], data_dir: str) -> list[dict[str, Any]]:
    """Une ligne par bras, chiffres attribués à CE bras."""
    rows = []
    marks_file = read_json(os.path.join(data_dir, MARKS_FILE), {}) or {}
    marks = marks_file.get("bets") if isinstance(marks_file.get("bets"), dict) else {}
    for arm in overlay.effective_config(config, data_dir)["arms"]:
        store = BetStore(data_dir, arm["name"])
        compte = account(config["capital_fictif_usd"], store.settled(), store.open_bets(), marks)
        compte["valorise_le"] = marks_file.get("updated_at_iso")
        rows.append({
            "arm": arm["name"],
            "role": arm.get("role"),
            "enabled": arm.get("enabled", True),
            "domaines": arm.get("domaines", []),
            "famille": arm.get("famille", "transverse"),
            "parent": arm.get("parent"),
            "variable": arm.get("variable"),
            "cree_depuis_le_site": bool(arm.get("cree_depuis_le_site")),
            "variables_forkables": list(overlay.VARIABLES.get(arm.get("role"), ())),
            "reglages": {k: arm[k] for k in ("prix_min", "prix_max", "cote", "edge_min", "estimateur",
                                             "max_jours", "min_minutes", "intervalle") if k in arm},
            "description": arm.get("description", ""),
            "ouverts": len(store.open_bets()),
            "compte": compte,
            # Baisse depuis le plus haut, latent compris, en % du plus haut (≤ 100 %).
            "drawdown": equity_drawdown(config["capital_fictif_usd"], store.settled(),
                                        compte.get("equite_marche")),
            # Limite d'engagement total : engagé / limite, base la plus prudente.
            # Et limite sur les paris longs (> 24 h, échéance inconnue comprise).
            "engagement": engagement_limit(compte, config.get("engagement_total_max_pct"),
                                           store.open_bets(), config.get("engagement_long_max_pct"),
                                           float(config.get("engagement_long_heures", 24.0))),
            "origine": arm.get("origine"),
            "apprentissage": learning.read_diagnostic(data_dir, arm["name"]),
            "stats": compute_stats(store.settled(), config["capital_fictif_usd"],
                                   config["min_paris_verdict"]),
        })
    return rows
