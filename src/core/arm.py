"""Un bras = une stratégie complète et isolée.

Ses filtres de sélection, ses règles de sortie, son portefeuille, son journal,
son shadow log et son apprentissage. Plusieurs bras jugent le MÊME lot enrichi
(cf. `src/pipeline.py`) : la comparaison est à budget API constant.

POURQUOI UN FICHIER PAR BRAS, et pas un document unique découpé en sections :

  - `ParamsStore.set` réécrit tout le document. N stores sur un fichier, c'est
    dernier écrivain gagne — au niveau du document entier.
  - Le chemin d'historique `learning.parameter_adjustment_history` est en dur
    dans `ParamsStore`. Sans fichiers séparés, tous les bras écrivent dans la
    même liste, sans attribution.
  - `LearningEngine` lit et écrit une douzaine de chemins de premier niveau.
    Un seul oublié à l'isolement = contamination silencieuse entre bras.
  - L'écriture atomique de `ParamsStore` est par fichier.

Le coût assumé est la dérive : une clé globale ajoutée à `config/params.json`
n'apparaîtra pas dans `config/arms/*.json`. `ParamsStore.get(path, default)`
absorbe le cas, et les clés vraiment globales (cadences de scan, budgets API,
mode, capital) restent lues sur le document du bras témoin.
"""

import copy
import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from src import settings
from src.core.journal import TradeJournal
from src.core.learning import LearningEngine
from src.core.params import ParamsStore
from src.core.shadow import ShadowTracker

VOTER = "voter"
CONSENSUS = "consensus"
NOTIFY_POLICIES = ("all", "exits", "none")

# GAIN À PARTIR DUQUEL UNE STRATÉGIE SILENCIEUSE PARLE QUAND MÊME.
#
# Calé sur les taux d'atteinte mesurés (26 positions instrumentées) : +50 % est
# franchi par 23 % des trades, +100 % par 15 %. Un gain de cet ordre est donc
# dans le quart supérieur — assez rare pour valoir une alerte, contrairement
# aux pertes, dont 96 % touchent -10 % et qui resteraient du bruit permanent.
#
# POURQUOI CE SEUIL EXISTE. La politique `notify=none` a été écrite quand seul
# le témoin tradait. Depuis, le témoin n'a rien pris en 5 h pendant que les six
# autres bras produisaient 27 gagnants sur 93 trades : la règle « seul le
# témoin parle » revenait à taire exactement ce qu'on voulait voir.
NOTABLE_GAIN_PCT = 50.0


class ManifestError(RuntimeError):
    """Manifeste incohérent : mieux vaut refuser de démarrer que fausser tout."""


class ArmNotifier:
    """Adapte les appels du portefeuille au reporter, en portant le nom du bras.

    CE QUE CETTE CLASSE N'EST PLUS. Elle décidait quoi envoyer, par bras : sept
    politiques indépendantes, dont six à `none`. Résultat mesuré le 2026-08-02
    sur 108 trades : **16 sorties à +50 % ou plus jamais notifiées**, dont
    JORDAN +184,2 % et GOONER +107,2 %.

    La décision appartient maintenant à `TelegramReporterAgent`, qui arbitre
    par NATURE D'ÉVÉNEMENT et non par stratégie — la valeur d'un +184 % ne
    dépend pas de quel bras l'a produit. Ce qui reste ici est l'adaptation
    d'interface et le nom du bras dans le message.

    `poll_commands` n'est toujours PAS exposé : les commandes sont au niveau du
    processus, pas de la stratégie.
    """

    def __init__(self, inner: Any, arm_name: str, policy: str = "none"):
        self.inner = inner
        self.arm_name = arm_name
        # Conservée pour compatibilité de manifeste, mais elle ne décide PLUS
        # rien : `TelegramReporterAgent` arbitre par nature d'événement. Un
        # manifeste qui porte encore `notify` reste valide et sans effet.
        self.policy = policy
        self.digest: list[str] = []

    def send(self, message: str) -> None:
        """Message libre d'une stratégie. Groupé — jamais urgent en soi."""
        if self.inner is None:
            return
        self.inner.report_alert(f"{self.arm_name}", message)

    def send_entry(self, position: Any) -> None:
        if self.inner is None:
            return
        self.inner.report_entry(self.arm_name, position)

    def send_exit(self, position: Any, pnl_pct: float, reason: str, minutes: float) -> None:
        if self.inner is None:
            return
        self.inner.report_exit(self.arm_name, position, pnl_pct, reason, minutes)

    def send_milestone(self, position: Any, pnl_pct: float, palier: float) -> None:
        if self.inner is None:
            return
        self.inner.report_milestone(self.arm_name, position, pnl_pct, palier)

    def drain_digest(self) -> list[str]:
        messages, self.digest = self.digest, []
        return messages


@dataclass
class StrategyArm:
    name: str
    role: str = VOTER
    enabled: bool = True
    capital_pct: float = 1.0
    notify: str = "none"
    min_confluence: int = 0
    description: str = ""
    params: Optional[ParamsStore] = None
    journal: Optional[TradeJournal] = None
    shadow: Optional[ShadowTracker] = None
    learning: Optional[LearningEngine] = None
    # Rempli en phase 4 : en observation, un bras n'a pas de portefeuille.
    portfolio: Optional[Any] = None
    bounds: dict[str, tuple[float, float]] = field(default_factory=dict)

    @property
    def is_baseline(self) -> bool:
        return self.name == settings.BASELINE_ARM

    @property
    def is_voter(self) -> bool:
        return self.role == VOTER

    @property
    def label(self) -> str:
        """Préfixe de log. Vide pour le bras témoin : sa sortie ne change pas."""
        return "" if self.is_baseline else f" [{self.name}]"

    @property
    def entry_threshold(self) -> float:
        return self.params.get("scan.alpha_score_entry_threshold", 75)

    @property
    def max_positions(self) -> int:
        return self.params.get(
            "risk_rules.max_concurrent_positions",
            self.params.get("max_concurrent_positions", 3),
        )

    def filters(self) -> dict[str, Any]:
        return self.params.get("filters", {})

    def summary(self) -> dict[str, Any]:
        exits = self.params.get("exit_rules", {})
        return {
            "name": self.name,
            "role": self.role,
            "enabled": self.enabled,
            "capital_pct": self.capital_pct,
            "description": self.description,
            "entry_threshold": self.entry_threshold,
            "min_confluence": self.min_confluence,
            "stop_loss_pct": exits.get("stop_loss_pct"),
            "take_profit_1": exits.get("take_profit_1"),
            "max_hold_time_minutes": exits.get("max_hold_time_minutes"),
        }


def _set_dotted(document: dict[str, Any], path: str, value: Any) -> None:
    node = document
    parts = path.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def materialise_arm_params(name: str, base: dict[str, Any], overrides: dict[str, Any]) -> str:
    """Crée `config/arms/<nom>.json` s'il n'existe pas. Ne le régénère JAMAIS.

    Une fois créé, le document appartient au `LearningEngine` du bras. Le
    régénérer à chaque démarrage effacerait tout ce que le bras a appris, et
    les `overrides` du manifeste deviendraient un plafond invisible.
    """
    path = settings.arm_paths(name)["params"]
    if os.path.exists(path):
        return path

    document = copy.deepcopy(base)
    document["strategy"] = name
    # L'historique du témoin n'appartient pas au nouveau bras.
    document["learning"] = {}
    for dotted, value in overrides.items():
        _set_dotted(document, dotted, value)

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(document, fh, indent=2, ensure_ascii=False)
    print(f"[Arms] {name} : config créée depuis params.json ({len(overrides)} overrides)")
    return path


def _flow_reader(arm_name: str):
    """Lecture paresseuse du flux : l'entonnoir peut ne pas exister encore."""

    def read() -> Optional[float]:
        from src.core.funnel import recent_flow

        return recent_flow(settings.FUNNEL_LOG_PATH, arm_name)

    return read


# Lignes d'entonnoir à remonter pour juger l'inactivité. Mesuré au 2026-08-02 :
# 68 lignes par cycle tous bras confondus, et `INACTIVITY_CYCLES` vaut 300 —
# il faut donc voir au-delà de 300 cycles pour situer la dernière entrée.
# 30 000 lignes ≈ 440 cycles, soit 1,5 fois le seuil.
#
# Un bras qui n'est PAS entré depuis plus longtemps que cette fenêtre rend
# simplement le nombre de cycles qu'elle contient : une borne inférieure, déjà
# au-dessus du seuil. Aucune raison de lire plus.
INACTIVITY_TAIL_LINES = 30_000


def _pool_reader(arm_name: str, manifest_names: list[str]):
    """Trajectoires instrumentées des AUTRES bras, pour le rejeu des sorties.

    Lecture paresseuse et à chaque appel : les journaux grossissent pendant
    que la boucle tourne, et un instantané pris au démarrage vieillirait sans
    jamais se rafraîchir.
    """

    def read() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for autre in manifest_names:
            if autre == arm_name:
                continue
            chemin = settings.arm_paths(autre)["trades"]
            if not os.path.exists(chemin):
                continue
            rows += TradeJournal(chemin).read_positions()
        return rows

    return read


def _inactivity_reader(arm_name: str):
    """(cycles depuis la dernière entrée, motif de rejet dominant).

    Le chemin est résolu à l'APPEL, pas à la construction : les tests
    redirigent `settings.FUNNEL_LOG_PATH` après `bootstrap_arms`.
    """

    def read() -> tuple[Optional[int], Optional[str]]:
        from src.core.funnel import inactivity_snapshot

        return inactivity_snapshot(
            settings.FUNNEL_LOG_PATH, arm_name, tail=INACTIVITY_TAIL_LINES
        )

    return read


def load_manifest(path: Optional[str] = None) -> list[dict[str, Any]]:
    """Lit `config/strategies.json`. Absent = un seul bras, le témoin."""
    path = path or settings.STRATEGIES_PATH
    if not os.path.exists(path):
        return [{"name": settings.BASELINE_ARM, "role": VOTER, "capital_pct": 1.0,
                 "notify": "all"}]
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("arms", [])


def attach_portfolios(
    arms: list[StrategyArm],
    capital_total: float,
    notifier: Any = None,
    mode: str = "PAPER",
    cooldown_hours: float = 0.0,
    losses_trigger: int = 3,
    max_drawdown_stop_pct: float = 0.0,
) -> None:
    """Donne à chaque stratégie son portefeuille.

    EN PAPER, CHAQUE BRAS A UNE MISE INDÉPENDANTE ET IDENTIQUE, et
    `capital_pct` n'est PAS appliqué. Deux raisons :

    1. Découper rétroactivement casse le témoin. Son journal porte -166,46 $
       accumulés contre une mise de 1000 $ ; lui donner 15 % = 150 $ le fait
       démarrer à -16,46 $ de capital, c'est-à-dire mort.
    2. Ça fausserait la comparaison. `position_size` est proportionnel au
       capital : un bras à 5 % prendrait des positions dix fois plus petites
       qu'un bras à 50 % et afficherait un P&L dix fois moindre — pour une
       raison d'allocation, pas de qualité. On compare des stratégies, pas
       des tailles de portefeuille.

    `capital_pct` reste dans le manifeste : c'est le plan d'allocation pour
    un éventuel passage en réel, où le capital est fini. En PAPER il ne sert
    qu'à documenter l'intention.
    """
    from src.core.portfolio import PaperPortfolio

    for arm in arms:
        arm.portfolio = PaperPortfolio(
            capital=capital_total,
            journal=arm.journal,
            notifier=ArmNotifier(notifier, arm.name, arm.notify),
            mode=mode,
            positions_path=settings.arm_paths(arm.name)["positions"],
            cooldown_hours=cooldown_hours,
            losses_trigger=losses_trigger,
            max_drawdown_stop_pct=max_drawdown_stop_pct,
        )


def bootstrap_arms(
    manifest_path: Optional[str] = None,
    base_params: Optional[ParamsStore] = None,
    with_journals: bool = True,
) -> list[StrategyArm]:
    """Construit les bras actifs, dans l'ordre du manifeste, consensus en dernier.

    L'ordre compte : un bras consensus lit le décompte des votes, qui n'est
    complet qu'une fois tous les votants évalués.
    """
    base_params = base_params or ParamsStore(settings.PARAMS_PATH)
    base_document = base_params.data

    entries = [e for e in load_manifest(manifest_path) if e.get("enabled", True)]
    if not entries:
        raise ManifestError("aucune stratégie active dans le manifeste")

    names = [e["name"] for e in entries]
    if len(set(names)) != len(names):
        raise ManifestError(f"noms de stratégie en double : {names}")

    total = sum(float(e.get("capital_pct", 0)) for e in entries)
    if abs(total - 1.0) > 1e-6:
        raise ManifestError(
            f"capital_pct somme à {total:.4f} au lieu de 1.0 — du capital inventé "
            f"rendrait toute comparaison entre bras fausse"
        )

    arms = []
    for entry in entries:
        name = entry["name"]
        notify = entry.get("notify", "none")
        if notify not in NOTIFY_POLICIES:
            raise ManifestError(f"{name} : notify '{notify}' inconnu {NOTIFY_POLICIES}")

        if name == settings.BASELINE_ARM:
            params = base_params
        else:
            materialise_arm_params(name, base_document, entry.get("overrides", {}))
            params = ParamsStore(settings.arm_paths(name)["params"])

        paths = settings.arm_paths(name)
        journal = TradeJournal(paths["trades"]) if with_journals else None
        shadow = ShadowTracker(paths["shadow"]) if with_journals else None
        bounds = {
            key: (float(low), float(high))
            for key, (low, high) in (entry.get("bounds") or {}).items()
        }

        arms.append(
            StrategyArm(
                name=name,
                role=entry.get("role", VOTER),
                enabled=True,
                capital_pct=float(entry.get("capital_pct", 0)),
                notify=notify,
                min_confluence=int(entry.get("min_confluence", 0)),
                description=entry.get("description", ""),
                params=params,
                journal=journal,
                shadow=shadow,
                learning=(
                    LearningEngine(
                        params, journal, shadow=shadow, bounds=bounds,
                        # Le flux se lit dans l'entonnoir. Sans lui, resserrer
                        # un filtre paraît toujours prudent — même quand il ne
                        # reste plus rien à filtrer.
                        flow=_flow_reader(name),
                        # Un bras qui n'entre plus depuis 300 cycles est bloqué
                        # par ses propres seuils, et n'a aucun trade neuf pour
                        # s'en apercevoir.
                        inactivity=_inactivity_reader(name),
                        # Le témoin est gelé : seule référence comparable aux
                        # trades historiques, aucun relâchement automatique.
                        frozen=(name == settings.BASELINE_ARM),
                        # Rejeu des SORTIES seulement : une trajectoire ne
                        # dépend pas du bras qui l'a achetée. 6 positions
                        # instrumentées par bras contre 75 mises en commun.
                        pool=_pool_reader(name, names),
                    )
                    if journal is not None
                    else None
                ),
                bounds=bounds,
            )
        )

    arms.sort(key=lambda arm: arm.role == CONSENSUS)
    return arms
