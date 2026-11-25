"""Masque les secrets dans tout ce que le bot écrit (préparé le 2026-11-22).

POURQUOI. Les erreurs réseau de `requests` recopient l'URL appelée. Pour Telegram, l'URL
contient le jeton du bot : `https://api.telegram.org/bot<jeton>/getUpdates`. Pendant la coupure
du 20-21/11, ce jeton a été écrit en clair ~2 300 fois dans logs/bot.log.

DEUX PROTECTIONS.
- `masquer(texte)` remplace tout jeton Telegram (forme `<chiffres>:<35 caractères>`) précédé
  de `bot`, et toute valeur secrète connue (jeton, URL du signe de vie), par un masque.
- `installer_filtre_sorties()` branche ce masque sur stdout/stderr : tout `print` et toute
  trace d'erreur passent par lui avant d'arriver dans le journal.
"""

from __future__ import annotations

import re
import sys
import threading
from collections.abc import Iterable

MASQUE = "<jeton-masque>"
# bot + identifiant numérique + « : » + secret (35 caractères chez Telegram ; ≥ 30 pour la marge).
MOTIF_JETON_TELEGRAM = re.compile(r"bot\d{5,15}:[A-Za-z0-9_-]{30,}")

_secrets_connus: list[str] = []
_verrou = threading.Lock()


def enregistrer_secrets(valeurs: Iterable[str | None]) -> None:
    """Valeurs à masquer telles quelles où qu'elles apparaissent (jamais affichées)."""
    with _verrou:
        for valeur in valeurs:
            if valeur and len(valeur) >= 8 and valeur not in _secrets_connus:
                _secrets_connus.append(valeur)
        _secrets_connus.sort(key=len, reverse=True)


def masquer(texte: str) -> str:
    if not texte:
        return texte
    texte = MOTIF_JETON_TELEGRAM.sub("bot" + MASQUE, texte)
    for valeur in _secrets_connus:
        if valeur in texte:
            texte = texte.replace(valeur, MASQUE)
    return texte


class FluxMasque:
    """Enveloppe d'un flux texte : même interface, contenu masqué."""

    def __init__(self, flux) -> None:
        self._flux = flux

    def write(self, texte):
        try:
            texte = masquer(texte)
        except Exception:
            pass
        return self._flux.write(texte)

    def writelines(self, lignes):
        for ligne in lignes:
            self.write(ligne)

    def __getattr__(self, nom):
        return getattr(self._flux, nom)


def installer_filtre_sorties() -> None:
    """Idempotent : n'enveloppe stdout/stderr qu'une fois."""
    if not isinstance(sys.stdout, FluxMasque):
        sys.stdout = FluxMasque(sys.stdout)
    if not isinstance(sys.stderr, FluxMasque):
        sys.stderr = FluxMasque(sys.stderr)
