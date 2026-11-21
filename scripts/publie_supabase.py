#!/usr/bin/env python3
"""Publication Supabase : les résumés du bot pour l'app iPhone, et ses commandes.

CE QUE ÇA FAIT. Toutes les 2 minutes (launchd), construit huit résumés à partir
des fonctions existantes de `api/server.py` (appelées directement, sans HTTP) et
les enregistre dans la table `snapshots` de Supabase. L'app iPhone lit cette table.

COMMANDES (ADR 021). Avant de publier, relit la table `commandes` (demandes
déposées depuis l'iPhone), et pour chacune « en attente » : la VALIDE avec les
mêmes règles que le site, l'applique, puis écrit « appliquée » ou « refusée »
avec la raison. Quatre actions, toutes PAPIER : `stop` et `resume` (déposés dans
la boîte de la boucle, comme /STOP et /RESUME sur Telegram), `pm_toggle` et
`pm_fork` (déposés dans la boîte de la simulation Liquid, comme depuis le site).
Une demande de plus de 15 min est refusée : un arrêt d'urgence ne doit pas
partir des heures plus tard au réveil du Mac. Aucune action ne peut passer
d'ordre réel : le bot n'a pas de module d'exécution.

CE QUI SORT, ET CE QUI NE SORT JAMAIS. Chaque clé a une LISTE BLANCHE de champs
(le contrat du plan `docs/superpowers/plans/2026-11-18-app-iphone.md`). Ce qui
n'y figure pas ne part pas : ni stop loss, ni niveaux de TP, ni adresse de
token, ni réglages de bras, ni l'exemple fictif Liquid. Un test le verrouille.

CONNEXION. Le compte de Killian (jamais de clé `service_role`). Le mot de passe
sert uniquement quand aucune session n'est valide ; ensuite c'est le jeton de
rafraîchissement, gardé dans `data/supabase_session.json` (droits 600, dossier
ignoré par git). Aucun message d'erreur ne cite le mot de passe, la clé ni le
corps d'une réponse.

SORTIES. Variables `.env` absentes : message clair et code 0 (un service launchd
qui sort en erreur est relancé en boucle). Supabase injoignable ou refus : code 1.

Usage :
  python scripts/publie_supabase.py                # un passage (launchd, toutes les 2 min)
  python scripts/publie_supabase.py --sans-envoi   # affiche ce qui serait publié, sans réseau
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from contextlib import redirect_stdout  # noqa: E402

from src import settings
from src.core import sante_donnees  # noqa: E402
from src.core.arm import load_manifest  # noqa: E402
from src.core.sans_prix import SANS_PRIX_ALERTE_MINUTES  # noqa: E402
from src.core import commandes as boite_bot  # noqa: E402

# api/server.py affiche un avertissement à l'import (jeton du site absent) :
# sur stderr, pour que `--sans-envoi` reste un JSON propre sur stdout.
with redirect_stdout(sys.stderr):
    import api.server as server  # noqa: E402

VARIABLES = ("SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_EMAIL", "SUPABASE_PASSWORD")
SESSION_PATH = os.path.join(settings.BASE_DIR, "data", "supabase_session.json")
VEILLE_ETAT_PATH = os.path.join(settings.BASE_DIR, "data", "veille_etat.json")
DELAI_S = 20
FENETRES = (("perf_7d", "7d"), ("perf_30d", "30d"), ("perf_all", "all"))
TYPES_ALERTE = ("ZOMBIE", "PANIC_STOP")
COMMANDES_DATA_DIR = os.path.join(settings.BASE_DIR, "data")
COMMANDE_EXPIRE_S = 15 * 60
COMMANDES_PAR_PASSAGE = 20
ACTIONS_PM = {"pm_toggle": "toggle", "pm_fork": "fork"}
ALERTES_MAX = 20
ALERTES_HEURES = 24


class ErreurPublication(Exception):
    """Message sûr à afficher : jamais de mot de passe, de clé ni de corps de réponse."""


# --- Construction des résumés (pur : aucun réseau) -------------------------

def _iso(epoch_ou_texte) -> "str | None":
    """ISO 8601 UTC d'un epoch ou d'un texte ISO ; `None` si absent ou illisible."""
    if epoch_ou_texte is None:
        return None
    try:
        moment = (datetime.fromtimestamp(float(epoch_ou_texte), tz=timezone.utc)
                  if isinstance(epoch_ou_texte, (int, float))
                  else server._parse_ts(str(epoch_ou_texte)))
    except (ValueError, OverflowError, OSError):
        return None
    return moment.astimezone(timezone.utc).isoformat() if moment else None


def _etat(maintenant: datetime, resume_liquid: dict) -> dict:
    etat = server._state()
    return {
        "bot": {"dernier_cycle": _iso(etat.get("updated_at")), "mode": "PAPER",
                "en_pause": bool(etat.get("paused"))},
        "liquid": {"dernier_cycle": _iso(resume_liquid.get("updated_at_iso"))},
    }


def _positions() -> dict:
    lignes = []
    for p in server._state().get("positions") or []:
        lignes.append({
            "bras": p.get("arm"),
            "symbole": p.get("symbol"),
            "pnl_pct": p.get("pnl_pct"),
            "taille_usd": p.get("size_usd"),
            "duree_min": p.get("duration_min"),
            "tp1_atteint": bool(p.get("tp1_hit")),
        })
    return {"lignes": lignes}


def _perf(fenetre: str) -> dict:
    brut = server.get_arms_performance(window=fenetre)
    bras = []
    for a in brut.get("arms", []):
        intervalle = a.get("win_rate_interval") or {}
        verdict = a.get("verdict")
        bras.append({
            "bras": a.get("arm"),
            "trades": a.get("trades"),
            "win_rate": a.get("win_rate"),
            "ic_bas": intervalle.get("low"),
            "ic_haut": intervalle.get("high"),
            "profit_factor": a.get("profit_factor"),
            "pnl_usd": a.get("pnl_usd"),
            "max_drawdown_pct": a.get("max_drawdown_pct"),
            "verdict": verdict.get("verdict") if isinstance(verdict, dict) else verdict,
        })
    flotte = brut.get("fleet") or {}
    return {
        "fenetre": fenetre,
        "capital_par_bras": brut.get("starting_capital_per_arm"),
        "bras": bras,
        "flotte": {k: flotte.get(k) for k in ("trades", "win_rate", "pnl_usd", "profit_factor")},
    }


def _resume_liquid() -> dict:
    """Résumé Liquid RÉEL, ou `{}` s'il est illisible. L'exemple fictif est écarté."""
    try:
        resume = server.get_prediction_summary(demo=False)
    except Exception:  # noqa: BLE001 — config absente (HTTPException) ou illisible
        return {}
    return {} if resume.get("exemple_fictif") else resume


def _liquid(resume: dict) -> dict:
    bras = []
    for a in resume.get("arms", []):
        bras.append({
            "bras": a.get("arm"),
            "famille": a.get("famille"),
            "role": a.get("role"),
            "actif": bool(a.get("enabled", True)),
            "ouverts": a.get("ouverts"),
            "solde_usd": (a.get("compte") or {}).get("solde"),
            "regles": (a.get("stats") or {}).get("paris_regles"),
            "gagnes_net": (a.get("stats") or {}).get("gagnes_net"),
        })
    return {"bras": bras}


def _alertes(maintenant: datetime) -> dict:
    limite = maintenant - timedelta(hours=ALERTES_HEURES)
    lignes: list[tuple[datetime, dict]] = []
    pannes: list[tuple[datetime, dict]] = []
    try:
        sorties = server._collect_positions("all")
    except Exception:  # noqa: BLE001 — un journal illisible ne doit pas taire les pannes
        sorties = []
    for p in sorties:
        raison = str(p.get("exit_reason") or "")
        type_ = next((t for t in TYPES_ALERTE if raison.startswith(t)), None)
        quand = server._parse_ts(p.get("timestamp_exit"))
        if type_ is None or quand is None or quand < limite:
            continue
        lignes.append((quand, {
            "quand": quand.astimezone(timezone.utc).isoformat(),
            "type": type_,
            "bras": p.get("arm"),
            "texte": f"{p.get('token') or '?'} : sortie {raison}",
        }))
    try:
        with open(VEILLE_ETAT_PATH, encoding="utf-8") as fh:
            veille = json.load(fh)
    except (OSError, ValueError):
        veille = {}
    for nom, etat in (veille.items() if isinstance(veille, dict) else ()):
        if not isinstance(etat, dict) or etat.get("ok") is not False:
            continue
        depuis = server._parse_ts(etat.get("depuis"))
        if depuis is None:
            continue
        minutes = max(0, int((maintenant - depuis).total_seconds() // 60))
        pannes.append((depuis, {
            "quand": depuis.astimezone(timezone.utc).isoformat(),
            "type": "PANNE",
            "bras": None,
            "texte": f"{nom} en panne depuis {minutes} min"
                     + (f" : {etat['detail']}" if isinstance(etat.get("detail"), str) and etat["detail"] else ""),
        }))
    # Positions ouvertes sans prix depuis 2 h ou plus : une panne en cours, pas une sortie.
    # `sans_prix_depuis_min` est écrit par le bot (src/core/sans_prix.py) ; absent = pas d'alerte.
    for p in server._state().get("positions") or []:
        minutes = p.get("sans_prix_depuis_min")
        if not isinstance(minutes, (int, float)) or minutes < SANS_PRIX_ALERTE_MINUTES:
            continue
        depuis = maintenant - timedelta(minutes=minutes)
        pannes.append((depuis, {
            "quand": depuis.astimezone(timezone.utc).isoformat(),
            "type": "SANS_PRIX",
            "bras": p.get("arm"),
            "texte": f"{p.get('symbol') or '?'} : aucun prix depuis {int(minutes)} min",
        }))
    # Les pannes EN COURS d'abord : vingt sorties récentes ne doivent pas
    # chasser du haut de la liste un service toujours à l'arrêt.
    pannes.sort(key=lambda t: t[0], reverse=True)
    lignes.sort(key=lambda t: t[0], reverse=True)
    return {"lignes": [ligne for _, ligne in (pannes + lignes)[:ALERTES_MAX]]}


def _donnees(maintenant: datetime) -> dict:
    """Santé des données : sources en direct (state.json) + entrées des 24 h (journaux).
    Voir src/core/sante_donnees.py ; rien de stratégique n'en sort."""
    bras = [e["name"] for e in load_manifest() if e.get("enabled", True)]
    entrees = sante_donnees.charger_entrees(
        maintenant, bras, lambda nom: settings.arm_paths(nom)["trades"])
    return sante_donnees.resume(server._state(), entrees)


def _vide(cle: str) -> dict:
    """Résumé « aucune donnée » : même forme que le contrat, sans contenu."""
    if cle == "etat":
        return {"bot": {"dernier_cycle": None, "mode": "PAPER", "en_pause": False},
                "liquid": {"dernier_cycle": None}}
    if cle == "positions" or cle == "alertes":
        return {"lignes": []}
    if cle == "liquid":
        return {"bras": []}
    if cle == "donnees":
        return {"fenetre_h": sante_donnees.FENETRE_HEURES, "entrees": 0, "sources": [], "champs_vides": []}
    fenetre = dict(FENETRES)[cle]
    return {"fenetre": fenetre, "capital_par_bras": None, "bras": [],
            "flotte": {"trades": 0, "win_rate": None, "pnl_usd": None, "profit_factor": None}}


def construire_snapshots(maintenant: datetime) -> dict:
    """Les huit résumés du contrat. Un constructeur qui échoue donne son résumé
    vide sans faire tomber les autres : `data/` vide = « aucune donnée »."""
    maintenant = maintenant.astimezone(timezone.utc)
    resume_liquid = _resume_liquid()
    constructeurs = {
        "etat": lambda: _etat(maintenant, resume_liquid),
        "positions": _positions,
        "liquid": lambda: _liquid(resume_liquid),
        "alertes": lambda: _alertes(maintenant),
        "donnees": lambda: _donnees(maintenant),
    }
    for cle, fenetre in FENETRES:
        constructeurs[cle] = (lambda f=fenetre: _perf(f))
    snaps = {}
    for cle, construire in constructeurs.items():
        try:
            corps = construire()
        except Exception:  # noqa: BLE001
            corps = _vide(cle)
        snaps[cle] = {"publie_a": maintenant.isoformat(), **corps}
    return snaps


# --- Session et envoi -------------------------------------------------------

def _lire_env() -> "dict | None":
    env = {nom: (os.environ.get(nom) or "").strip() for nom in VARIABLES}
    return env if all(env.values()) else None


def _post(url: str, **kwargs):
    """`requests.post` sans jamais laisser sortir le texte d'une exception :
    celle de `requests` peut citer l'URL ou le corps."""
    try:
        return requests.post(url, timeout=DELAI_S, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise ErreurPublication(f"Supabase injoignable ({type(exc).__name__})") from None


def _chemin_tmp(chemin: str) -> str:
    """Temporaire de la session : se termine par `.json` pour tomber sous
    `data/*.json` du .gitignore (dépôt public : un jeton ne doit jamais être
    ajouté par un `git add -A` après un plantage entre écriture et remplacement)."""
    racine, extension = os.path.splitext(chemin)
    return f"{racine}.tmp{extension}"


class Session:
    """Jeton d'accès Supabase du compte de Killian."""

    def __init__(self, env: dict):
        self.env = env
        self.acces: "str | None" = None

    def _entetes_auth(self) -> dict:
        return {"apikey": self.env["SUPABASE_ANON_KEY"], "Content-Type": "application/json"}

    def _jeton(self, grant: str, corps: dict) -> bool:
        reponse = _post(f"{self.env['SUPABASE_URL'].rstrip('/')}/auth/v1/token?grant_type={grant}",
                        headers=self._entetes_auth(), json=corps)
        if reponse.status_code != 200:
            return False
        try:
            donnees = reponse.json()
            self.acces = donnees["access_token"]
            self._garder(donnees.get("refresh_token"))
        except (ValueError, KeyError, TypeError):
            return False
        return True

    @staticmethod
    def _garder(refresh) -> None:
        """Écrit le jeton de rafraîchissement (droits 600, jamais le mot de passe).
        Échec d'écriture : on publie quand même (prochain passage = mot de passe)."""
        if not refresh:
            return
        tmp = _chemin_tmp(SESSION_PATH)
        try:
            os.makedirs(os.path.dirname(SESSION_PATH), exist_ok=True)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"refresh_token": refresh}, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, SESSION_PATH)
        except OSError as exc:
            print(f"Session Supabase non enregistrée ({type(exc).__name__}) : publication poursuivie.",
                  file=sys.stderr)
            try:
                os.remove(tmp)
            except OSError:
                pass

    def _refresh_gardé(self) -> "str | None":
        try:
            with open(SESSION_PATH, encoding="utf-8") as fh:
                return json.load(fh).get("refresh_token") or None
        except (OSError, ValueError, AttributeError):
            return None

    def mot_de_passe(self) -> None:
        if not self._jeton("password", {"email": self.env["SUPABASE_EMAIL"],
                                        "password": self.env["SUPABASE_PASSWORD"]}):
            raise ErreurPublication("Connexion Supabase refusée : vérifier SUPABASE_EMAIL / SUPABASE_PASSWORD")

    def ouvrir(self) -> None:
        refresh = self._refresh_gardé()
        if refresh and self._jeton("refresh_token", {"refresh_token": refresh}):
            return
        self.mot_de_passe()

    def entetes(self) -> dict:
        if self.acces is None:
            self.ouvrir()
        return {**self._entetes_auth(), "Authorization": f"Bearer {self.acces}",
                "Prefer": "resolution=merge-duplicates,return=minimal"}


def publier(snapshots: dict, session: Session) -> None:
    """Un upsert par passage (une ligne par clé). Un 401 déclenche UNE reconnexion
    par mot de passe ; tout autre refus est une erreur."""
    adresse = f"{session.env['SUPABASE_URL'].rstrip('/')}/rest/v1/snapshots?on_conflict=user_id,key"
    publie_a = next(iter(snapshots.values()))["publie_a"] if snapshots else None
    lignes = [{"key": cle, "value": valeur, "published_at": publie_a} for cle, valeur in snapshots.items()]
    for essai in (1, 2):
        reponse = _post(adresse, headers=session.entetes(), json=lignes)
        if reponse.status_code in (200, 201, 204):
            return
        if reponse.status_code == 401 and essai == 1:
            session.mot_de_passe()
            continue
        break
    raise ErreurPublication(f"Supabase a refusé la publication (HTTP {reponse.status_code})")


# --- Commandes depuis l'iPhone (ADR 021) -------------------------------------

def _requete(methode: str, url: str, **kwargs):
    """Comme `_post`, pour GET et PATCH : jamais le texte d'une exception."""
    try:
        return requests.request(methode, url, timeout=DELAI_S, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise ErreurPublication(f"Supabase injoignable ({type(exc).__name__})") from None


def appliquer_commande(ligne: dict, maintenant: datetime) -> tuple[str, str]:
    """(statut, détail) pour UNE demande. Pur côté réseau : n'écrit que dans
    les boîtes locales (boucle memecoin ou simulation Liquid)."""
    action = ligne.get("action")
    payload = ligne.get("payload") if isinstance(ligne.get("payload"), dict) else {}
    cree = server._parse_ts(str(ligne.get("cree_a") or ""))
    if cree is None:
        return "refusée", "date de la demande illisible"
    age = (maintenant - cree).total_seconds()
    if age > COMMANDE_EXPIRE_S:
        return "refusée", f"expirée : reçue {int(age // 60)} min après l'envoi (Mac éteint ?)"
    ident = f"{int(cree.timestamp() * 1000):013d}-{int(ligne.get('id') or 0)}"
    if action in boite_bot.COMMANDES:
        boite_bot.deposer(COMMANDES_DATA_DIR, action, ident)
        quoi = "arrêt d'urgence" if action == "stop" else "reprise des entrées"
        return "appliquée", f"{quoi} transmis à la boucle (prochain cycle)"
    if action in ACTIONS_PM:
        demande = dict(payload, action=ACTIONS_PM[action])
        try:
            reponse = server.deposer_demande_prediction(demande)
        except server.HTTPException as exc:
            return "refusée", str(exc.detail)[:300]
        return "appliquée", f"déposée pour la simulation Liquid ({reponse.get('note', 'prochain cycle')})"
    return "refusée", "action inconnue"


def traiter_commandes(session: "Session", maintenant: datetime) -> int:
    """Traite les demandes en attente ; rend leur nombre. Un échec réseau lève
    ErreurPublication (sans corps de réponse)."""
    base = f"{session.env['SUPABASE_URL'].rstrip('/')}/rest/v1/commandes"
    entetes = {k: v for k, v in session.entetes().items() if k != "Prefer"}
    reponse = _requete("GET", base, headers=entetes, params={
        "select": "id,action,payload,cree_a", "statut": "eq.en attente",
        "order": "id.asc", "limit": str(COMMANDES_PAR_PASSAGE)})
    if reponse.status_code != 200:
        raise ErreurPublication(f"Lecture des commandes refusée (HTTP {reponse.status_code})")
    try:
        lignes = reponse.json()
    except ValueError:
        raise ErreurPublication("Réponse illisible pour les commandes") from None
    if not isinstance(lignes, list):
        return 0
    traitees = 0
    for ligne in lignes:
        if not isinstance(ligne, dict) or not isinstance(ligne.get("id"), int):
            continue
        try:
            statut, detail = appliquer_commande(ligne, maintenant)
        except (OSError, ValueError) as exc:
            statut, detail = "refusée", f"échec local ({type(exc).__name__})"
        maj = _requete("PATCH", base, headers={**entetes, "Prefer": "return=minimal"},
                       params={"id": f"eq.{ligne['id']}", "statut": "eq.en attente"},
                       json={"statut": statut, "detail": detail, "traite_a": maintenant.isoformat()})
        if maj.status_code not in (200, 204):
            raise ErreurPublication(f"Mise à jour d'une commande refusée (HTTP {maj.status_code})")
        traitees += 1
        print(f"Commande {ligne['id']} ({ligne.get('action')}) : {statut} — {detail}")
    return traitees


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Publie les résumés du bot dans Supabase.")
    parser.add_argument("--sans-envoi", action="store_true", help="affiche ce qui serait publié, n'envoie rien")
    args = parser.parse_args(argv)

    if args.sans_envoi:
        print(json.dumps(construire_snapshots(datetime.now(timezone.utc)), ensure_ascii=False, indent=2))
        return 0

    env = _lire_env()
    if env is None:
        manquantes = [n for n in VARIABLES if not (os.environ.get(n) or "").strip()]
        print("Publication Supabase non configurée : variables absentes dans .env : "
              + ", ".join(manquantes) + " (voir .env.example). Rien publié.")
        return 0

    session = Session(env)
    try:
        # Les commandes d'abord : la publication qui suit montre déjà leur effet
        # (pause, bras désactivé). Un échec ici ne bloque pas la publication.
        traiter_commandes(session, datetime.now(timezone.utc))
    except ErreurPublication as exc:
        print(f"Commandes iPhone non traitées : {exc}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"Commandes iPhone non traitées : erreur inattendue ({type(exc).__name__})", file=sys.stderr)
    try:
        publier(construire_snapshots(datetime.now(timezone.utc)), session)
    except ErreurPublication as exc:
        print(f"Publication Supabase échouée : {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — dernier recours : pas de traceback dans le journal launchd
        print(f"Publication Supabase échouée : erreur inattendue ({type(exc).__name__})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
