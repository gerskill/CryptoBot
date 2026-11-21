"""Suivi des positions ouvertes restées SANS prix, pour alerter avant la fermeture zombie.

POURQUOI. OGTA est resté ouvert 31 h 47 sans que le stop à -25 % joue : plus aucune source
ne donnait de prix, donc aucune règle de sortie ne pouvait se déclencher, et rien ne le
disait. B1 ferme maintenant une telle position à -100 % au time stop + 30 min ; mais le
time stop va jusqu'à plusieurs heures, et pendant ce temps la perte est invisible. Ce
module compte depuis quand une position n'a plus de prix et dit, UNE fois, qu'elle a
franchi le seuil d'alerte.

CE QUE C'EST, ET CE QUE CE N'EST PAS. C'est une mesure en mémoire : elle ne ferme rien et ne
décide rien. Après un redémarrage du bot le compteur repart de zéro (la fermeture zombie,
elle, lit `entry_time` et tient). Le temps compté est celui des passages du moniteur sans
prix utilisable, pas l'âge de la position.
"""

import threading
import time
from collections.abc import Callable, Iterable

# Au-delà, on prévient. Deux heures : assez long pour ignorer un trou de quelques cycles
# (les API répondent mal de temps en temps), assez court pour agir avant une perte totale.
SANS_PRIX_ALERTE_MINUTES = 120


class SuiviSansPrix:
    """Depuis quand chaque position ouverte n'a plus de prix. Sûr entre fils (le moniteur de
    positions et la boucle principale appellent tous deux le même tick)."""

    def __init__(
        self,
        seuil_minutes: float = SANS_PRIX_ALERTE_MINUTES,
        horloge: Callable[[], float] = time.time,
    ) -> None:
        self.seuil_minutes = seuil_minutes
        self._horloge = horloge
        self._depuis: dict[str, float] = {}
        self._prevenues: set[str] = set()
        self._verrou = threading.Lock()

    def prix_absent(self, position_id: str) -> float | None:
        """Note un passage sans prix. Retourne la durée en minutes la PREMIÈRE fois qu'elle
        atteint le seuil (pour envoyer un seul message), sinon `None`."""
        with self._verrou:
            maintenant = self._horloge()
            debut = self._depuis.setdefault(position_id, maintenant)
            minutes = (maintenant - debut) / 60
            if minutes >= self.seuil_minutes and position_id not in self._prevenues:
                self._prevenues.add(position_id)
                return minutes
            return None

    def prix_present(self, position_id: str) -> None:
        """Un prix utilisable est revenu : le compteur repart, une prochaine coupure reprévient."""
        with self._verrou:
            self._depuis.pop(position_id, None)
            self._prevenues.discard(position_id)

    def minutes(self, position_id: str) -> float | None:
        """Minutes sans prix, ou `None` si la position a un prix (ou n'est pas suivie)."""
        with self._verrou:
            debut = self._depuis.get(position_id)
            return None if debut is None else (self._horloge() - debut) / 60

    def purge(self, ids_ouverts: Iterable[str]) -> None:
        """Oublie les positions fermées : sans ça, la mémoire grossit à chaque position."""
        ouverts = set(ids_ouverts)
        with self._verrou:
            for position_id in list(self._depuis):
                if position_id not in ouverts:
                    del self._depuis[position_id]
            self._prevenues &= ouverts
