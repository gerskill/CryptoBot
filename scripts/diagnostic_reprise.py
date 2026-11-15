#!/usr/bin/env python3
"""Diagnostic de reprise après une interruption de surveillance.

RÉPOND AUX TROIS QUESTIONS QU'ON SE POSE EN RENTRANT, dans l'ordre où elles
doivent être posées :

  1. la boucle tourne-t-elle encore, et si non, DEPUIS QUAND ?
  2. qu'est-ce que `data/` a accumulé pendant l'absence ?
  3. que valent les gardes livrées juste avant le départ ?

L'ORDRE EST LA DONNÉE. Une relance écrit dans `data/` et reprend le verrou :
elle efface l'indice qui permet de dater l'arrêt. Ce script est en LECTURE
SEULE et ne relance rien — il se termine en imprimant la séquence de reprise,
qui reste une décision du propriétaire.

Dater l'arrêt, ce n'est pas de la curiosité : `ShadowTracker` écarte les suivis
périmés pendant une coupure, `LearningEngine` compte des cycles, et un trou de
plusieurs jours dans les journaux se lit autrement qu'un crash de dix minutes.

Usage :
  python3 -m scripts.diagnostic_reprise
  python3 -m scripts.diagnostic_reprise --depuis 2026-08-17
"""

import argparse
import fcntl
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from src.core.arm import load_manifest  # noqa: E402
from src.core.funnel import FUNNEL_MAX_BYTES  # noqa: E402
from src.core.shadow import reason_family, tracking_path  # noqa: E402

# Gardes livrées le 2026-08-17, jamais observées sur une longue série. La clé
# est la famille de motif rendue par `reason_family`, la valeur son libellé.
GARDES_2026_08_17 = {
    "lp_lock": "filtre LP verrouillée",
    "sector": "concentration sectorielle",
}
# Motifs de sortie du watchdog anti-slow-rug, cherchés dans les journaux.
MOTIFS_WATCHDOG = ("dev_dump", "slow rug", "solde du créateur", "dev dump")


def _age(seconds: float) -> str:
    """Durée lisible. Un trou de 13 jours et un trou de 13 minutes n'appellent
    pas la même conclusion — l'unité doit sauter aux yeux."""
    if seconds < 0:
        return "dans le futur (horloge ?)"
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} jours"


def _horodatage(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _derniere_ligne_ts(path: str) -> float:
    """Horodatage de la dernière ligne d'un .jsonl, 0.0 si illisible.

    Lecture PAR LA FIN : ces journaux font plusieurs Mo, et on ne veut qu'une
    ligne. Les clés d'horodatage diffèrent d'un journal à l'autre (`ts`,
    `timestamp`, `timestamp_exit`) — on prend la première présente.
    """
    if not os.path.exists(path):
        return 0.0
    try:
        taille = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, taille - 65536))
            lignes = fh.read().decode("utf-8", errors="ignore").splitlines()
        for ligne in reversed(lignes):
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                row = json.loads(ligne)
            except json.JSONDecodeError:
                continue
            for cle in ("ts", "timestamp", "timestamp_exit", "timestamp_entry"):
                valeur = row.get(cle)
                if isinstance(valeur, (int, float)) and valeur > 0:
                    return float(valeur)
                if isinstance(valeur, str):
                    try:
                        return datetime.fromisoformat(
                            valeur.replace("Z", "+00:00")
                        ).timestamp()
                    except ValueError:
                        continue
    except OSError:
        return 0.0
    return 0.0


def verrou() -> tuple[str, str]:
    """(état, détail) du verrou d'instance, SANS le prendre durablement.

    `flock` est détenu par un seul process à la fois et le noyau le libère à la
    mort du détenteur. Si on arrive à le prendre, c'est que PERSONNE ne le
    tient : la boucle ne tourne pas. On le relâche aussitôt, sans supprimer le
    fichier — `InstanceLock.release()` le fait, mais lui a le droit : il l'a
    créé. Le supprimer ici effacerait le PID du dernier détenteur.
    """
    path = settings.LOCK_PATH
    if not os.path.exists(path):
        return ("ARRÊTÉE", f"aucun fichier de verrou ({path})")
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError as exc:
        return ("INDÉTERMINÉ", f"verrou illisible : {exc}")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            pid = os.read(fd, 32).decode("utf-8").strip()
        except OSError:
            pid = "?"
        os.close(fd)
        return ("EN COURS", f"verrou détenu par le PID {pid}")
    # Verrou libre : on l'a pris, donc la boucle est arrêtée.
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        pid = os.read(fd, 32).decode("utf-8").strip() or "?"
    except OSError:
        pid = "?"
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    return ("ARRÊTÉE", f"verrou libre — dernier PID connu {pid}, process mort")


def journaux(manifeste: list[dict]) -> list[tuple[str, str, float]]:
    """(bras, fichier, dernier horodatage) pour tout ce que la boucle écrit."""
    out = []
    for entree in manifeste:
        nom = entree["name"]
        chemins = settings.arm_paths(nom)
        for cle in ("trades", "shadow"):
            out.append((nom, chemins[cle], _derniere_ligne_ts(chemins[cle])))
    for nom, chemin in (
        ("flotte", settings.FUNNEL_LOG_PATH),
        ("flotte", settings.WALLETS_LOG_PATH),
    ):
        out.append((nom, chemin, _derniere_ligne_ts(chemin)))
    return out


def taille_data() -> tuple[int, list[tuple[str, int]]]:
    """Poids total de `data/` et les plus gros fichiers."""
    racine = os.path.join(settings.BASE_DIR, "data")
    total, fichiers = 0, []
    for dossier, _, noms in os.walk(racine):
        for nom in noms:
            chemin = os.path.join(dossier, nom)
            try:
                taille = os.path.getsize(chemin)
            except OSError:
                continue
            total += taille
            fichiers.append((os.path.relpath(chemin, settings.BASE_DIR), taille))
    fichiers.sort(key=lambda x: -x[1])
    return total, fichiers


def gardes(manifeste: list[dict], depuis: float) -> dict[str, dict]:
    """Ce que les gardes du 2026-08-17 ont réellement écarté, par famille.

    Source : les journaux SHADOW, pas l'entonnoir. `funnel_log` échantillonne
    le détail des rejets de filtres (1 sur `FILTER_SAMPLE_EVERY`) — il compte
    juste en agrégé, mais sous-représente un motif précis. Le shadow enregistre
    chaque rejet suivi, c'est le dénominateur honnête.
    """
    compte = {famille: {"n": 0, "gagnants": 0, "bras": set()} for famille in GARDES_2026_08_17}
    for entree in manifeste:
        nom = entree["name"]
        chemin = settings.arm_paths(nom)["shadow"]
        if not os.path.exists(chemin):
            continue
        with open(chemin, encoding="utf-8") as fh:
            for ligne in fh:
                ligne = ligne.strip()
                if not ligne:
                    continue
                try:
                    row = json.loads(ligne)
                except json.JSONDecodeError:
                    continue
                if float(row.get("timestamp", 0)) < depuis:
                    continue
                famille = reason_family(row.get("reason", ""))
                if famille not in compte:
                    continue
                compte[famille]["n"] += 1
                compte[famille]["bras"].add(nom)
                if row.get("would_have_won"):
                    compte[famille]["gagnants"] += 1
    return compte


def watchdog(manifeste: list[dict], depuis: float) -> int:
    """Sorties attribuées au watchdog anti-slow-rug depuis `depuis`."""
    total = 0
    for entree in manifeste:
        chemin = settings.arm_paths(entree["name"])["trades"]
        if not os.path.exists(chemin):
            continue
        with open(chemin, encoding="utf-8") as fh:
            for ligne in fh:
                bas = ligne.lower()
                if any(motif in bas for motif in MOTIFS_WATCHDOG):
                    total += 1
    return total


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument(
        "--depuis",
        default="2026-08-17",
        help="date (AAAA-MM-JJ) à partir de laquelle qualifier les gardes",
    )
    args = parseur.parse_args()
    try:
        depuis = datetime.strptime(args.depuis, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except ValueError:
        print(f"date illisible : {args.depuis} (attendu AAAA-MM-JJ)")
        return 2

    maintenant = time.time()
    manifeste = load_manifest()
    actifs = [e for e in manifeste if e.get("enabled", True)]

    print("=" * 72)
    print(f"DIAGNOSTIC DE REPRISE — {_horodatage(maintenant)}")
    print("=" * 72)

    # 1 ------------------------------------------------------------------
    etat, detail = verrou()
    print(f"\n1. BOUCLE : {etat}\n   {detail}")

    lignes = journaux(manifeste)
    vus = [(nom, chemin, ts) for nom, chemin, ts in lignes if ts > 0]
    if not vus:
        print(
            "   Aucun journal daté — soit le bot n'a jamais tourné sur cette\n"
            "   machine, soit `data/` a été déplacé. Rien à conclure d'autre."
        )
        dernier = 0.0
    else:
        dernier = max(ts for _, _, ts in vus)
        print(
            f"   Dernière écriture, tous journaux confondus : "
            f"{_horodatage(dernier)} (il y a {_age(maintenant - dernier)})"
        )
        if etat == "ARRÊTÉE":
            print(
                f"   ⇒ ARRÊT DATÉ AU {_horodatage(dernier)}. "
                f"Noter cette date AVANT de relancer : la reprise l'efface."
            )
        print("\n   Par bras (dernière ligne écrite) :")
        for nom, chemin, ts in sorted(lignes, key=lambda x: -x[2]):
            court = os.path.basename(chemin)
            if ts <= 0:
                print(f"     {nom:14} {court:24} —  (vide ou absent)")
            else:
                print(
                    f"     {nom:14} {court:24} {_horodatage(ts)} "
                    f"(il y a {_age(maintenant - ts)})"
                )

    # Suivis shadow en cours : ce que la reprise récupérera.
    en_cours = 0
    for entree in actifs:
        chemin = tracking_path(settings.arm_paths(entree["name"])["shadow"])
        if not os.path.exists(chemin):
            continue
        try:
            with open(chemin, encoding="utf-8") as fh:
                en_cours += len(json.load(fh))
        except (OSError, ValueError):
            continue
    if en_cours:
        print(
            f"\n   Suivis shadow persistés : {en_cours}. Ceux de plus de 4 h "
            f"seront écartés au démarrage, pas jugés."
        )

    # 2 ------------------------------------------------------------------
    total, fichiers = taille_data()
    print(f"\n2. DONNÉES : data/ pèse {total / 1e6:.1f} Mo")
    if not fichiers:
        print("   Répertoire vide.")
    else:
        print("   Les plus gros :")
        for chemin, taille in fichiers[:8]:
            marque = ""
            if chemin.endswith("funnel_log.jsonl") and taille > FUNNEL_MAX_BYTES:
                marque = "  ← au-dessus du seuil de rotation (8 Mo)"
            elif taille > FUNNEL_MAX_BYTES and "funnel" not in chemin:
                marque = "  ← append-only SANS rotation"
            print(f"     {taille / 1e6:8.1f} Mo  {chemin}{marque}")
    print("   Sauvegarder avant toute manipulation : ./scripts/backup.sh")

    # 3 ------------------------------------------------------------------
    print(f"\n3. GARDES DU 2026-08-17, depuis le {args.depuis}")
    compte = gardes(manifeste, depuis)
    rien = True
    for famille, libelle in GARDES_2026_08_17.items():
        stats = compte[famille]
        if not stats["n"]:
            print(f"   {libelle:28} aucun rejet jugé — rien à conclure")
            continue
        rien = False
        taux = 100 * stats["gagnants"] / stats["n"]
        print(
            f"   {libelle:28} {stats['n']:4} rejets jugés, "
            f"{stats['gagnants']} auraient atteint +100 % ({taux:.1f} %), "
            f"{len(stats['bras'])} bras"
        )
        if stats["n"] < 15:
            print(
                "        échantillon sous SHADOW_MIN_SAMPLE (15) : "
                "à lire comme une tendance, pas comme un verdict"
            )
    sorties = watchdog(manifeste, depuis)
    print(f"   {'watchdog anti-slow-rug':28} {sorties} sortie(s) déclenchée(s)")
    if rien and not sorties:
        print(
            "   ⇒ Aucune des trois gardes n'a encore mordu. C'est un résultat :\n"
            "     soit elles sont trop lâches, soit le flux ne les rencontre pas.\n"
            "     Ne pas toucher aux seuils sur cette base — mesurer plus longtemps."
        )

    # ---------------------------------------------------------------------
    print("\n" + "=" * 72)
    if etat == "EN COURS":
        print("La boucle tourne. Rien à relancer.")
    else:
        print("SÉQUENCE DE REPRISE (dans cet ordre) :")
        if dernier:
            print(f"  0. arrêt daté au {_horodatage(dernier)} — noté ci-dessus")
        print("  1. ./scripts/backup.sh")
        print("  2. python3 -m unittest discover -s tests")
        print("  3. python3 -m src.main")
        print("  4. python3 -m scripts.diagnostic_reprise   # confirmer EN COURS")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
