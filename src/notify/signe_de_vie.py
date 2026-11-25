"""Signe de vie vu de l'extérieur (préparé le 2026-11-22). DÉSACTIVÉ tant qu'aucune URL.

POURQUOI. Quand le Mac perd Internet (20-21/11, 30 h), ni Telegram ni l'app iPhone ne peuvent
prévenir : ils passent par le même réseau. La solution est inversée : le bot « pointe » toutes
les 5 min auprès d'un service gratuit extérieur (type healthchecks.io). Si le pointage n'arrive
plus (Mac éteint, sans réseau, bot planté), C'EST LE SERVICE qui envoie l'alerte (e-mail,
Telegram...).

L'URL de pointage est personnelle (quiconque la connaît peut pointer à la place du bot) : elle
est lue dans `.env` (SIGNE_DE_VIE_URL) ou dans params.json (`monitoring.signe_de_vie_url`), et
n'est JAMAIS affichée — les messages ne citent que le nom du site.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

INTERVALLE_DEFAUT_SECONDES = 300.0
DELAI_REQUETE_SECONDES = 5.0


def url_configuree(params: Any = None) -> str | None:
    """`.env` d'abord, puis params.json. Vide ou absent = désactivé."""
    url = (os.getenv("SIGNE_DE_VIE_URL") or "").strip()
    if not url and params is not None:
        try:
            url = str(params.get("monitoring.signe_de_vie_url", "") or "").strip()
        except Exception:
            url = ""
    if not url.startswith("https://"):
        return None
    return url


class SigneDeVie:
    def __init__(
        self,
        url: str | None,
        envoyer: Callable[[str, float], Any] | None = None,
        horloge: Callable[[], float] = time.time,
        intervalle: float = INTERVALLE_DEFAUT_SECONDES,
        en_fond: bool = True,
    ) -> None:
        self._url = url
        self._envoyer = envoyer or _envoi_requests
        self._horloge = horloge
        self.intervalle = intervalle
        self._en_fond = en_fond
        self._dernier_at: float | None = None
        self._verrou = threading.Lock()
        self.envois = 0
        self.echecs = 0

    @property
    def actif(self) -> bool:
        return bool(self._url)

    def description(self) -> str:
        """Pour les logs : le nom du site seulement, jamais l'URL."""
        if not self._url:
            return "signe de vie : désactivé (aucune URL configurée)"
        return f"signe de vie : actif vers {urlparse(self._url).hostname}, toutes les {self.intervalle / 60:.0f} min"

    def pointer(self, force: bool = False) -> bool:
        """Envoie le signe de vie si l'intervalle est écoulé. Ne lève jamais, ne bloque pas la
        boucle (envoi en fil séparé par défaut). Retourne True si un envoi a été lancé."""
        if not self._url:
            return False
        with self._verrou:
            maintenant = self._horloge()
            if not force and self._dernier_at is not None and maintenant - self._dernier_at < self.intervalle:
                return False
            self._dernier_at = maintenant
        if self._en_fond:
            threading.Thread(target=self._envoi_sur, daemon=True, name="signe-de-vie").start()
        else:
            self._envoi_sur()
        return True

    def _envoi_sur(self) -> None:
        try:
            self._envoyer(self._url, DELAI_REQUETE_SECONDES)
            self.envois += 1
        except Exception as exc:  # l'URL n'apparaît jamais dans le message
            self.echecs += 1
            print(f"[SigneDeVie] envoi impossible ({type(exc).__name__})")


def _envoi_requests(url: str, delai: float) -> None:
    import requests

    reponse = requests.get(url, timeout=delai)
    reponse.raise_for_status()
