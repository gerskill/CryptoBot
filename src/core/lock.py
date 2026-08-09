"""Verrou d'instance unique.

Deux boucles lancées en parallèle écrivent le même `state.json`, le même
journal et le même cache : les lectures deviennent incohérentes, la
consommation d'API double, et deux positions peuvent s'ouvrir sur le même
token sans que `can_open()` le voie — chaque process a son propre portefeuille.

Le verrou utilise `flock` (verrou noyau, atomique) sur un fichier contenant
le PID. Contrairement à un verrou "lire le PID puis écrire" (TOCTOU : deux
process peuvent passer le check en même temps), `flock` ne peut être détenu
que par un seul process à la fois — le noyau tranche, pas nous. Un verrou
dont le process est mort (kill -9, crash) est automatiquement libéré par le
noyau à la sortie du process, donc plus besoin de vérifier la vivacité du
PID à la main.
"""

import atexit
import fcntl
import os
from typing import Optional


class InstanceLock:
    def __init__(self, path: str):
        self.path = path
        self.acquired = False
        self._fd: Optional[int] = None

    def acquire(self) -> Optional[int]:
        """None si le verrou est pris. Sinon acquiert et retourne None."""
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Verrou deja detenu par un autre process (vivant, forcement :
            # le noyau l'aurait libere sinon). On lit le PID pour le message.
            holder = self._read_fd_pid(fd)
            os.close(fd)
            return holder if holder is not None else -1

        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.fsync(fd)
        self._fd = fd
        self.acquired = True
        atexit.register(self.release)
        return None

    def release(self) -> None:
        if not self.acquired or self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            os.unlink(self.path)
        except OSError:
            pass
        self._fd = None
        self.acquired = False

    @staticmethod
    def _read_fd_pid(fd: int) -> Optional[int]:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            data = os.read(fd, 32).decode("utf-8").strip()
            return int(data) if data else None
        except (OSError, ValueError):
            return None
