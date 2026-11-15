#!/usr/bin/env python3
"""Observation GMGN séparée. Ne lance pas la boucle et ne la modifie pas.

Aucun appel tant que le cooldown (incident du 8 novembre 2026, 17:27:31
Zurich, ou un ban plus récent) n'est pas passé. Le CLI est lancé sans
shell, sans lire ni transmettre de clé : il charge sa propre config.

Usage, depuis la racine du dépôt, processus déjà lancés laissés tels quels :

  python3 scripts/gmgn_observe.py --once
  python3 scripts/gmgn_observe.py --interval 60

Arrêt : Ctrl-C. Les fichiers sous data/gmgn_observe/ restent.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.gmgn_observe import (  # noqa: E402
    COMMAND_TIMEOUT_SECONDS,
    GmgnObserver,
    child_env,
    read_state,
)

_RUNNING = True


def _stop(_signum, _frame) -> None:
    global _RUNNING
    _RUNNING = False


def resolve_cli() -> str:
    found = shutil.which("gmgn-cli")
    if not found:
        raise SystemExit("gmgn-cli introuvable dans PATH — observation non lancée")
    path = os.path.realpath(found)
    if os.path.basename(path) != "gmgn-cli" or not os.access(path, os.X_OK):
        raise SystemExit("exécutable gmgn-cli refusé")
    return path


def make_runner(executable: str):
    def run(argv: list[str]) -> dict[str, object]:
        if argv[0] != executable:
            raise RuntimeError("argv inattendu")
        try:
            completed = subprocess.run(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
                env=child_env(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("gmgn-cli timeout") from exc
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout or "",
            "stderr": completed.stderr or "",
        }

    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Observation GMGN après sélection (PAPER, sans décision)")
    parser.add_argument("--root", default="")
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    observer = GmgnObserver(root)
    while _RUNNING:
        source = os.path.join(root, "data", "state.json")
        document, age = read_state(source)
        # Pendant le cooldown, on ne résout même pas le binaire : aucun spawn.
        if not observer.in_cooldown() and observer.runner is None:
            path = resolve_cli()
            observer.executable = path
            observer.runner = make_runner(path)
        heart = observer.poll(document, age)
        print(
            f"[gmgn_observe] status={heart['status']} appels={heart['calls']} "
            f"ok={heart['responses_ok']} erreurs={heart['responses_error']}"
        )
        if args.once or not _RUNNING:
            break
        deadline = time_monotonic() + max(5.0, args.interval)
        while _RUNNING and time_monotonic() < deadline:
            time_sleep(min(1.0, deadline - time_monotonic()))
    return 0


def time_monotonic() -> float:
    import time
    return time.monotonic()


def time_sleep(seconds: float) -> None:
    import time
    time.sleep(seconds)


if __name__ == "__main__":
    sys.exit(main())
