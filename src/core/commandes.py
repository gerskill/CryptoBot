"""Boîte de commandes du bot memecoin, alimentée depuis l'iPhone (ADR 021).

INVARIANT : la boucle (`src/main.py`) est seule propriétaire de son état. Les
autres processus ne la pilotent pas directement : ils DÉPOSENT un fichier par
commande dans `data/commandes_bot/`, et la boucle les lit au début du cycle
suivant, exactement comme une commande Telegram (/STOP, /RESUME).

Écriture atomique (fichier temporaire puis `os.replace`) : la boucle ne lit
jamais un fichier à moitié écrit. Chaque fichier est supprimé après lecture :
une commande n'est appliquée qu'une fois.

Deux commandes seulement, celles de Telegram : rien ici ne peut ouvrir une
position ni toucher aux réglages d'un bras.
"""

import json
import os
import re
import time
from typing import Optional

# action de la boîte -> commande Telegram équivalente
COMMANDES = {"stop": "/STOP", "resume": "/RESUME"}
_NOM = re.compile(r"^[0-9A-Za-z_-]{1,64}\.json$")
# Une commande plus vieille que ça n'est plus appliquée : un /STOP ne doit pas
# partir des heures plus tard, au redémarrage de la boucle (ADR 021).
EXPIRE_S = 15 * 60


def dossier(data_dir: str) -> str:
    return os.path.join(data_dir, "commandes_bot")


def deposer(data_dir: str, action: str, ident: str, maintenant: Optional[float] = None) -> str:
    """Dépose une commande, datée. Lève ValueError si l'action ou l'identifiant est invalide."""
    if action not in COMMANDES:
        raise ValueError(f"action inconnue : {action}")
    nom = f"{ident}.json"
    if not _NOM.match(nom):
        raise ValueError("identifiant invalide")
    racine = dossier(data_dir)
    os.makedirs(racine, exist_ok=True)
    chemin = os.path.join(racine, nom)
    tmp = os.path.join(racine, f".{ident}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"action": action, "id": ident, "t": time.time() if maintenant is None else maintenant}, fh)
    os.replace(tmp, chemin)
    return chemin


def en_attente(data_dir: str) -> int:
    """Nombre de commandes déposées et pas encore lues par la boucle (lecture seule)."""
    try:
        return sum(1 for n in os.listdir(dossier(data_dir)) if _NOM.match(n))
    except OSError:
        return 0


def _perimee(contenu: dict, maintenant: float) -> bool:
    """Datée de plus de 15 min. Sans date (ancien format) : appliquée, comme avant."""
    t = contenu.get("t")
    return isinstance(t, (int, float)) and maintenant - t > EXPIRE_S


def lire_et_vider(data_dir: str, maintenant: Optional[float] = None) -> list[str]:
    """Commandes Telegram équivalentes, dans l'ordre de dépôt ; chaque fichier est
    supprimé. Un fichier illisible, inconnu ou périmé est supprimé sans effet."""
    maintenant = time.time() if maintenant is None else maintenant
    racine = dossier(data_dir)
    try:
        noms = sorted(n for n in os.listdir(racine) if _NOM.match(n))
    except OSError:
        return []
    commandes: list[str] = []
    for nom in noms:
        chemin = os.path.join(racine, nom)
        commande: Optional[str] = None
        try:
            with open(chemin, encoding="utf-8") as fh:
                contenu = json.load(fh) or {}
            if not _perimee(contenu, maintenant):
                commande = COMMANDES.get(contenu.get("action"))
        except (OSError, ValueError, AttributeError):
            commande = None
        try:
            os.remove(chemin)
        except OSError:
            continue  # pas supprimable : ne pas l'appliquer, sinon elle repasserait à chaque cycle
        if commande:
            commandes.append(commande)
    return commandes
