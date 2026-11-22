#!/usr/bin/env python3
"""Veille : prévenir sur Telegram quand le bot, le site ou Liquid s'arrêtent.

LE TROU QUE ÇA FERME. Sur le Mac mini, tout est lancé à la main et rien ne
redémarre après un reboot (HANDOFF du 16/11) ; le bot memecoin a tourné des
jours sur un ancien code sans que personne le sache. Les LaunchAgents
(`scripts/launchd/`) relancent les processus ; ce script dit quand ça n'a
pas suffi.

CE QU'IL REGARDE, sans dépendre de l'API (qui peut être elle-même tombée) :
  - bot memecoin : âge de `data/state.json` (`updated_at`) ;
  - Liquid : âge de `data/prediction/state.json` ;
  - site : réponse de http://127.0.0.1:8002/api/health (dashboard officiel,
    modifiable par CRYPTOBOT_UI_HEALTH_URL) ;
  - données : une source du bot COUPÉE (plus aucun fournisseur, on-chain en erreur sur presque
    toutes les entrées, signaux GMGN vides) — voir src/core/sante_donnees.py. Un simple repli
    (source dégradée) ne prévient pas : il se lit dans l'app.

QUAND IL PRÉVIENT. Au CHANGEMENT d'état seulement (OK → panne, panne → OK),
plus un rappel toutes les 6 h tant que la panne dure. Un message toutes les
5 minutes serait coupé au bout d'une journée — et l'alerte qui compte avec.

LE CAS QU'IL NE PEUT PAS VOIR : le Mac entier éteint ou sans réseau — la
veille s'arrête avec lui. Pour ça, `HEALTHCHECK_PING_URL` dans `.env`
(healthchecks.io, gratuit) : la veille y signale chaque passage, et c'est le
service EXTERNE qui prévient quand les signaux cessent.

Lecture seule sur les données du bot. N'écrit que son propre état
(`data/veille_etat.json`). Aucune clé affichée.

Usage :
  python scripts/veille.py                 # un passage (lancé toutes les 5 min par launchd)
  python scripts/veille.py --sans-envoi    # affiche le diagnostic, n'envoie rien
  python scripts/veille.py --verifie bot,ui
  python scripts/veille.py --test          # envoie UN message de test sur Telegram, rien d'autre
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from src import settings  # noqa: E402
from src.core import sante_donnees  # noqa: E402
from src.core.arm import load_manifest  # noqa: E402
from src.notify.telegram import TelegramNotifier  # noqa: E402

# Le bot écrit son état à chaque cycle de 90 s et l'API le dit périmé après
# 180 s. 10 min laisse passer un cycle lent (quota Birdeye) sans fausse alerte.
SEUIL_BOT_S = 600
# `run_prediction.py` tourne toutes les 60 s par défaut.
SEUIL_PREDICTION_S = 600
# Dashboard officiel sur 8002 depuis le 2026-11-22 (service com.cryptobbot.ui-nouveau).
URL_SANTE_UI = os.getenv("CRYPTOBOT_UI_HEALTH_URL", "http://127.0.0.1:8002/api/health")
RAPPEL_S = 6 * 3600
VERIFICATIONS = ("bot", "ui", "prediction", "donnees")
ETAT_PATH = os.path.join(settings.BASE_DIR, "data", "veille_etat.json")


def _age_fichier_etat(chemin: str, maintenant: float):
    """Âge en secondes du `updated_at` d'un state.json, ou `None` s'il est
    absent ou illisible — ce qui est une panne en soi, pas une donnée
    manquante à ignorer : un bot qui n'écrit plus son état ne tourne plus."""
    try:
        with open(chemin, encoding="utf-8") as fh:
            return maintenant - float(json.load(fh)["updated_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _diagnostic_donnees(maintenant: float) -> tuple[bool, str]:
    """(ok, détail) : `ok` est faux seulement si une source est à l'état `coupe`. « Inconnu » (trop
    peu d'entrées) et « dégradé » ne sont pas des pannes."""
    moment = datetime.fromtimestamp(maintenant, tz=timezone.utc)
    try:
        with open(settings.STATE_PATH, encoding="utf-8") as fh:
            etat = json.load(fh)
    except (OSError, ValueError):
        etat = None
    bras = [e["name"] for e in load_manifest() if e.get("enabled", True)]
    entrees = sante_donnees.charger_entrees(moment, bras, lambda nom: settings.arm_paths(nom)["trades"])
    coupees = sante_donnees.sources_coupees(sante_donnees.resume(etat, entrees))
    if not coupees:
        return True, "aucune source coupée"
    return False, " ; ".join(f"{s['nom']} : {s['detail']}" for s in coupees)


def tout_ok(diagnostic: dict) -> bool:
    """État pour le ping externe (healthchecks.io). Les données n'y entrent pas : ce ping dit
    « le Mac et le bot vivent », et une source de données coupée ne doit pas le faire passer
    pour mort (elle a son message Telegram)."""
    return all(ok for nom, (ok, _) in diagnostic.items() if nom != "donnees")


def diagnostiquer(verifs, maintenant: float, get=requests.get) -> dict:
    """`{nom: (ok, détail)}` pour chaque vérification demandée."""
    resultat = {}
    if "bot" in verifs:
        age = _age_fichier_etat(settings.STATE_PATH, maintenant)
        resultat["bot"] = (
            age is not None and age < SEUIL_BOT_S,
            "aucun state.json lisible" if age is None else f"dernier cycle il y a {age / 60:.0f} min",
        )
    if "prediction" in verifs:
        chemin = os.path.join(settings.PREDICTION_DATA_DIR, "state.json")
        age = _age_fichier_etat(chemin, maintenant)
        resultat["prediction"] = (
            age is not None and age < SEUIL_PREDICTION_S,
            "aucun state.json lisible" if age is None else f"dernier cycle il y a {age / 60:.0f} min",
        )
    if "donnees" in verifs:
        resultat["donnees"] = _diagnostic_donnees(maintenant)
    if "ui" in verifs:
        try:
            reponse = get(URL_SANTE_UI, timeout=5)
            resultat["ui"] = (reponse.status_code == 200, f"HTTP {reponse.status_code}")
        except requests.exceptions.RequestException as exc:
            resultat["ui"] = (False, f"injoignable ({type(exc).__name__})")
    return resultat


def messages_a_envoyer(diagnostic: dict, memoire: dict, maintenant: float) -> tuple[list, dict]:
    """Décide QUOI dire, sans rien envoyer — testable sans réseau.

    `memoire` : `{nom: {"ok": bool, "depuis": ts, "dernier_message": ts}}`.
    Retourne les messages et la nouvelle mémoire.
    """
    nouvelle = {}
    messages = []
    for nom, (ok, detail) in diagnostic.items():
        avant = memoire.get(nom)
        if avant is None or avant.get("ok") != ok:
            # Premier passage : on ne prévient que d'une panne. Annoncer « OK »
            # à chaque installation n'apprend rien.
            if not ok or avant is not None:
                duree = ""
                if avant is not None and ok:
                    duree = f" après {(maintenant - avant.get('depuis', maintenant)) / 60:.0f} min de panne"
                messages.append(f"{'✅' if ok else '🔴'} {nom} : {'rétabli' + duree if ok else 'EN PANNE'} — {detail}")
                nouvelle[nom] = {"ok": ok, "depuis": maintenant, "dernier_message": maintenant,
                                 "detail": detail}
            else:
                nouvelle[nom] = {"ok": ok, "depuis": maintenant, "dernier_message": None,
                                 "detail": detail}
            continue
        etat = dict(avant)
        # Le détail du DERNIER passage : l'app l'affiche avec l'alerte (« données en panne depuis
        # 77 min » sans dire laquelle ne servait à rien).
        etat["detail"] = detail
        if not ok and maintenant - (etat.get("dernier_message") or 0) >= RAPPEL_S:
            heures = (maintenant - etat.get("depuis", maintenant)) / 3600
            messages.append(f"🔴 {nom} : toujours en panne depuis {heures:.1f} h — {detail}")
            etat["dernier_message"] = maintenant
        nouvelle[nom] = etat
    return messages, nouvelle


def _lire_memoire() -> dict:
    try:
        with open(ETAT_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _ecrire_memoire(memoire: dict) -> None:
    os.makedirs(os.path.dirname(ETAT_PATH), exist_ok=True)
    tmp = f"{ETAT_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(memoire, fh, indent=2)
    os.replace(tmp, ETAT_PATH)  # atomique, comme tout fichier d'état du dépôt


def _ping_externe(tout_ok: bool) -> None:
    """healthchecks.io : `<url>` si tout va bien, `<url>/fail` sinon. Le
    service prévient tout seul si plus aucun ping n'arrive (Mac éteint)."""
    url = os.getenv("HEALTHCHECK_PING_URL")
    if not url:
        return
    try:
        requests.get(url if tout_ok else f"{url.rstrip('/')}/fail", timeout=10)
    except requests.exceptions.RequestException as exc:
        print(f"[veille] ping externe impossible : {type(exc).__name__}")


MESSAGE_TEST = (
    "🧪 <b>CryptobBot — test de la veille</b>\n"
    "Si tu lis ce message sur ton téléphone, les alertes de panne arriveront aussi. "
    "Mode PAPER, rien n'a été modifié."
)


def envoyer_test(notifier=None) -> int:
    """Un message de test, et rien d'autre : ni diagnostic, ni mémoire, ni ping externe.

    Code de sortie : 0 si Telegram a accepté le message, 1 sinon (configuration absente ou refus).
    Aucune clé affichée : on dit seulement QUELLE variable manque."""
    notifier = notifier or TelegramNotifier(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)
    if not notifier.enabled:
        manquantes = [nom for nom, valeur in (("TELEGRAM_BOT_TOKEN", settings.TELEGRAM_BOT_TOKEN),
                                              ("TELEGRAM_CHAT_ID", settings.TELEGRAM_CHAT_ID)) if not valeur]
        print(f"[veille] Telegram non configuré : {', '.join(manquantes) or 'configuration incomplète'} "
              "absent(s) de .env")
        return 1
    if notifier.send(MESSAGE_TEST):
        print("[veille] message de test envoyé : regarde Telegram sur ton téléphone.")
        return 0
    print("[veille] Telegram a refusé le message (jeton ou chat_id faux ? voir le journal ci-dessus).")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sans-envoi", action="store_true", help="diagnostic seul, aucun message ni ping")
    parser.add_argument("--test", action="store_true",
                        help="envoie un message de test sur Telegram et s'arrête")
    parser.add_argument("--verifie", default=",".join(VERIFICATIONS),
                        help=f"liste parmi {','.join(VERIFICATIONS)} (défaut : tout)")
    args = parser.parse_args()
    if args.test:
        raise SystemExit(envoyer_test())
    verifs = [v.strip() for v in args.verifie.split(",") if v.strip()]
    inconnues = set(verifs) - set(VERIFICATIONS)
    if inconnues:
        raise SystemExit(f"vérification inconnue : {', '.join(sorted(inconnues))}")

    maintenant = time.time()
    diagnostic = diagnostiquer(verifs, maintenant)
    for nom, (ok, detail) in diagnostic.items():
        print(f"[veille] {nom:<11}{'OK   ' if ok else 'PANNE'} {detail}")
    if args.sans_envoi:
        return

    messages, memoire = messages_a_envoyer(diagnostic, _lire_memoire(), maintenant)
    if messages:
        notifier = TelegramNotifier(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)
        if notifier.enabled:
            notifier.send("<b>CryptobBot — veille</b>\n" + "\n".join(messages))
        else:
            print("[veille] Telegram non configuré : " + " | ".join(messages))
    _ecrire_memoire(memoire)
    _ping_externe(tout_ok(diagnostic))


if __name__ == "__main__":
    main()
