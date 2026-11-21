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
import math
import os
import re
import time
from typing import NamedTuple, Optional

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


# Tolérance d'horloge : un `t` plus loin dans le futur que ça n'est pas crédible.
DECALAGE_MAX_S = 60


class Lecture(NamedTuple):
    commandes: list[str]  # à appliquer, dans l'ordre de dépôt
    perimees: list[str]   # ignorées parce que de plus de 15 min (commande Telegram équivalente)


def _t_valide(t: object, maintenant: float) -> bool:
    """Un horodatage crédible : nombre fini (pas un booléen), pas dans le futur."""
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t):
        return False
    return t <= maintenant + DECALAGE_MAX_S


def _perimee(contenu: dict, maintenant: float, mtime: float) -> bool:
    """Datée de plus de 15 min. `t` absent ou invalide : l'âge du fichier fait foi."""
    t = contenu.get("t")
    depuis = t if _t_valide(t, maintenant) else mtime
    return maintenant - depuis > EXPIRE_S


def _etat_fichier(chemin: str, maintenant: float) -> tuple[Optional[str], bool]:
    """(action, périmée) d'un fichier de la boîte ; action None si illisible."""
    try:
        mtime = os.stat(chemin).st_mtime
        with open(chemin, encoding="utf-8") as fh:
            contenu = json.load(fh) or {}
        return contenu.get("action"), _perimee(contenu, maintenant, mtime)
    except (OSError, ValueError, AttributeError):
        return None, False


def en_attente(data_dir: str) -> int:
    """Nombre de commandes déposées et pas encore lues par la boucle (lecture seule)."""
    try:
        return sum(1 for n in os.listdir(dossier(data_dir)) if _NOM.match(n))
    except OSError:
        return 0


def deja_en_attente(data_dir: str, action: str, maintenant: Optional[float] = None) -> Optional[str]:
    """Identifiant d'une commande `action` valide (non périmée) déjà déposée, sinon None."""
    maintenant = time.time() if maintenant is None else maintenant
    racine = dossier(data_dir)
    try:
        noms = sorted(n for n in os.listdir(racine) if _NOM.match(n))
    except OSError:
        return None
    for nom in noms:
        trouvee, perimee = _etat_fichier(os.path.join(racine, nom), maintenant)
        if trouvee == action and not perimee:
            return nom[:-len(".json")]
    return None


def lire(data_dir: str, maintenant: Optional[float] = None) -> Lecture:
    """Lit et supprime la boîte. Un fichier illisible ou inconnu est supprimé sans
    effet ; un fichier périmé aussi, mais il est SIGNALÉ dans `perimees`."""
    maintenant = time.time() if maintenant is None else maintenant
    racine = dossier(data_dir)
    try:
        noms = sorted(n for n in os.listdir(racine) if _NOM.match(n))
    except OSError:
        return Lecture([], [])
    commandes: list[str] = []
    perimees: list[str] = []
    for nom in noms:
        chemin = os.path.join(racine, nom)
        action, perimee = _etat_fichier(chemin, maintenant)
        commande = COMMANDES.get(action) if isinstance(action, str) else None
        try:
            os.remove(chemin)
        except OSError:
            continue  # pas supprimable : ne pas l'appliquer, sinon elle repasserait à chaque cycle
        if commande and perimee:
            perimees.append(commande)
        elif commande:
            commandes.append(commande)
    return Lecture(commandes, perimees)


def lire_et_vider(data_dir: str, maintenant: Optional[float] = None) -> list[str]:
    """Commandes Telegram équivalentes à appliquer (compatibilité : sans les périmées)."""
    return lire(data_dir, maintenant).commandes
