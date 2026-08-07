"""Auto-amélioration — étape 6 du workflow.

Recalcule les statistiques après chaque trade clôturé, découpe l'historique en
segments, puis ajuste les paramètres selon la cadence de la spec :
filtres tous les 5 trades, poids et sorties tous les 10, risque tous les 20.

GARDE-FOU CENTRAL : aucun ajustement sans `MIN_SEGMENT_SAMPLE` trades DANS LE
SEGMENT concerné. Sans ça le bot sur-réagit au bruit.
"""

import statistics
from typing import Any, Callable, Optional

from src.core.journal import TradeJournal
from src.core.params import ParamsStore
from src.core.shadow import ShadowTracker

MIN_SEGMENT_SAMPLE = 10
# Plancher ABSOLU par stratégie, indépendant des cadences. Avec 7 bras, une
# cadence « tous les 5 trades » se déclenche sur 5 trades DE CE BRAS — un
# échantillon où le hasard domine tout. Le multi-bras divise l'échantillon
# par le nombre de bras : sans ce plancher, chaque bras apprend plus vite et
# plus faux qu'un bras unique ne le faisait.
MIN_TRADES_PER_ARM = 15
FILTER_CADENCE = 5
WEIGHTS_CADENCE = 10
EXIT_CADENCE = 10
RISK_CADENCE = 20

# Un filtre dont plus de ce % des rejets aurait fait +100% coûte de l'argent.
MISSED_RATE_RELAX_THRESHOLD = 25.0
SHADOW_MIN_SAMPLE = 15

# Ce qu'on desserre, et de combien, par famille de motif de rejet. Partagé par
# `_relax_from_shadow` (preuve : les rejets montaient) et
# `_relax_from_inactivity` (preuve : le bras n'entre plus du tout).
#
# `rugcheck` et `authority` sont ABSENTES et doivent le rester : aucun manque à
# gagner ne justifie de relâcher un garde-fou de sécurité.
RELAXATIONS = {
    "liquidity": ("filters.min_liquidity_usd", -5000),
    "holders": ("filters.min_holders", -25),
    "social": ("filters.min_social_mentions_1h", -5),
    "smart_money": ("filters.min_smart_money_buys_30min", -1),
    "concentration": ("filters.max_top_wallet_concentration", +5),
    # Deux entrées pour l'âge : trop vieux et trop jeune appellent des
    # corrections opposées.
    "age_max": ("filters.max_age_hours", +2),
    "age_min": ("filters.min_age_hours", -0.5),
    "volume": ("filters.min_volume_1h", -2000),
    # PAS UN FILTRE : la porte du score. Un bras dont les candidats passent
    # les filtres et meurent au seuil alpha ne sera JAMAIS débloqué en
    # desserrant un filtre. Mesuré au 2026-08-02 : c'est le cas de `narrative`
    # (869 cycles sans entrée) et de `consensus` (383).
    #
    # Le pas est petit et la borne basse haute (voir PARAM_BOUNDS) : baisser
    # ce seuil, c'est accepter d'entrer sur un signal plus faible. C'est le
    # relâchement le plus coûteux de la table, il doit descendre lentement.
    "alpha": ("scan.alpha_score_entry_threshold", -2.5),
}

# INACTIVITÉ : à partir de combien de cycles évalués sans entrée un bras est
# considéré comme bloqué par ses propres seuils.
#
# CALÉ SUR LA MESURE, pas sur une intuition d'horloge. Entonnoir du 2026-08-02,
# 927 cycles : les bras qui entrent le font tous les 36 cycles (`sniper`, 26
# entrées) à 155 cycles (`consensus`, 6 entrées). 300 est deux fois le plus
# lent observé — au-delà, « ce bras n'entre plus » n'est plus du hasard.
#
# À 90 s par cycle, 300 cycles valent environ 7 h 30 de marché observé. Le
# compteur est en CYCLES et pas en heures pour ne pas confondre un bras qui ne
# trouve rien avec un processus qui ne tournait pas.
INACTIVITY_CYCLES = 300

# Étape 6.4 : un ajustement de filtres doit prouver son gain sur l'historique.
BACKTEST_WINDOW = 20
BACKTEST_MIN_IMPROVEMENT = 0.10
BACKTEST_MIN_KEPT = 5

# Rejeu des SORTIES : il faut assez de trades instrumentés (peak/trough) pour
# trancher. En dessous, `validate_exit_changes` ne rend AUCUN verdict — ne pas
# savoir ne doit pas vouloir dire « annule ».
EXIT_BACKTEST_MIN_COVERAGE = 15
EXIT_BACKTEST_MIN_CHANGED = 5

# Repli quand l'échantillon ne permet pas encore de les mesurer.
DEFAULT_SL_SLIPPAGE = -4.4
DEFAULT_BREAKEVEN_REST = -2.9

# Grille explorée par `_search_exits`.
SL_GRID = (-10.0, -15.0, -20.0, -25.0, -30.0, -40.0)
TP1_GRID = (25.0, 40.0, 50.0, 75.0, 100.0, 150.0)

# BORNES DURES — sans elles, les règles d'ajustement ne font que resserrer et
# le bot converge vers "plus aucun candidat ne passe", définitivement.
PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "filters.min_age_hours": (0.0, 3.0),
    "filters.max_age_hours": (2.0, 24.0),
    "filters.min_liquidity_usd": (5_000, 100_000),
    "filters.min_holders": (25, 2_000),
    "filters.min_volume_1h": (2_000, 100_000),
    "filters.min_rugcheck_score": (50, 95),
    "filters.max_top_wallet_concentration": (10, 40),
    "filters.min_social_mentions_1h": (0, 200),
    "filters.min_smart_money_buys_30min": (0, 10),
    "risk_per_trade": (0.01, 0.05),
    "exit_rules.stop_loss_pct": (-50, -10),
    "exit_rules.take_profit_1": (30, 300),
    "exit_rules.trailing_stop_distance_pct": (20, 80),
    "exit_rules.max_hold_time_minutes": (30, 1440),
    # Mesuré le 2026-08-03 sur 95 déclenchements réels : dépassement médian
    # +6,61 pts, p75 +14,73. Le plafond reste sous le p75 pour ne pas courir
    # après les rugs (p90 +21,74), qui ne sont pas du glissement mais un
    # incident distinct — voir `_recalibrate_slippage_buffer`.
    "exit_rules.stop_loss_slippage_buffer_pct": (0.0, 15.0),
    # Le seuil d'entrée est relâchable (famille `alpha` de RELAXATIONS), donc
    # il LUI FAUT une borne. Sans elle, un bras durablement inactif descendrait
    # vers zéro et finirait par acheter n'importe quoi — le relâchement
    # automatique se transformerait en désarmement.
    #
    # Plancher à 55 : les bras sont configurés entre 65 et 75, ce qui laisse
    # 4 à 8 paliers de -2,5. Assez pour débloquer un bras trop exigeant, pas
    # assez pour supprimer la sélection.
    "scan.alpha_score_entry_threshold": (55, 90),
}

# GARDE-FOU CROISÉ, absent du 2026-08-03 au 2026-08-06. `stop_loss_pct` et le
# tampon sont ajustés par DEUX mécanismes indépendants (`_adjust_exits` resserre
# le premier, `_recalibrate_slippage_buffer` augmente le second), chacun borné
# SEUL contre `PARAM_BOUNDS` — rien ne vérifiait leur SOMME. Mesuré sur `quality`
# le 2026-08-06 : stop_loss_pct au plancher (-10) + tampon au plafond (15.0) =
# seuil effectif (`Position.effective_stop_loss_pct`) à +5 %. Un stop loss
# positif sort quasi toute position dans les secondes suivant l'entrée (139
# trades perdus en une journée, durée médiane 0.2 min). Le seuil effectif doit
# rester un stop RÉEL, jamais nul ni positif.
#
# -1.0 ÉTAIT TROP PRÈS DE ZÉRO, trouvé le 2026-08-07 : `_recalibrate_slippage_
# buffer` ne redescend JAMAIS le tampon (voir sa docstring), donc dès que le
# glissement mesuré reste positif — ce qui arrive en continu sur du memecoin,
# où le prix bouge de plusieurs points entre deux mesures à 5 s — le tampon
# grimpe cycle après cycle jusqu'à ce plancher. 4 bras sur 6 (consensus,
# quality, scalp, runner) l'avaient atteint : seuil effectif à -1.0 %, donc
# stop déclenché sur le bruit normal du marché plutôt que sur une thèse
# invalidée. Win rate mesuré 9-17 % sur ces bras ce jour-là. Remonté à -6.0 :
# garde un stop réel avec de la marge, sans permettre au cliquet de l'annuler.
MIN_EFFECTIVE_STOP_LOSS_PCT = -6.0
STOP_LOSS_PCT_PATH = "exit_rules.stop_loss_pct"
STOP_LOSS_BUFFER_PATH = "exit_rules.stop_loss_slippage_buffer_pct"

# --- ANTI-BOUCLE DE RÉTROACTION NÉGATIVE ---
#
# LA CAUSE RACINE DU PROJET, prouvée par l'historique : 6 resserrages en 13 h,
# aucun relâchement, tous décidés sur 10 à 13 trades.
#
#   min_liquidity_usd  15000 -> 20000 -> 25000
#   min_age_hours        0.5 ->   1.0 ->   1.5
#
# Or la combinaison finale (âge 1,5-6h, liquidité 25 K) ne laisse plus qu'UN
# token disponible sur tout le marché Solana — mesuré. La boucle :
#
#   peu de trades -> apprentissage sur du bruit -> filtres resserrés
#        -> moins de flux -> encore moins de trades -> ...
#
# `_relax_from_shadow` était censé faire contrepoids, mais il exige
# SHADOW_MIN_SAMPLE rejets jugés dans une famille — or un flux réduit produit
# aussi moins de rejets, donc le contrepoids s'éteint en même temps que la
# cause. Les deux mécanismes s'affaiblissent ensemble : c'est ce qui rend la
# boucle auto-entretenue.
#
# La garde : refuser de RESSERRER quand le flux est déjà famélique. Relâcher
# reste toujours permis — l'asymétrie est volontaire, c'est elle qui casse la
# boucle.
# Valeur calée sur la MESURE, pas sur un ordre de grandeur : le flux réel est
# de 0 à 4 candidats retenus par cycle et par bras (entonnoir du 2026-08-01,
# 2300 candidats présentés). Un seuil de 50 — proposé lors d'un audit externe —
# bloquerait tout resserrage à jamais : l'échec symétrique de celui qu'on
# corrige. Le seuil doit être juste au-dessus de « famine », pas au niveau
# d'un flux confortable qui n'existe pas ici.
#
# Volontairement GLOBAL et non par bras. Un bras sélectif comme `quality` a
# structurellement peu de flux — et c'est précisément lui qui ne doit pas
# resserrer davantage. Rendre le seuil configurable par bras permettrait à un
# bras affamé de se resserrer encore, ce que la garde existe pour empêcher.
MIN_FLOW_TO_TIGHTEN = 2.0
# Sens du resserrage par paramètre : monter un plancher restreint, baisser un
# plafond restreint. Sans cette table, impossible de distinguer les deux.
TIGHTENS_WHEN_RAISED = frozenset({
    "filters.min_age_hours",
    "filters.min_liquidity_usd",
    "filters.min_holders",
    "filters.min_volume_1h",
    "filters.min_rugcheck_score",
    "filters.min_social_mentions_1h",
    "filters.min_smart_money_buys_30min",
    # Monter le seuil d'entrée restreint : `_starving` doit l'empêcher quand
    # le flux est déjà famélique, au même titre qu'un filtre.
    "scan.alpha_score_entry_threshold",
})
TIGHTENS_WHEN_LOWERED = frozenset({
    "filters.max_age_hours",
    "filters.max_top_wallet_concentration",
    "filters.max_dev_wallet_pct",
})

AGE_BUCKETS = [(0, 1, "0-1h"), (1, 2, "1-2h"), (2, 4, "2-4h"), (4, 999, "4h+")]
LIQ_BUCKETS = [
    (0, 10_000, "<10K"),
    (10_000, 30_000, "10-30K"),
    (30_000, 50_000, "30-50K"),
    (50_000, float("inf"), ">50K"),
]
SOCIAL_BUCKETS = [(0, 20, "faible"), (20, 50, "moyen"), (50, float("inf"), "fort")]


def _bucket(value: Optional[float], buckets: list) -> Optional[str]:
    if value is None:
        return None
    for low, high, label in buckets:
        if low <= value < high:
            return label
    return None


def _win_rate(rows: list[dict[str, Any]]) -> float:
    """Attend des POSITIONS (`journal.read_positions`), pas des sorties finales.

    Sur des sorties finales, une position sortie en TP1 puis breakeven est
    comptée sur sa seule dernière jambe (-1.8%) et classée perdante alors
    qu'elle a rapporté +49% : le win rate mesuré était 2,8% au lieu de 11,1%.
    """
    if not rows:
        return 0.0
    return 100 * sum(1 for r in rows if r.get("pnl_usd", 0) > 0) / len(rows)


def segment_stats(rows: list[dict[str, Any]], key: str, buckets: list) -> dict[str, dict]:
    """Win rate et taille d'échantillon par segment."""
    grouped: dict[str, list] = {}
    for row in rows:
        label = _bucket(row.get(key), buckets)
        if label:
            grouped.setdefault(label, []).append(row)
    return {
        label: {"win_rate": round(_win_rate(items), 1), "sample": len(items)}
        for label, items in grouped.items()
    }


class LearningEngine:
    def __init__(
        self,
        params: ParamsStore,
        journal: TradeJournal,
        shadow: Optional[ShadowTracker] = None,
        bounds: Optional[dict[str, tuple[float, float]]] = None,
        flow: Optional[Callable[[], Optional[float]]] = None,
        inactivity: Optional[Callable[[], tuple[Optional[int], Optional[str]]]] = None,
        frozen: bool = False,
        pool: Optional[Callable[[], list[dict[str, Any]]]] = None,
    ):
        self.params = params
        self.journal = journal
        self.shadow = shadow
        # Mesure du flux de candidats. Sans elle, resserrer paraît toujours
        # prudent ; avec elle, on voit qu'on s'étrangle.
        self.flow = flow
        # (cycles depuis la dernière entrée, motif de rejet dominant). Un bras
        # qui n'entre plus est bloqué par ses propres seuils, et il n'a aucun
        # trade neuf pour s'en apercevoir.
        self.inactivity = inactivity
        # Un bras GELÉ n'obtient jamais le relâchement sur inactivité —
        # aucun bras du manifeste actuel, témoin compris depuis le 2026-08-03.
        # Réservé à un futur bras référence réellement immuable.
        self.frozen = frozen
        # Trajectoires des AUTRES bras, pour le rejeu des sorties uniquement.
        # `simulate_exits` ne lit que pic et creux : une trajectoire ne dépend
        # pas du bras qui l'a achetée. Filtrées sur la fenêtre du bras par
        # `_in_window`. N'entre JAMAIS dans le rejeu des filtres d'entrée —
        # là, chaque bras a ses seuils et c'est toute sa raison d'être.
        self.pool = pool
        # Bornes par stratégie : un bras expérimental doit pouvoir explorer un
        # stop loss à -35% pendant que le bras témoin reste dans (-50, -10).
        self.bounds = {**PARAM_BOUNDS, **(bounds or {})}

    def _stop_loss_ceiling(self, other_value: float) -> float:
        """Valeur max pour `stop_loss_pct` OU le tampon, l'autre étant fixé,
        pour que leur somme (le seuil effectif) reste <=
        `MIN_EFFECTIVE_STOP_LOSS_PCT` — un stop réel, jamais nul ni positif.
        Symétrique : sert aux deux mécanismes qui les ajustent séparément.
        """
        return MIN_EFFECTIVE_STOP_LOSS_PCT - other_value

    def _bounded_set(
        self, path: str, value: float, reason: str, sample: int
    ) -> Optional[str]:
        """Écrit un paramètre en respectant ses bornes. None si rien ne change."""
        blocage = self._starving(path, value)
        if blocage:
            print(f"🧠 {blocage}")
            return None

        low, high = self.bounds.get(path, (float("-inf"), float("inf")))
        clamped = max(low, min(high, value))
        current = self.params.get(path)

        if current is not None and abs(clamped - current) < 1e-9:
            if abs(value - clamped) > 1e-9:
                print(f"🧠 {path} déjà à sa borne ({clamped}) — ajustement ignoré")
            return None

        self.params.set(path, clamped, reason, sample)
        return f"{path} -> {clamped}"

    def _tightens(self, path: str, value: float, current: float) -> Optional[bool]:
        """Ce changement restreint-il ? `None` = le sens n'est pas connu."""
        if path in TIGHTENS_WHEN_RAISED:
            return value > current
        if path in TIGHTENS_WHEN_LOWERED:
            return value < current
        return None

    def _relax_set(
        self, path: str, step: float, reason: str, sample: int
    ) -> Optional[str]:
        """Desserre un paramètre — ou refuse, si le clamp inverserait le sens.

        LE PIÈGE QUE ÇA FERME. Les bornes sont GLOBALES, la configuration d'un
        bras ne l'est pas. `quality` porte `max_age_hours = 48`, très au-dessus
        de sa borne `(2, 24)`. Un relâchement de +2 h donne 50, que
        `_bounded_set` clampe à 24 : la fenêtre du bras passerait de 48 h à
        24 h. **Un relâchement qui divise la fenêtre par deux est un
        resserrage**, et c'est exactement ce que ce chemin existe pour ne
        jamais faire.

        Refuser est le bon comportement, pas élargir la borne en douce : la
        borne est là pour dire jusqu'où l'apprentissage a le droit d'aller. Un
        écart entre elle et la config est une décision de propriétaire, à
        prendre dans le manifeste (`bounds`), pas au détour d'un ajustement.
        """
        current = self.params.get(path)
        if current is None:
            return None

        low, high = self.bounds.get(path, (float("-inf"), float("inf")))
        clamped = max(low, min(high, current + step))
        if self._tightens(path, clamped, current):
            print(
                f"🧠 {path} : relâchement REFUSÉ — {current} + {step} clampé à "
                f"{clamped} par la borne ({low}, {high}), ce qui RESSERRE. "
                f"La config du bras est hors de sa borne : à corriger dans "
                f"`bounds` du manifeste, pas ici."
            )
            return None
        return self._bounded_set(path, current + step, reason, sample)

    def _starving(self, path: str, value: float) -> Optional[str]:
        """Ce changement resserre-t-il un filtre alors que le flux est déjà nul ?

        L'asymétrie est le cœur de la garde : RELÂCHER reste toujours permis,
        quel que soit le flux. Seul le resserrage est conditionné. Bloquer les
        deux figerait le bot dans l'état étranglé où la boucle l'a mené.
        """
        if self.flow is None:
            return None
        current = self.params.get(path)
        if current is None:
            return None

        if path in TIGHTENS_WHEN_RAISED:
            resserre = value > current
        elif path in TIGHTENS_WHEN_LOWERED:
            resserre = value < current
        else:
            return None
        if not resserre:
            return None

        try:
            flux = self.flow()
        except Exception:
            return None
        if flux is None or flux >= MIN_FLOW_TO_TIGHTEN:
            return None

        return (
            f"{path} : resserrage {current} → {value} REFUSÉ — flux à "
            f"{flux:.0f} candidat(s)/cycle, sous le plancher de "
            f"{MIN_FLOW_TO_TIGHTEN:.0f}. Resserrer ici garantit de ne plus "
            f"jamais collecter la donnée qui dirait si c'était justifié."
        )

    def refresh_stats(self) -> dict[str, Any]:
        """Étapes 6.1 et 6.2 : stats globales + analyse par segment."""
        rows = self.journal.read_positions()
        wins = [r for r in rows if r.get("pnl_usd", 0) > 0]
        losses = [r for r in rows if r.get("pnl_usd", 0) <= 0]

        learning = self.params.get("learning", {})
        learning.update(
            {
                "total_trades": len(rows),
                "winning_trades": len(wins),
                "losing_trades": len(losses),
                "avg_win_pct": round(sum(r["pnl_pct"] for r in wins) / len(wins), 2)
                if wins
                else 0,
                "avg_loss_pct": round(sum(r["pnl_pct"] for r in losses) / len(losses), 2)
                if losses
                else 0,
                "win_rate_by_age": segment_stats(rows, "age_hours_at_entry", AGE_BUCKETS),
                "win_rate_by_liquidity": segment_stats(rows, "liquidity_at_entry", LIQ_BUCKETS),
                "win_rate_by_social_score": segment_stats(rows, "social_score", SOCIAL_BUCKETS),
            }
        )
        self.params.set("learning", learning, log=False)
        return learning

    def run(self, max_drawdown_pct: float = 0.0) -> list[str]:
        """Point d'entrée après chaque trade clôturé. Retourne les changements."""
        learning = self.refresh_stats()
        total = learning["total_trades"]
        # LES DEUX RELÂCHEMENTS PASSENT AVANT LA PORTE DES 15 TRADES, et ils
        # sont indépendants : le shadow prouve que les rejets montaient,
        # l'inactivité prouve que le bras ne joue plus. Un bras peut être dans
        # le second cas sans jamais atteindre le premier — c'est justement
        # `narrative`, zéro entrée sur 927 cycles.
        applied: list[str] = self._relax_from_inactivity()

        if total < MIN_TRADES_PER_ARM:
            # LE RELÂCHEMENT PAR LE SHADOW N'EST PAS SOUMIS À CE SEUIL, et
            # c'est le seul ajustement dans ce cas. Il ne lit AUCUN trade : il
            # lit les rejets suivis. Le garder derrière la porte des 15 trades
            # créait une poule-et-œuf de la même famille que celle corrigée
            # dans `economics` — un bras dont les filtres coupent tout ne
            # trade pas, donc n'atteint jamais 15, donc ne peut jamais
            # découvrir que ses filtres coupent trop. Le contrepoids était
            # inaccessible exactement dans la situation qui le réclame.
            #
            # Sans risque : `_relax_from_shadow` ne fait que RELÂCHER, et
            # `_starving` n'interdit que le resserrage.
            applied += self._relax_from_shadow()
            # Se taire sur le reste, mais le DIRE : « rien ajusté » et « pas
            # assez de données pour ajuster » sont deux états différents, et
            # les confondre fait croire que l'apprentissage tourne.
            return applied + [
                f"(en attente) {total}/{MIN_TRADES_PER_ARM} trades avant "
                f"tout ajustement sauf relâchements shadow et inactivité"
            ]

        if self._due("filters", total, FILTER_CADENCE):
            # Photo des filtres AVANT ajustement : le backtest doit pouvoir
            # revenir en arrière si le changement n'améliore rien.
            previous_filters = self.params.get("filters", {})
            filter_changes = self._adjust_filters(learning)
            if filter_changes:
                verdict = self.validate_filter_changes(previous_filters)
                if verdict and "annulés" in verdict:
                    print(f"🧠 BACKTEST | {verdict}")
                    filter_changes = [f"(annulé) {c}" for c in filter_changes]
                elif verdict:
                    print(f"🧠 BACKTEST | {verdict}")
            applied += filter_changes
        if self._due("weights", total, WEIGHTS_CADENCE):
            applied += self._adjust_weights(learning)
        if self._due("exits", total, EXIT_CADENCE):
            # Photo AVANT ajustement, comme pour les filtres : le rejeu doit
            # pouvoir revenir en arrière.
            previous_exits = self.params.get("exit_rules", {})
            exit_changes = self._adjust_exits(learning) + self._search_exits()
            if exit_changes:
                verdict = self.validate_exit_changes(previous_exits)
                if verdict and "annulées" in verdict:
                    print(f"🧠 BACKTEST | {verdict}")
                    exit_changes = [f"(annulé) {c}" for c in exit_changes]
                elif verdict:
                    print(f"🧠 BACKTEST | {verdict}")
            applied += exit_changes
        if self._due("risk", total, RISK_CADENCE):
            applied += self._adjust_risk(learning, max_drawdown_pct)
        return applied

    def _due(self, key: str, total_trades: int, cadence: int) -> bool:
        """Un palier ne déclenche qu'une fois, même si `run` est rappelé."""
        if total_trades < cadence:
            return False
        last = self.params.get(f"learning.last_adjustment_at.{key}", 0) or 0
        if total_trades - last < cadence:
            return False
        self.params.set(f"learning.last_adjustment_at.{key}", total_trades, log=False)
        return True

    def _adjust_filters(self, learning: dict) -> list[str]:
        """Étape 6.3.A — resserre OU relâche les filtres, dans leurs bornes."""
        changes: list[str] = []
        by_age = learning.get("win_rate_by_age", {})
        by_liq = learning.get("win_rate_by_liquidity", {})

        young = by_age.get("0-1h")
        if young and young["sample"] >= MIN_SEGMENT_SAMPLE and young["win_rate"] < 30:
            change = self._bounded_set(
                "filters.min_age_hours",
                self.params.get("filters.min_age_hours", 0.5) + 0.5,
                f"WR {young['win_rate']}% sur tokens 0-1h",
                young["sample"],
            )
            if change:
                changes.append(change)

        old = by_age.get("4h+")
        if old and old["sample"] >= MIN_SEGMENT_SAMPLE and old["win_rate"] < 30:
            change = self._bounded_set(
                "filters.max_age_hours",
                self.params.get("filters.max_age_hours", 6) - 0.5,
                f"WR {old['win_rate']}% sur tokens 4h+",
                old["sample"],
            )
            if change:
                changes.append(change)

        low_liq = by_liq.get("10-30K")
        high_liq = by_liq.get(">50K")
        raise_liq_reason = None
        if low_liq and low_liq["sample"] >= MIN_SEGMENT_SAMPLE and low_liq["win_rate"] < 25:
            raise_liq_reason = f"WR {low_liq['win_rate']}% sous 30K"
        elif high_liq and high_liq["sample"] >= MIN_SEGMENT_SAMPLE and high_liq["win_rate"] > 60:
            raise_liq_reason = f"WR {high_liq['win_rate']}% au-dessus de 50K"
        if raise_liq_reason:
            change = self._bounded_set(
                "filters.min_liquidity_usd",
                self.params.get("filters.min_liquidity_usd", 15000) + 5000,
                raise_liq_reason,
                MIN_SEGMENT_SAMPLE,
            )
            if change:
                changes.append(change)

        changes += self._relax_from_shadow()
        return changes

    def _relax_from_shadow(self) -> list[str]:
        """Relâche un filtre dont les rejets partaient à +100%.

        Sans ce contre-poids, toutes les règles ci-dessus ne font que resserrer
        et le bot finit par ne plus rien acheter du tout.
        """
        if self.shadow is None:
            return []

        changes = []
        for family, stats in self.shadow.missed_rate_by_family(SHADOW_MIN_SAMPLE).items():
            target = RELAXATIONS.get(family)
            if target is None or stats["missed_rate"] < MISSED_RATE_RELAX_THRESHOLD:
                continue
            path, step = target
            change = self._relax_set(
                path,
                step,
                f"{stats['missed_rate']}% des rejets '{family}' auraient fait +100% "
                f"(pic moyen {stats['avg_peak_gain_pct']}%)",
                stats["sample"],
            )
            if change:
                changes.append(change)
        return changes

    def _relax_from_inactivity(self) -> list[str]:
        """Desserre le seuil qui bloque un bras qui n'entre plus du tout.

        LE TROU QUE ÇA BOUCHE. `_relax_from_shadow` demande des PREUVES : des
        rejets suivis 4 h, jugés, et dont plus de 25 % seraient montés. Un bras
        qui n'entre jamais produit bien des rejets — mais tant qu'ils n'ont pas
        atteint `SHADOW_MIN_SAMPLE` dans une famille, rien ne bouge. Et tous
        les autres ajustements attendent 15 trades qu'il n'aura jamais.

        Ici la preuve est d'une autre nature : **le bras a vu passer 300 cycles
        et n'a rien pris.** Ce n'est pas « ses rejets auraient gagné », c'est
        « il ne joue pas ». Les deux méritent un relâchement, pour des raisons
        différentes.

        CE QUI EST DESSERRÉ : le motif de rejet DOMINANT de ce bras, lu dans
        l'entonnoir. Pas un seuil au hasard — celui qui tue le plus de
        candidats. Un seul par passage : desserrer cinq seuils d'un coup rend
        l'effet du changement illisible.

        NE S'APPLIQUE PAS À UN BRAS GELÉ (`self.frozen`, aucun aujourd'hui) ni
        aux familles de sécurité, absentes de `RELAXATIONS`.
        """
        if self.frozen or self.inactivity is None:
            return []

        cycles, motifs = self.inactivity()
        if cycles is None or cycles < INACTIVITY_CYCLES:
            return []
        if not motifs:
            # Aucun rejet enregistré : le bras ne voit rien passer du tout.
            # Ce n'est pas un seuil trop strict, c'est la fenêtre de
            # découverte — desserrer un filtre au hasard n'y changerait rien.
            print(
                f"🧠 inactif depuis {cycles} cycles mais aucun motif de rejet — "
                f"le problème est en amont des filtres, rien à desserrer"
            )
            return []

        from src.core.shadow import reason_family

        # ON DESCEND LA LISTE JUSQU'AU PREMIER QUI BOUGE. Le motif dominant
        # peut être déjà à sa borne : `quality` est bloqué sur l'âge à 24 h,
        # son plafond. S'arrêter au premier laisserait un bras inactif
        # indéfiniment sans rien tenter, ce qui viderait ce mécanisme de son
        # sens. Un seul changement est appliqué — desserrer plusieurs seuils
        # d'un coup rendrait l'effet illisible.
        essayes = []
        for motif in motifs:
            famille = reason_family(motif)
            target = RELAXATIONS.get(famille)
            if target is None:
                essayes.append(f"{motif} (famille {famille}, aucun paramètre)")
                continue
            path, step = target
            change = self._relax_set(
                path,
                step,
                f"inactif depuis {cycles} cycles sans entrée "
                f"(seuil {INACTIVITY_CYCLES}) — motif « {motif} »",
                cycles,
            )
            if change:
                return [change]
            essayes.append(f"{motif} -> {path} (sans effet)")

        print(
            f"🧠 inactif depuis {cycles} cycles, AUCUN seuil desserrable : "
            + " | ".join(essayes)
            + ". Le blocage n'est plus dans les filtres — voir les bornes du "
            "manifeste ou la fenêtre de découverte."
        )
        return []

    def _adjust_weights(self, learning: dict) -> list[str]:
        """Étape 6.3.B — renforce le critère du meilleur segment social."""
        by_social = learning.get("win_rate_by_social_score", {})
        strong = by_social.get("fort")
        weak = by_social.get("faible")
        weights = self.params.get("scoring_weights", {})
        if not weights:
            return []

        delta = 0.0
        reason = ""
        if strong and strong["sample"] >= MIN_SEGMENT_SAMPLE and strong["win_rate"] > 60:
            delta, reason = 0.05, f"WR {strong['win_rate']}% quand le social est fort"
        elif weak and weak["sample"] >= MIN_SEGMENT_SAMPLE and weak["win_rate"] < 30:
            delta, reason = -0.05, f"WR {weak['win_rate']}% quand le social est faible"
        if delta == 0:
            return []

        current = weights.get("social_sentiment", 0.20)
        target = max(0.05, min(0.40, current + delta))
        if abs(target - current) < 1e-9:
            return []

        others = {k: v for k, v in weights.items() if k != "social_sentiment"}
        total_others = sum(others.values())
        shift = target - current
        new_weights = {"social_sentiment": round(target, 3)}
        for key, value in others.items():
            new_weights[key] = round(max(0.05, value - shift * value / total_others), 3)

        self.params.set("scoring_weights", new_weights, reason, MIN_SEGMENT_SAMPLE)
        return [f"scoring_weights.social_sentiment -> {target:.2f}"]

    def _recalibrate_slippage_buffer(self, rows: list[dict[str, Any]]) -> Optional[str]:
        """Augmente le tampon si le stop dépasse ENCORE son seuil effectif.

        LE TAMPON POSÉ LE 2026-08-03 ÉTAIT UN INSTANTANÉ FIGÉ, calé une fois
        sur un rejeu manuel. Rien ne le remettait à jour. Cette méthode ferme
        ce trou en le recalibrant à chaque cadence de sorties (`EXIT_CADENCE`),
        sur le vécu propre de CE bras.

        `stop_loss_trigger_pct`, écrit au journal, est déjà
        `effective_stop_loss_pct` = seuil configuré + tampon ALORS actif (voir
        `Position.effective_stop_loss_pct`). `measured_slippage` mesure donc le
        RÉSIDU après ce tampon, pas le dépassement brut — exactement ce qu'il
        faut pour savoir si le tampon en place suffit encore.

        NE DIMINUE JAMAIS LE TAMPON AUTOMATIQUEMENT. Même asymétrie que
        `_starving` côté filtres, en miroir côté sorties : augmenter le tampon
        déclenche la sortie plus tôt, donc protège plus — c'est le sens sûr à
        automatiser. Le réduire tolérerait une perte réelle plus large sans
        preuve équivalente ; ça resterait un choix du propriétaire.
        """
        stop_loss_rows = [
            r for r in rows if str(r.get("exit_reason", "")).startswith("STOP_LOSS")
        ]
        if len(stop_loss_rows) < MIN_SEGMENT_SAMPLE:
            return None

        residu = -self.measured_slippage(stop_loss_rows)
        if residu <= 0:
            # Le tampon actuel couvre déjà le glissement mesuré. Rien à faire —
            # et surtout pas le réduire sur ce seul signal.
            return None

        courant = self.params.get(STOP_LOSS_BUFFER_PATH, 0.0) or 0.0
        stop_loss_pct = self.params.get(STOP_LOSS_PCT_PATH, -25)
        proposed = min(round(courant + residu, 1), self._stop_loss_ceiling(stop_loss_pct))
        return self._bounded_set(
            STOP_LOSS_BUFFER_PATH,
            proposed,
            f"tampon augmenté : {len(stop_loss_rows)} sorties stop loss "
            f"dépassent encore leur seuil effectif de {residu:.2f} pts "
            f"(médiane) même avec le tampon actuel de {courant:.1f}",
            len(stop_loss_rows),
        )

    def _adjust_exits(self, learning: dict) -> list[str]:
        """Étape 6.3.C — recalibre stop loss et take profit sur le vécu."""
        changes = []
        rows = self.journal.read_positions()
        stop_loss = self.params.get("exit_rules.stop_loss_pct", -25)
        avg_loss = learning.get("avg_loss_pct", 0)

        buffer_change = self._recalibrate_slippage_buffer(rows)
        if buffer_change:
            changes.append(buffer_change)

        # Pertes bien plus grandes que le SL -> le SL n'est pas atteignable
        # (gap ou rug) : le resserrer pour sortir plus tôt. Plafonné par le
        # tampon ACTUEL (relu après un éventuel changement ci-dessus) pour que
        # stop_loss_pct + tampon reste un stop réel — voir `_stop_loss_ceiling`.
        if avg_loss and avg_loss < stop_loss - 10:
            buffer = self.params.get(STOP_LOSS_BUFFER_PATH, 0.0) or 0.0
            proposed = min(round(stop_loss + 5, 1), self._stop_loss_ceiling(buffer))
            change = self._bounded_set(
                STOP_LOSS_PCT_PATH,
                proposed,
                f"perte moyenne {avg_loss}% bien au-delà du SL {stop_loss}%",
                learning.get("losing_trades", 0),
            )
            if change:
                changes.append(change)

        time_stops = [r for r in rows if str(r.get("exit_reason", "")).startswith("TIME_STOP")]
        winning_time_stops = [r for r in time_stops if r.get("pnl_pct", 0) > 30]
        if len(winning_time_stops) >= MIN_SEGMENT_SAMPLE:
            change = self._bounded_set(
                "exit_rules.max_hold_time_minutes",
                self.params.get("exit_rules.max_hold_time_minutes", 240) + 30,
                f"{len(winning_time_stops)} trades gagnants coupés par le time stop",
                len(winning_time_stops),
            )
            if change:
                changes.append(change)

        trailing = [r for r in rows if str(r.get("exit_reason", "")).startswith("TRAILING")]
        if len(trailing) >= MIN_SEGMENT_SAMPLE and _win_rate(trailing) > 80:
            change = self._bounded_set(
                "exit_rules.trailing_stop_distance_pct",
                self.params.get("exit_rules.trailing_stop_distance_pct", 50) + 10,
                f"{len(trailing)} sorties trailing quasi toutes gagnantes : trop serré",
                len(trailing),
            )
            if change:
                changes.append(change)

        return changes

    def _adjust_risk(self, learning: dict, max_drawdown_pct: float = 0.0) -> list[str]:
        """Étape 6.3.D — ajuste le risque par trade, jamais hors des bornes."""
        rows = self.journal.read_positions()
        if len(rows) < RISK_CADENCE:
            return []

        wins = [r for r in rows if r.get("pnl_usd", 0) > 0]
        gross_win = sum(r["pnl_usd"] for r in wins)
        gross_loss = abs(sum(r["pnl_usd"] for r in rows if r.get("pnl_usd", 0) <= 0))
        win_rate = 100 * len(wins) / len(rows)
        profit_factor = gross_win / gross_loss if gross_loss > 0 else 0.0
        current = self.params.get("risk_per_trade", 0.03)

        if win_rate > 50 and profit_factor > 2.0:
            new = round(current + 0.005, 4)
            reason = f"WR {win_rate:.0f}% et profit factor {profit_factor:.2f}"
        elif win_rate < 35 or max_drawdown_pct > 20:
            new = round(current - 0.005, 4)
            reason = f"WR {win_rate:.0f}%, drawdown {max_drawdown_pct:.0f}%"
        else:
            return []

        change = self._bounded_set("risk_per_trade", new, reason, len(rows))
        return [change] if change else []

    # ------------------------------------------------------- rejeu / entrée

    def simulate_filters(
        self, rows: list[dict[str, Any]], filters: dict[str, Any]
    ) -> dict[str, Any]:
        """Rejoue les trades passés sous un jeu de filtres d'ENTRÉE donné.

        Le journal conserve la liquidité, les holders, l'âge et le social au
        moment de l'entrée : ces filtres-là se rejouent exactement.

        Pour les règles de SORTIE, voir `simulate_exits` — le journal stocke
        depuis l'instrumentation le pic (`peak_pct`) et le creux
        (`trough_pct`), ce qui suffit à savoir si un seuil a été franchi.
        """
        kept = []
        for row in rows:
            liquidity = row.get("liquidity_at_entry")
            if liquidity is not None and liquidity < filters.get("min_liquidity_usd", 0):
                continue
            holders = row.get("holders_at_entry")
            if holders is not None and holders < filters.get("min_holders", 0):
                continue
            age = row.get("age_hours_at_entry")
            if age is not None and not (
                filters.get("min_age_hours", 0) <= age <= filters.get("max_age_hours", 999)
            ):
                continue
            social = row.get("social_score")
            if social is not None and social < filters.get("min_social_mentions_1h", 0):
                continue
            rugcheck = row.get("rugcheck_score")
            if rugcheck is not None and rugcheck < filters.get("min_rugcheck_score", 0):
                continue
            kept.append(row)

        total_pnl = sum(r.get("pnl_usd", 0) for r in kept)
        return {
            "trades": len(kept),
            "total_pnl_usd": round(total_pnl, 4),
            "pnl_per_trade": round(total_pnl / len(kept), 4) if kept else 0.0,
            "win_rate": round(_win_rate(kept), 1),
        }

    def validate_filter_changes(self, previous_filters: dict[str, Any]) -> Optional[str]:
        """Étape 6.4 — annule un ajustement de filtres qui n'améliore rien."""
        rows = self.journal.read_positions()[-BACKTEST_WINDOW:]
        if len(rows) < BACKTEST_WINDOW:
            return None  # échantillon trop court pour trancher

        before = self.simulate_filters(rows, previous_filters)
        after = self.simulate_filters(rows, self.params.get("filters", {}))

        if after["trades"] < BACKTEST_MIN_KEPT:
            self.params.set(
                "filters", previous_filters,
                f"backtest : {after['trades']} trades restants sur {len(rows)}, "
                f"filtres trop restrictifs — annulé",
                len(rows),
            )
            return "filtres annulés (trop restrictifs)"

        gain = after["pnl_per_trade"] - before["pnl_per_trade"]
        threshold = abs(before["pnl_per_trade"]) * BACKTEST_MIN_IMPROVEMENT
        if gain < threshold:
            self.params.set(
                "filters", previous_filters,
                f"backtest : P&L/trade {before['pnl_per_trade']} → {after['pnl_per_trade']}, "
                f"pas d'amélioration ≥ {BACKTEST_MIN_IMPROVEMENT:.0%} — annulé",
                len(rows),
            )
            return "filtres annulés (backtest non concluant)"

        return (
            f"filtres validés (P&L/trade {before['pnl_per_trade']} → {after['pnl_per_trade']})"
        )

    # ------------------------------------------------------- rejeu / sortie

    def measured_slippage(self, rows: list[dict[str, Any]]) -> float:
        """Écart médian entre le P&L réel et le seuil qui a déclenché la sortie.

        Médiane et pas moyenne : un gap de -25 points n'est pas du glissement
        d'échantillonnage, et une moyenne le laisserait dominer la mesure.
        """
        ecarts = [
            row.get("final_leg_pnl_pct", row.get("pnl_pct")) - row["stop_loss_trigger_pct"]
            for row in rows
            if str(row.get("exit_reason", "")).startswith("STOP_LOSS")
            and row.get("stop_loss_trigger_pct") is not None
            and row.get("final_leg_pnl_pct", row.get("pnl_pct")) is not None
        ]
        return round(statistics.median(ecarts), 2) if ecarts else DEFAULT_SL_SLIPPAGE

    def measured_breakeven_rest(self, rows: list[dict[str, Any]]) -> float:
        """Ce que rapporte le second lot après un TP1, mesuré.

        Sur les données réelles c'est -2,9%, pas 0 : le passage au breakeven
        après TP1 rend systématiquement la seconde moitié un peu en dessous du
        prix d'entrée. Supposer 0 surestimerait chaque scénario avec TP.
        """
        rests = [
            leg["pnl_pct"]
            for leg in self.journal.read_all()
            if str(leg.get("exit_reason", "")).startswith("BREAKEVEN_STOP")
            and leg.get("pnl_pct") is not None
        ]
        return round(statistics.median(rests), 2) if rests else DEFAULT_BREAKEVEN_REST

    @staticmethod
    def _tp_before_sl(row: dict[str, Any], ambiguity: str) -> tuple[bool, bool]:
        """Le pic a-t-il précédé le creux ? Retourne (tp_avant, par_fuite).

        FUITE DE DONNÉES — le défaut le plus grave du rejeu, corrigé ici.
        Sans horodatage du creux, la version précédente déduisait l'ordre de
        la RAISON DE SORTIE HISTORIQUE : « sortie en breakeven donc TP1 avait
        été touché ». C'est circulaire. Cette raison a été produite par les
        règles qu'on cherche justement à évaluer ; l'utiliser revient à
        supposer que la configuration testée s'est comportée comme
        l'historique, ce qui est exactement la question posée.

        Effet mesuré : les 3 positions ambiguës sont 3 des 4 gagnantes.
        Politique pessimiste −2,76 $/trade, optimiste −1,14 $. La fuite
        choisissait systématiquement la plus favorable.

        Le second membre du tuple signale la déduction non fiable. Les
        appelants qui DÉCIDENT (`_search_exits`) doivent refuser d'agir
        dessus ; ceux qui INFORMENT peuvent l'afficher, en le disant.
        """
        minutes_to_peak = row.get("minutes_to_peak")
        minutes_to_trough = row.get("minutes_to_trough")
        if minutes_to_peak is not None and minutes_to_trough is not None:
            return minutes_to_peak < minutes_to_trough, False
        if ambiguity == "optimistic":
            return True, False
        # Défaut et `resolve` : PESSIMISTE. Ne pas savoir doit coûter, pas
        # rapporter. Un rejeu qui, dans le doute, choisit l'issue favorable
        # produit des paramètres optimistes qu'on découvre faux en réel.
        return False, True

    def simulate_exits(
        self,
        positions: list[dict[str, Any]],
        exit_rules: dict[str, Any],
        ambiguity: str = "resolve",
    ) -> dict[str, Any]:
        """Rejoue les positions passées sous un jeu de règles de SORTIE.

        Repose sur les deux statistiques d'ordre de la trajectoire, `peak_pct`
        (plus fort gain latent) et `trough_pct` (plus forte perte latente) :
        elles suffisent à savoir SI un seuil a été franchi.

        CE QUE CETTE SIMULATION NE SAIT PAS — à lire avant d'agir dessus :

        1. L'ORDRE, sur les lignes antérieures à `minutes_to_trough`. Ce n'est
           pas marginal : aux réglages d'origine, 3 des 26 positions
           instrumentées sont ambiguës, et ce sont 3 des 4 gagnantes. Une
           politique `pessimistic` les transforme toutes en stop loss et
           conclut TOUJOURS « le SL va bien, le TP est inatteignable » ;
           `optimistic` conclut toujours l'inverse. `resolve` s'appuie sur la
           raison de sortie historique, ce qui est une FUITE : ça ne marche
           que parce que les règles d'alors prenaient déjà TP1 à +100%. Les
           trades postérieurs à l'instrumentation portent les deux dates et
           sont tranchés exactement.
        2. LE CHEMIN. -8 / +90 / -9 / +105 est indistinguable d'une montée
           droite. Les échelles TP1 -> TP2 -> TP3 ne sont pas séquençables.
        3. LE TRAILING, pas rejouable du tout : il dépend du plus-haut courant
           à chaque tick. `trailing_stop_*` est ignoré ici, volontairement.
        4. LE TEMPS. `max_hold_time_minutes` est hors périmètre : couper plus
           tôt suppose de connaître le P&L à cet instant, que le journal ne
           stocke pas.
        5. ÉLARGIR LE STOP est SOUS-ÉVALUÉ. Le cas « aucun seuil franchi »
           retombe sur le P&L réel, produit par le stop loss historique. La
           grille est indicative, jamais un motif de rejet.
        6. Le P&L reste optimiste côté take profit : ni slippage ni frais.
        """
        stop_loss = exit_rules.get("stop_loss_pct", -25)
        buffer_pct = exit_rules.get("stop_loss_slippage_buffer_pct", 0.0)
        stop_effective = stop_loss + buffer_pct
        tp1 = exit_rules.get("take_profit_1", 100)
        tp2 = exit_rules.get("take_profit_2", 300)
        f1 = exit_rules.get("partial_sell_tp1_pct", 0.5)
        f2 = exit_rules.get("partial_sell_tp2_pct", 0.5)

        slip = self.measured_slippage(positions)
        rest = self.measured_breakeven_rest(positions)

        total_pnl = 0.0
        wins = 0
        skipped = 0
        ambiguous = 0
        degraded = 0
        changed = 0
        by_outcome: dict[str, int] = {}
        evaluated = 0

        for row in positions:
            peak = row.get("peak_pct")
            if peak is None:
                skipped += 1
                continue
            trough = row.get("trough_pct")
            if trough is None:
                skipped += 1
                continue

            evaluated += 1
            size = row.get("position_size") or 0.0
            hit_stop = trough <= stop_effective
            hit_tp1 = peak >= tp1
            if hit_stop and hit_tp1:
                ambiguous += 1
                tp_first, guessed = self._tp_before_sl(row, ambiguity)
                if guessed:
                    degraded += 1
                hit_stop = not tp_first
                hit_tp1 = not hit_stop

            if hit_tp1:
                pnl_pct = f1 * tp1
                remaining = 1 - f1
                if peak >= tp2:
                    pnl_pct += remaining * f2 * tp2
                    remaining -= remaining * f2
                pnl_pct += remaining * rest
                outcome = "TP_LADDER" if peak >= tp2 else "TP1"
                pnl_usd = size * pnl_pct / 100
            elif hit_stop:
                pnl_pct = stop_effective + slip
                outcome = "SL"
                pnl_usd = size * pnl_pct / 100
            else:
                # Aucun seuil franchi : la position serait sortie par une règle
                # que ce rejeu ne modifie pas (temps, trailing, rug).
                outcome = "UNCHANGED"
                pnl_usd = row.get("pnl_usd", 0) or 0.0

            if outcome != "UNCHANGED":
                changed += 1
            by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
            total_pnl += pnl_usd
            if pnl_usd > 0:
                wins += 1

        return {
            "trades": evaluated,
            "total_pnl_usd": round(total_pnl, 4),
            "pnl_per_trade": round(total_pnl / evaluated, 4) if evaluated else 0.0,
            "win_rate": round(100 * wins / evaluated, 1) if evaluated else 0.0,
            "coverage": evaluated,
            "skipped": skipped,
            "ambiguous": ambiguous,
            # Positions dont l'ordre pic/creux a été DEVINÉ faute
            # d'horodatage. Un résultat majoritairement dégradé ne doit
            # pas servir à décider.
            "degraded": degraded,
            "changed": changed,
            "by_outcome": by_outcome,
            "slippage_used": slip,
            "breakeven_rest_used": rest,
        }

    def exit_grid(
        self,
        positions: list[dict[str, Any]],
        exit_rules: Optional[dict[str, Any]] = None,
        ambiguity: str = "resolve",
    ) -> list[dict[str, Any]]:
        """Grille SL x TP1, triée du meilleur P&L/trade au pire.

        36 évaluations sur au plus 20 positions : quelques microsecondes.
        """
        base = dict(exit_rules or self.params.get("exit_rules", {}))
        grid = []
        for stop_loss in SL_GRID:
            for tp1 in TP1_GRID:
                candidate = {**base, "stop_loss_pct": stop_loss, "take_profit_1": tp1}
                outcome = self.simulate_exits(positions, candidate, ambiguity)
                grid.append({"stop_loss_pct": stop_loss, "take_profit_1": tp1, **outcome})
        return sorted(grid, key=lambda row: row["pnl_per_trade"], reverse=True)

    def _in_window(self, row: dict[str, Any]) -> bool:
        """Ce trade serait-il entré dans la FENÊTRE de ce bras ?

        Ne rejoue PAS tous les filtres — seulement l'âge et la liquidité à
        l'entrée, les deux axes sur lesquels les bras sont réellement séparés
        (cf. la table du manifeste). Rejouer holders, concentration ou
        rugcheck écarterait des trajectoires pour des raisons qui ne changent
        pas la FORME de la trajectoire.

        Une donnée absente ne rejette pas : même invariant que le pipeline.
        """
        filtres = self.params.get("filters", {})
        age = row.get("age_hours_at_entry")
        liq = row.get("liquidity_at_entry")

        if age is not None:
            low, high = filtres.get("min_age_hours"), filtres.get("max_age_hours")
            if low is not None and age < low:
                return False
            if high is not None and age > high:
                return False
        if liq is not None:
            low = filtres.get("min_liquidity_usd")
            if low is not None and liq < low:
                return False
        return True

    def _instrumented(self, window: int = BACKTEST_WINDOW) -> list[dict[str, Any]]:
        """Positions récentes portant pic ET creux, du bras PUIS du pool.

        POURQUOI METTRE EN COMMUN. `simulate_exits` ne lit que `peak_pct` et
        `trough_pct` : **une trajectoire est une trajectoire**, elle ne dépend
        pas du bras dont les filtres l'ont admise. Mesuré au 2026-08-02 : 6
        positions instrumentées pour `scalp`, `runner` et `consensus`, 2 pour
        `quality`, 0 pour `narrative` — tous sous `EXIT_BACKTEST_MIN_COVERAGE`
        = 15, donc aucun verdict possible. Mises en commun : 75.

        POURQUOI FILTRER SUR LA FENÊTRE. Les trajectoires ne sont PAS
        interchangeables. Un token de `sniper` (âge 0-1 h, liquidité 4 K) n'a
        pas la même forme qu'un token de `quality` (âge 4-48 h, liquidité
        20 K). Mettre en commun sans filtre ferait juger les sorties d'un bras
        sur des trajectoires qu'il n'aurait jamais achetées — on remplacerait
        un manque de données par un biais, ce qui est pire : le manque se voit.

        Les positions du bras lui-même passent toujours : elles ont déjà
        franchi ses filtres, les rejuger sur des seuils qui ont bougé depuis
        les écarterait à tort.
        """
        propres = [
            row
            for row in self.journal.read_positions()[-window:]
            if row.get("peak_pct") is not None and row.get("trough_pct") is not None
        ]
        if self.pool is None:
            return propres

        connus = {row.get("position_id") or id(row) for row in propres}
        empruntes = [
            row
            for row in self.pool()
            if row.get("peak_pct") is not None
            and row.get("trough_pct") is not None
            and (row.get("position_id") or id(row)) not in connus
            and self._in_window(row)
        ]
        return propres + empruntes[-window:]

    def _search_exits(self) -> list[str]:
        """Cherche le meilleur couple (stop loss, TP1) sur l'historique rejoué.

        Débloque un apprentissage qui ne pouvait rien faire : la seule règle de
        `_adjust_exits` sur le stop loss ne sait que le resserrer, et il est
        déjà collé à sa borne basse — elle retournait `None` à chaque appel,
        définitivement.
        """
        positions = self._instrumented()
        if len(positions) < EXIT_BACKTEST_MIN_COVERAGE:
            return []

        current = self.params.get("exit_rules", {})
        reference = self.simulate_exits(positions, current)

        # Refuser de DÉCIDER sur un rejeu majoritairement deviné. L'ordre
        # pic/creux manque sur les lignes antérieures à `minutes_to_trough`,
        # et ces positions ambiguës sont précisément les gagnantes : régler un
        # stop loss dessus reviendrait à optimiser une supposition.
        if reference["degraded"] > len(positions) * 0.25:
            print(
                f"🧠 recherche de sorties suspendue — {reference['degraded']}/"
                f"{len(positions)} positions sans horodatage du creux, l'ordre "
                f"est deviné. Les nouveaux trades le portent."
            )
            return []

        best = None
        for row in self.exit_grid(positions, current):
            # Un « gagnant » qui ne change presque rien ne fait que recopier le
            # résultat historique : ce n'est pas une découverte.
            if row["changed"] < EXIT_BACKTEST_MIN_CHANGED:
                continue
            low, high = self.bounds.get("exit_rules.stop_loss_pct", (-100.0, 0.0))
            if not low <= row["stop_loss_pct"] <= high:
                continue
            low, high = self.bounds.get("exit_rules.take_profit_1", (0.0, 1000.0))
            if not low <= row["take_profit_1"] <= high:
                continue
            best = row
            break

        if best is None:
            return []
        gain = best["pnl_per_trade"] - reference["pnl_per_trade"]
        if gain < abs(reference["pnl_per_trade"]) * BACKTEST_MIN_IMPROVEMENT:
            return []

        reason = (
            f"rejeu sur {len(positions)} positions : P&L/trade "
            f"{reference['pnl_per_trade']} → {best['pnl_per_trade']} "
            f"({best['ambiguous']} ambiguës)"
        )
        changes = []
        for path, value in (
            ("exit_rules.stop_loss_pct", best["stop_loss_pct"]),
            ("exit_rules.take_profit_1", best["take_profit_1"]),
        ):
            change = self._bounded_set(path, value, reason, len(positions))
            if change:
                changes.append(change)
        return changes

    def validate_exit_changes(self, previous_exits: dict[str, Any]) -> Optional[str]:
        """Annule un ajustement de sorties qui n'améliore rien sur l'historique.

        Sous `EXIT_BACKTEST_MIN_COVERAGE` positions instrumentées : aucun
        verdict, et surtout aucune annulation. Ne pas savoir n'est pas une
        raison de revenir en arrière.
        """
        positions = self._instrumented()
        if len(positions) < EXIT_BACKTEST_MIN_COVERAGE:
            return None

        before = self.simulate_exits(positions, previous_exits)
        after = self.simulate_exits(positions, self.params.get("exit_rules", {}))

        gain = after["pnl_per_trade"] - before["pnl_per_trade"]
        threshold = abs(before["pnl_per_trade"]) * BACKTEST_MIN_IMPROVEMENT
        if gain < threshold:
            self.params.set(
                "exit_rules",
                previous_exits,
                f"backtest sorties : P&L/trade {before['pnl_per_trade']} → "
                f"{after['pnl_per_trade']}, pas d'amélioration ≥ "
                f"{BACKTEST_MIN_IMPROVEMENT:.0%} — annulé",
                len(positions),
            )
            return "sorties annulées (backtest non concluant)"

        return (
            f"sorties validées (P&L/trade {before['pnl_per_trade']} → "
            f"{after['pnl_per_trade']})"
        )

    def live_mode_allowed(self) -> tuple[bool, str]:
        """Règle 10 de la spec, PLUS un verrou propriétaire explicite.

        Les critères statistiques (20 trades, WR > 40%, PF > 1.5) sont une
        condition NÉCESSAIRE, jamais suffisante. Le propriétaire a demandé que
        le passage en réel ne se fasse que sur sa décision explicite : tant que
        `live_mode_authorized_by_owner` est false, cette méthode retourne
        toujours False, quels que soient les chiffres.
        """
        if not self.params.get("live_mode_authorized_by_owner", False):
            return False, "verrou propriétaire — passage en réel non autorisé"

        rows = self.journal.read_positions()
        if len(rows) < 20:
            return False, f"{len(rows)}/20 trades papier"

        wins = [r for r in rows if r.get("pnl_usd", 0) > 0]
        win_rate = 100 * len(wins) / len(rows)
        gross_win = sum(r["pnl_usd"] for r in wins)
        gross_loss = abs(sum(r["pnl_usd"] for r in rows if r.get("pnl_usd", 0) <= 0))
        profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

        if win_rate <= 40:
            return False, f"win rate {win_rate:.0f}% ≤ 40%"
        if profit_factor <= 1.5:
            return False, f"profit factor {profit_factor:.2f} ≤ 1.5"

        # VERROU STATISTIQUE, ajouté après l'audit. Les trois seuils ci-dessus
        # portent sur des POINTS : un win rate de 41% sur 20 trades a un
        # intervalle de confiance à 95% qui va d'environ 20% à 65%. Passer en
        # réel sur ce chiffre revient à parier sur la borne haute.
        #
        # Le seul critère non négociable : la borne BASSE de l'intervalle du
        # P&L par trade doit être positive. Tant qu'elle ne l'est pas, perdre
        # de l'argent reste une issue compatible avec les données.
        from src.core.stats import pnl_per_trade_interval

        interval = pnl_per_trade_interval(rows)
        if interval is None:
            return False, "échantillon trop court pour un intervalle de confiance"
        if interval.low <= 0:
            return False, (
                f"P&L/trade {interval.value:+.2f} $ IC95 "
                f"[{interval.low:+.2f} .. {interval.high:+.2f}] — la borne basse "
                f"n'est pas positive, perdre reste compatible avec les données"
            )

        return True, (
            f"WR {win_rate:.0f}%, PF {profit_factor:.2f}, P&L/trade IC95 "
            f"[{interval.low:+.2f} .. {interval.high:+.2f}] sur {len(rows)} trades"
        )
