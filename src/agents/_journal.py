"""Écriture journalisée commune aux agents de mesure.

POURQUOI CE MODULE EXISTE PLUTÔT QU'UN `open(..., "a")` DANS CHAQUE AGENT.
Le 2026-08-02, `data/funnel_log.jsonl` avait atteint 11 Mo et 61 922 lignes
sans aucune rotation, et il était relu EN ENTIER à chaque ajustement de
paramètre. Cinq agents qui journalisent à chaque cycle reproduiraient ce
défaut cinq fois.

Ici la rotation est acquise dès la première ligne écrite, et la lecture est
bornée par défaut.
"""

import json
import os
from typing import Any, Optional

from src.core.funnel import read_funnel, rotate

# Plus petit que l'entonnoir : ces journaux portent une ligne par token jugé,
# pas une par porte et par bras. 4 Mo ≈ 20 000 lignes, largement au-delà de ce
# qu'une analyse rétrospective réclame.
AGENT_LOG_MAX_BYTES = 4 * 1024 * 1024


def append(path: str, row: dict[str, Any]) -> None:
    """Ajoute une ligne et fait tourner le fichier s'il déborde.

    L'écriture est en `a` : deux agents n'écrivent jamais dans le même fichier,
    et une ligne JSON tient dans un `write` unique, donc pas de verrou.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    rotate(path, max_bytes=AGENT_LOG_MAX_BYTES)


def read(path: str, tail: Optional[int] = 5_000) -> list[dict[str, Any]]:
    """Relit un journal d'agent, génération précédente comprise.

    `tail` est borné PAR DÉFAUT, à l'inverse de `read_funnel` : aucun appelant
    d'un journal d'agent n'a besoin de tout l'historique en mémoire, et le
    défaut permissif est précisément ce qui avait laissé l'entonnoir déraper.
    """
    return read_funnel(path, tail=tail)
