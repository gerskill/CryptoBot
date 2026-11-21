"""Compte paper courant : on repart d'un solde neuf sans effacer l'historique (2026-11-21).

POURQUOI. Après des milliers de trades, les chiffres de chaque bras traînaient tout leur passé
(baseline : -4 343 $ sur 7 000 $, plus forte baisse 62 %), mélangeant d'anciens réglages et d'anciens
bugs. Killian a demandé de repartir, pour CHAQUE bras, d'un nouveau compte paper de 100 $, en
gardant les anciens trades.

CE QUE ÇA CHANGE, ET CE QUE ÇA NE CHANGE PAS.
- Le solde, le P&L, le taux de réussite, le drawdown, les fenêtres de l'app et du dashboard ne
  comptent que les positions OUVERTES depuis `depuis`.
- La taille des positions suit d'elle-même : elle est proportionnelle au capital (capital ×
  risk_per_trade, plafonnée à 5 %). Avec 100 $ : ~3 $ par position. Les coûts fixes (priority fee)
  pèsent alors plus en %, comme ils pèseraient en réel : c'est voulu.
- Les journaux ne sont ni déplacés ni effacés. L'APPRENTISSAGE continue de lire tout l'historique.
- Une position ouverte AVANT `depuis` finit sa vie dans l'ancien compte : sa sortie est journalisée,
  mais elle ne crédite ni ne débite le nouveau solde.

Le fichier vit dans `data/` (état du bot), pas dans `config/` : il n'est ni versionné ni réécrit par
l'apprentissage. Absent = comportement d'avant (capital_total de params.json, tout le journal).
"""

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src import settings


def compte_path() -> str:
    """`data/compte.json`, à côté du journal du témoin. Lu à chaque appel pour suivre un
    `settings.TRADES_LOG_PATH` redirigé : les tests isolent leurs journaux ainsi, et un vrai compte
    du Mac ne doit jamais filtrer les trades d'un test."""
    return os.path.join(os.path.dirname(os.path.abspath(settings.TRADES_LOG_PATH)), "compte.json")


@dataclass(frozen=True)
class Compte:
    depuis: float  # epoch UTC : début du compte
    capital: float  # solde de départ de CHAQUE bras
    numero: int = 1

    def contient(self, ligne: dict[str, Any]) -> bool:
        """Une position du journal appartient-elle à ce compte ? Décidé par son ENTRÉE."""
        moment = _epoch(ligne.get("timestamp_entry"))
        return moment is not None and moment >= self.depuis


def _epoch(valeur: Any) -> float | None:
    if isinstance(valeur, (int, float)):
        return float(valeur)
    try:
        moment = datetime.fromisoformat(str(valeur))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def compte_actuel(chemin: str | None = None) -> Compte | None:
    """Le compte courant, ou `None` (fichier absent ou illisible : comportement d'avant)."""
    try:
        with open(chemin or compte_path(), encoding="utf-8") as fh:
            brut = json.load(fh)
        depuis = _epoch(brut["depuis"])
        capital = float(brut["capital"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if depuis is None or capital <= 0:
        return None
    return Compte(depuis=depuis, capital=capital, numero=int(brut.get("numero", 1) or 1))


def ouvrir_compte(capital: float, maintenant: float | None = None,
                  chemin: str | None = None) -> Compte:
    """Écrit un nouveau compte (écriture atomique) et le rend. Le numéro suit le précédent."""
    if capital <= 0:
        raise ValueError("le solde de départ doit être positif")
    chemin = chemin or compte_path()
    precedent = compte_actuel(chemin)
    maintenant = datetime.now(timezone.utc).timestamp() if maintenant is None else maintenant
    compte = Compte(depuis=maintenant, capital=float(capital),
                    numero=(precedent.numero + 1) if precedent else 2)
    os.makedirs(os.path.dirname(os.path.abspath(chemin)), exist_ok=True)
    contenu = {
        "depuis": datetime.fromtimestamp(compte.depuis, timezone.utc).isoformat(),
        "capital": compte.capital,
        "numero": compte.numero,
        "_note": "Compte paper courant de chaque bras (src/core/compte.py). Les journaux gardent tout.",
    }
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(chemin)), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(contenu, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, chemin)
    return compte
