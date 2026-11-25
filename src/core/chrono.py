"""Chronométrage des étapes d'un cycle — mesure seule, aucune décision (2026-11-25).

POURQUOI. Le point « durée du cycle » du dashboard est orange presque en permanence : mesuré sur le
Mac le 2026-11-25, 91 % des cycles dépassent le créneau de 45 s (médiane 52 s, 1 sur 10 au-delà de
123 s, pire 491 s), alors que le scan lui-même ne prend que 7 à 13 s. Le journal ne disait pas où
part le reste. Ce module additionne le temps passé dans chaque étape nommée et le résume en une
ligne ; il ne change aucun comportement du bot.

Usage :
    chrono = Chrono(horloge=time.monotonic)
    with chrono.etape("collecte"):
        ...
    print(chrono.ligne(total))
"""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

# Au-delà de ce nombre d'étapes, la ligne ne montre que les plus longues (le reste est additionné).
ETAPES_AFFICHEES = 6


class Chrono:
    def __init__(self, horloge: Callable[[], float] = time.monotonic) -> None:
        self._horloge = horloge
        self.durees: dict[str, float] = {}

    @contextmanager
    def etape(self, nom: str) -> Iterator[None]:
        """Ajoute le temps du bloc à `nom`, même si le bloc lève une exception."""
        debut = self._horloge()
        try:
            yield
        finally:
            self.durees[nom] = self.durees.get(nom, 0.0) + (self._horloge() - debut)

    def resume(self) -> dict[str, float]:
        """Durées arrondies au dixième, de la plus longue à la plus courte."""
        return {nom: round(d, 1) for nom, d in sorted(self.durees.items(), key=lambda kv: -kv[1])}

    def ligne(self, total: float, cycle: int | None = None) -> str:
        """`⏱️ étapes cycle N (total 52.0s) : entrées/sniper 20.1s | collecte 8.0s | … | hors étapes 1.2s`."""
        etapes = list(self.resume().items())
        montrees = etapes[:ETAPES_AFFICHEES]
        reste = sum(d for _, d in etapes[ETAPES_AFFICHEES:])
        morceaux = [f"{nom} {d:.1f}s" for nom, d in montrees]
        if reste:
            morceaux.append(f"{len(etapes) - ETAPES_AFFICHEES} autres {reste:.1f}s")
        hors = total - sum(self.durees.values())
        if hors >= 0.05:
            morceaux.append(f"hors étapes {hors:.1f}s")
        numero = f" cycle {cycle}" if cycle is not None else ""
        return f"⏱️ étapes{numero} (total {total:.1f}s) : " + (" | ".join(morceaux) or "aucune")
