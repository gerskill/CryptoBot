"""Verrou d'instance unique.

Deux boucles lancées en parallèle écrivent le même `state.json`, le même
journal et le même cache : les lectures deviennent incohérentes, la
consommation d'API double, et deux positions peuvent s'ouvrir sur le même
token sans que `can_open()` le voie — chaque process a son propre portefeuille.

Le verrou est un fichier contenant le PID. Un verrou dont le process n'existe
plus est considéré périmé et repris (cas du kill -9 ou du crash).
"""

import atexit
import os
from typing import Optional


class InstanceLock:
    def __init__(self, path: str):
        self.path = path
        self.acquired = False

    def acquire(self) -> Optional[int]:
        """None si le verrou est pris. Sinon acquiert et retourne None."""
        existing = self._read_pid()
        if existing is not None and self._is_alive(existing):
            return existing

        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        self.acquired = True
        atexit.register(self.release)
        return None

    def release(self) -> None:
        if not self.acquired:
            return
        # Ne supprimer que SON propre verrou : un process qui a repris un
        # verrou périmé ne doit pas effacer celui de son successeur.
        if self._read_pid() == os.getpid():
            try:
                os.unlink(self.path)
            except OSError:
                pass
        self.acquired = False

    def _read_pid(self) -> Optional[int]:
        try:
            with open(self.path, encoding="utf-8") as fh:
                return int(fh.read().strip())
        except (OSError, ValueError):
            return None

    @staticmethod
    def _is_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)  # signal 0 : teste l'existence sans rien envoyer
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # process d'un autre utilisateur : il existe
