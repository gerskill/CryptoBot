"""Coupure Internet générale ou token mort ? (préparé le 2026-11-22, B1 bis)

POURQUOI. Du 20/11 04:07 au 21/11 10:19 (Zurich), le Mac Mini a perdu Internet pendant ~30 h.
Aucune position n'avait de prix. La règle B1 (« sans prix après time stop + 30 min = fermée à
-100 % ») ne sait pas distinguer un token mort d'une panne générale : pendant une coupure, elle
fermerait TOUTES les positions à -100 %, y compris les saines (mihao : -5,63 $ en vrai, -67 $
avec B1).

LA RÈGLE.
- Si au moins une position a un prix pendant ce tick : le réseau marche, une position sans prix
  est un token mort -> B1 s'applique comme avant.
- Si AUCUNE position n'a de prix : on sonde (au plus une fois par minute) la résolution DNS des
  sources et un prix de référence (SOL). Si la sonde échoue : PANNE GÉNÉRALE. On ne ferme rien,
  on garde les positions ; au premier vrai prix, les sorties normales (stop, time stop...) jouent.
- Si la sonde réussit alors qu'aucune position n'a de prix : ce sont bien les tokens qui sont
  morts -> B1 s'applique.

Aucun I/O ici hormis la sonde injectée : testable hors réseau.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable, Iterable

# Une sonde au plus toutes les 60 s pendant un trou de prix : quasi gratuit.
SONDE_INTERVALLE_SECONDES = 60.0
HOTES_SOURCES = ("api.dexscreener.com", "api.jup.ag")


def dns_repond(hotes: Iterable[str] = HOTES_SOURCES) -> bool:
    """Vrai si au moins un hôte des sources se résout. Pendant la coupure du 20/11, tous
    échouaient (« nodename nor servname provided »)."""
    for hote in hotes:
        try:
            socket.getaddrinfo(hote, 443)
            return True
        except OSError:
            continue
    return False


class DetecteurPanne:
    """État « panne générale » partagé par la boucle et le fil de surveillance.

    `sonde()` -> True si le réseau ET une source de prix répondent. `None` = aucune sonde
    possible (bancs de test sans réseau) : jamais de panne déclarée, comportement d'avant.
    """

    def __init__(
        self,
        sonde: Callable[[], bool] | None = None,
        horloge: Callable[[], float] = time.time,
        intervalle: float = SONDE_INTERVALLE_SECONDES,
    ) -> None:
        self._sonde = sonde
        self._horloge = horloge
        self._intervalle = intervalle
        self._verrou = threading.Lock()
        self.en_panne = False
        self.panne_depuis: float | None = None
        self._derniere_sonde_at: float | None = None
        self._derniere_sonde_ok = True

    def _sonder(self, maintenant: float) -> bool:
        if self._sonde is None:
            return True
        if (
            self._derniere_sonde_at is not None
            and maintenant - self._derniere_sonde_at < self._intervalle
        ):
            return self._derniere_sonde_ok
        try:
            ok = bool(self._sonde())
        except Exception:  # une sonde qui plante = réseau douteux
            ok = False
        self._derniere_sonde_at = maintenant
        self._derniere_sonde_ok = ok
        return ok

    def observer_tick(self, prix_ok: int, prix_manquants: int) -> str | None:
        """À appeler une fois par tick de surveillance, avec le nombre de PAIRES avec et sans
        prix. Retourne "debut" ou "fin" au changement d'état (pour prévenir), sinon None."""
        with self._verrou:
            maintenant = self._horloge()
            if prix_ok > 0:
                panne = False
            elif prix_manquants <= 0:
                panne = self.en_panne  # rien d'ouvert : on ne conclut rien
            else:
                panne = not self._sonder(maintenant)
            if panne and not self.en_panne:
                self.en_panne, self.panne_depuis = True, maintenant
                return "debut"
            if not panne and self.en_panne:
                self.en_panne = False
                self._derniere_sonde_at = None
                return "fin"
            return None

    def duree_minutes(self) -> float:
        with self._verrou:
            if not self.en_panne or self.panne_depuis is None:
                return 0.0
            return (self._horloge() - self.panne_depuis) / 60
