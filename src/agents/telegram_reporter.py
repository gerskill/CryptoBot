"""Reporter Telegram — un seul point de sortie pour les sept stratégies.

CE QUI A MOTIVÉ CE MODULE, mesuré le 2026-08-02 sur 108 trades clôturés :

    16 sorties à +50 % ou plus n'ont JAMAIS été notifiées.

    JORDAN   +184,2 %   runner        CATECOIN  +123,9 %   sniper
    WOJAKOS  +158,6 %   baseline      windoge   +112,0 %   sniper
    CATECOIN +123,9 %   sniper        GOONER    +107,2 %   sniper
    ...

La cause n'était ni Telegram ni la comptabilité. Six bras sur sept étaient en
`notify=none`, une politique écrite quand seul le témoin tradait ; leurs
messages tombaient dans un digest cadencé à 4 h dont le compteur repartait à
zéro à chaque redémarrage. GOONER a fait +107,2 % en 10 minutes, en trois
jambes, et rien n'est parti.

LE PROBLÈME QUE CE MODULE DOIT RÉSOUDRE SANS EN CRÉER UN AUTRE. « Tout envoyer »
n'est pas la solution : à 8-32 trades par jour et par bras, entrées comprises,
c'est plusieurs centaines de messages quotidiens. Un canal qu'on finit par
couper ne notifie plus rien du tout — le silence par saturation vaut le silence
par politique.

D'où une règle par NATURE D'ÉVÉNEMENT plutôt que par bras :

    entrée              groupée       nombreuses, faible information unitaire
    sortie remarquable  immédiate     23 % des trades franchissent +50 %
    sortie routinière   groupée       96 % touchent -10 %, ce serait du bruit
    alerte              immédiate     mais dédupliquée : une API morte se
                                      répète à chaque cycle
    rapport             périodique    avec IC95, sinon on lit un point pour
                                      une mesure

Le bras n'entre plus dans la décision d'envoyer, seulement dans le contenu du
message. C'est ce qui répare le défaut de fond : la valeur d'un +184 % ne
dépend pas de quelle stratégie l'a produit.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from src.agents import _journal

# Gain à partir duquel une sortie part SEULE et tout de suite. Calé sur les
# taux d'atteinte mesurés (26 positions instrumentées) : +50 % est franchi par
# 23 % des trades, +100 % par 15 %. Le pic médian étant +4,9 %, un seuil plus
# bas ferait sonner presque chaque trade.
NOTABLE_GAIN_PCT = 50.0

# Une perte, aussi lourde soit-elle, ne part jamais seule : 96 % des trades
# touchent -10 %. Elle est comptée dans le lot groupé.
#
# EXCEPTION : le rug. Ce n'est pas un résultat de trading mais un incident, il
# renseigne sur la SÉLECTION et pas sur la stratégie de sortie.
RUG_REASONS = ("RUG", "RUG_PULL", "LIQUIDITY")

# Fenêtre de regroupement. Assez courte pour que l'information reste fraîche,
# assez longue pour que sept bras évaluant le même lot ne produisent pas sept
# messages en quelques secondes.
BATCH_WINDOW_SECONDS = 900.0
# Au-delà, on tronque et on DIT combien manquent : un lot amputé présenté comme
# complet est pire qu'un lot tronqué annoncé.
BATCH_MAX_LINES = 25

# Une alerte identique ne se répète pas avant ce délai. Sans ça, « Birdeye
# indisponible » partirait à chaque cycle, soit 960 fois par jour.
ALERT_COOLDOWN_SECONDS = 3600.0

# Cadence des rapports périodiques.
REPORT_EVERY_SECONDS = 3600.0


def is_notable(pnl_pct: float, reason: str = "") -> bool:
    """Cet événement mérite-t-il d'interrompre l'utilisateur ?

    Deux cas seulement : un gain rare, ou un rug. Tout le reste attend le lot.
    """
    if pnl_pct >= NOTABLE_GAIN_PCT:
        return True
    haut = (reason or "").upper()
    return any(motif in haut for motif in RUG_REASONS)


@dataclass
class TelegramReporterAgent:
    """Point de sortie unique. Décide QUOI envoyer et QUAND, jamais QUOI FAIRE.

    `inner` est un `TelegramNotifier` (ou tout objet portant `send`). Absent,
    l'agent reste fonctionnel et silencieux : les tests et un déploiement sans
    jeton empruntent le même chemin que la production.
    """

    inner: Any = None
    log_path: Optional[str] = None
    batch_window: float = BATCH_WINDOW_SECONDS
    report_every: float = REPORT_EVERY_SECONDS
    alert_cooldown: float = ALERT_COOLDOWN_SECONDS

    _batch: list[str] = field(default_factory=list)
    _batch_dropped: int = 0
    _last_batch_at: float = field(default_factory=time.time)
    _last_report_at: float = field(default_factory=time.time)
    _alerts_seen: dict[str, float] = field(default_factory=dict)

    # ------------------------------------------------------------------ envoi

    def _send(self, message: str, kind: str) -> bool:
        """Envoi réel. Toute panne est absorbée et journalisée.

        Une notification perdue ne doit jamais emporter la boucle : elle est
        postérieure au trade, qui est déjà écrit au journal. Invariant « la
        boucle ne meurt jamais ».
        """
        envoye = False
        if self.inner is not None:
            try:
                self.inner.send(message)
                envoye = True
            except Exception as exc:  # noqa: BLE001
                print(f"[Reporter] envoi {kind} impossible : {exc}")
        if self.log_path:
            _journal.append(self.log_path, {
                "ts": time.time(), "kind": kind, "sent": envoye,
                "message": message,
            })
        return envoye

    def _queue(self, ligne: str) -> None:
        """Ajoute au lot. Tronque en COMPTANT ce qui saute."""
        if len(self._batch) >= BATCH_MAX_LINES:
            self._batch_dropped += 1
            return
        self._batch.append(ligne)

    # ------------------------------------------------------------- événements

    def report_entry(self, arm_name: str, position: Any) -> None:
        """Une ouverture. Toujours groupée.

        Une entrée n'apprend rien seule : ce qui compte est ce qu'elle devient.
        Les envoyer en direct multiplierait par deux un canal déjà chargé, pour
        de l'information qui sera de toute façon republiée à la sortie.
        """
        self._queue(
            f"🟢 <b>{position.symbol}</b> [{arm_name}] "
            f"{position.size_usd:.2f} $ · SL {position.stop_loss_pct:g}%"
        )

    def report_exit(
        self,
        arm_name: str,
        position: Any,
        pnl_pct: float,
        reason: str,
        minutes: float,
    ) -> bool:
        """Une clôture. Immédiate si remarquable, groupée sinon.

        Retourne True si le message est parti tout de suite — utile aux tests
        et au journal, pas à l'appelant, qui ne doit rien décider là-dessus.
        """
        icone = "💰" if pnl_pct > 0 else "🔻"
        corps = (
            f"{icone} <b>{position.symbol}</b> [{arm_name}] "
            f"{pnl_pct:+.1f}% en {minutes:.0f} min — {reason}"
        )
        if is_notable(pnl_pct, reason):
            return self._send(corps, "exit_notable")
        self._queue(corps)
        return False

    def report_milestone(self, arm_name: str, position: Any, pnl_pct: float,
                         palier: float) -> bool:
        """Un palier de gain LATENT, sur une position encore ouverte.

        C'est le trou que ni `report_entry` ni `report_exit` ne couvrent : une
        position peut monter à +136 % et redescendre sans qu'aucun des deux ne
        se déclenche au bon moment. GOONER a fait son plus-haut à +135,7 % et
        n'est sorti qu'à +107,2 %.

        Toujours immédiat : un palier n'est franchi qu'une fois par position,
        le risque de saturation n'existe pas.
        """
        return self._send(
            f"🚀 <b>{position.symbol}</b> [{arm_name}] franchit +{palier:.0f}% "
            f"(latent {pnl_pct:+.1f}%, reste "
            f"{position.remaining_fraction * 100:.0f}%)",
            "milestone",
        )

    def report_alert(self, kind: str, message: str) -> bool:
        """Une alerte d'exploitation. Immédiate, mais DÉDUPLIQUÉE par `kind`.

        Sans le refroidissement, « Birdeye indisponible » partirait à chaque
        cycle — 960 fois par jour. L'alerte deviendrait le bruit qu'elle est
        censée percer.
        """
        maintenant = time.time()
        dernier = self._alerts_seen.get(kind)
        if dernier is not None and maintenant - dernier < self.alert_cooldown:
            return False
        self._alerts_seen[kind] = maintenant
        return self._send(f"⚠️ <b>{kind}</b>\n{message}", "alert")

    # ------------------------------------------------------------- périodique

    def due_for_batch(self, now: Optional[float] = None) -> bool:
        maintenant = now if now is not None else time.time()
        return bool(self._batch) and (
            maintenant - self._last_batch_at >= self.batch_window
        )

    def flush_batch(self, now: Optional[float] = None, force: bool = False) -> bool:
        """Envoie le lot accumulé en UN message."""
        maintenant = now if now is not None else time.time()
        if not self._batch or (not force and not self.due_for_batch(maintenant)):
            return False

        lignes = list(self._batch)
        if self._batch_dropped:
            lignes.append(f"<i>({self._batch_dropped} de plus, non détaillés)</i>")
        self._batch.clear()
        self._batch_dropped = 0
        self._last_batch_at = maintenant
        return self._send("📋 <b>Activité</b>\n" + "\n".join(lignes), "batch")

    def due_for_report(self, now: Optional[float] = None) -> bool:
        maintenant = now if now is not None else time.time()
        return maintenant - self._last_report_at >= self.report_every

    def report_periodic(
        self, arms: Sequence[Any], now: Optional[float] = None, force: bool = False
    ) -> bool:
        """Rapport de flotte, avec l'INTERVALLE et pas seulement le point.

        `pnl_per_trade_interval` plutôt qu'un P&L moyen nu : sur les
        échantillons de ce projet, un point est de la fausse précision. Le
        rapport doit dire ce qu'on ignore, sinon il fabrique une confiance que
        les données ne portent pas.
        """
        maintenant = now if now is not None else time.time()
        if not force and not self.due_for_report(maintenant):
            return False
        self._last_report_at = maintenant

        from src.core.stats import pnl_per_trade_interval

        positions: list[dict[str, Any]] = []
        lignes: list[str] = []
        for arm in arms:
            journal = getattr(arm, "journal", None)
            rows = journal.read_positions() if journal is not None else []
            positions += rows
            if not rows:
                continue
            gagnants = [r for r in rows if (r.get("pnl_usd") or 0) > 0]
            pnl = sum(r.get("pnl_usd") or 0 for r in rows)
            lignes.append(
                f"  <b>{arm.name}</b> {len(rows)} trades · "
                f"WR {100 * len(gagnants) / len(rows):.0f}% · {pnl:+.2f} $"
            )

        if not positions:
            return self._send("📊 <b>Rapport</b>\nAucun trade clôturé.", "report")

        gagnants = [r for r in positions if (r.get("pnl_usd") or 0) > 0]
        total = sum(r.get("pnl_usd") or 0 for r in positions)
        pertes = abs(sum(r.get("pnl_usd") or 0 for r in positions
                         if (r.get("pnl_usd") or 0) <= 0))
        interval = pnl_per_trade_interval(positions)

        entete = [
            "📊 <b>Rapport de flotte</b>",
            f"{len(positions)} trades · {len(gagnants)} gagnants · "
            f"WR {100 * len(gagnants) / len(positions):.1f}%",
            f"P&L {total:+.2f} $ · PF "
            f"{(sum(r['pnl_usd'] for r in gagnants) / pertes) if pertes else 0:.2f}",
        ]
        if interval is not None:
            # PAS `interval.conclusive` ICI : son seuil de 12 « points » est
            # calibré pour des PROPORTIONS, et `pnl_per_trade_interval` rend des
            # DOLLARS. Un intervalle de 4 $ de large y passerait pour concluant
            # par simple collision d'unités.
            #
            # Le seul verdict qui a un sens sur un P&L est le SIGNE : tant que
            # l'intervalle contient zéro, gagner et perdre restent tous deux
            # compatibles avec les données. C'est aussi le critère de
            # `live_mode_allowed`.
            tranche = (
                "perte démontrée" if interval.high < 0
                else "gain démontré" if interval.low > 0
                else "ne conclut pas sur le signe"
            )
            entete.append(
                f"P&L/trade {interval.value:+.2f} $ "
                f"IC95 [{interval.low:+.2f} .. {interval.high:+.2f}] — {tranche}"
            )
        return self._send("\n".join(entete + lignes), "report")

    # ------------------------------------------------------------------ tick

    def tick(self, arms: Sequence[Any], now: Optional[float] = None) -> None:
        """À appeler une fois par cycle. Ne lève jamais.

        Regroupe les deux échéances pour que la boucle n'ait qu'un appel à
        faire, et absorbe toute panne : un rapport manqué ne vaut pas un cycle
        perdu.
        """
        try:
            self.flush_batch(now)
            self.report_periodic(arms, now)
        except Exception as exc:  # noqa: BLE001
            print(f"[Reporter] tick impossible : {exc}")
