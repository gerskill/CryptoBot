"""Santé des données : le bot sait-il encore lire ses sources ?

POURQUOI. Les contrôles on-chain (Helius) sont restés en `error` pendant des jours, Birdeye a
épuisé son quota, et les entrées partaient quand même avec bundlers, insiders et snipers
vides : une donnée absente ne rejette jamais (invariant du dépôt), donc rien ne bloquait ni ne
prévenait. Sans cette mesure, impossible de savoir si « le scan est bon » ou s'il tourne à
l'aveugle. Ce module ne change AUCUN comportement du bot : il lit ce qu'il écrit déjà et le
résume en états lisibles sur l'iPhone et en alerte Telegram.

TROIS SOURCES D'INFORMATION, de la plus fraîche à la plus lente :
  1. `state["capabilities"]` : l'état en direct de chaque capacité (bougies, holders, prix,
     sécurité, social, smart money) et de ses fournisseurs morts ;
  2. `state["budgets"]` : le quota mensuel des API qui en ont un (Twitter) ;
  3. le journal des trades : pour chaque ENTRÉE des 24 dernières heures, le statut on-chain et
     les champs d'entrée restés vides. Il ne voit que les positions déjà sorties (les écritures
     se font à la sortie) : un retard de la durée de détention, assumé et documenté.

ÉTATS : `ok`, `degrade` (repli en cours ou part notable de données manquantes), `coupe` (plus
aucune source, ou presque toutes les entrées sans donnée), `inconnu` (trop peu d'entrées pour
juger : `MIN_ENTREES`). « Rien à signaler » n'est jamais confondu avec « pas assez de données ».
"""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

FENETRE_HEURES = 24
# Sous ce nombre d'entrées, un pourcentage ne dit rien (règle du dépôt : on ne juge pas un
# échantillon trop court).
MIN_ENTREES = 10
SEUIL_DEGRADE_PCT = 30.0
SEUIL_COUPE_PCT = 80.0
# On ne relit que la fin de chaque journal : les trades y sont ajoutés par ordre de sortie, la
# fenêtre de 24 h est toujours à la fin, et relire des dizaines de Mo toutes les 2 minutes serait
# du gaspillage.
FIN_JOURNAL_OCTETS = 3_000_000

# (nom affiché, champ du journal). 0 est une valeur valide (zéro achat smart money, zéro sniper) :
# seul `None` compte comme « vide ».
CHAMPS_ENTREE = (
    ("holders", "holders_at_entry"),
    ("bundlers", "entry_bundler_rate"),
    ("insiders", "entry_insider_rate"),
    ("snipers", "entry_sniper_count"),
    ("entrapment", "entry_entrapment_ratio"),
    ("smart_money", "entry_smart_money_buys_30m"),
)
# Signaux de manipulation fournis par GMGN en un seul appel de découverte.
SIGNAUX_GMGN = ("bundlers", "insiders", "snipers", "entrapment")
STATUTS_ONCHAIN_DEFAUT = ("error", "missing", "budget")
STATUTS_ONCHAIN_LUS = ("ok", "partial")


def etat_depuis_pct(pct: float, total: int) -> str:
    """`ok` / `degrade` / `coupe` d'un pourcentage de défauts, `inconnu` si l'échantillon est trop court."""
    if total < MIN_ENTREES:
        return "inconnu"
    if pct >= SEUIL_COUPE_PCT:
        return "coupe"
    if pct >= SEUIL_DEGRADE_PCT:
        return "degrade"
    return "ok"


def champs_vides(entrees: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Part (en %) d'entrées où chaque champ surveillé est `None`, du plus vide au moins vide."""
    if not entrees:
        return []
    lignes = []
    for nom, champ in CHAMPS_ENTREE:
        vides = sum(1 for e in entrees if e.get(champ) is None)
        lignes.append({"champ": nom, "vide_pct": round(100 * vides / len(entrees), 1)})
    lignes.sort(key=lambda ligne: ligne["vide_pct"], reverse=True)
    return lignes


def _sources_capacites(capacites: Any) -> list[dict[str, str]]:
    lignes = []
    for c in capacites if isinstance(capacites, list) else []:
        if not isinstance(c, dict) or not c.get("capability"):
            continue
        morts = [str(m) for m in c.get("down") or []]
        if c.get("blind"):
            etat, detail = "coupe", "aucune source disponible"
        else:
            etat = "degrade" if (c.get("degraded") or morts) else "ok"
            vivants = [str(v) for v in c.get("available") or []]
            detail = (f"source : {c['using']}" if c.get("using")
                      else f"disponible : {', '.join(vivants) or '—'}")
            if morts:
                detail += f" ; morte : {', '.join(morts)} (repli en place)"
        lignes.append({"nom": str(c["capability"]), "etat": etat, "detail": detail})
    return lignes


def _source_budgets(budgets: Any) -> list[dict[str, str]]:
    """Quotas mensuels. Jamais `coupe` : le bot a un repli (voir capabilities), et un quota
    épuisé depuis des semaines (Twitter) ferait sonner la veille sans fin."""
    lignes = []
    for nom, b in (budgets.items() if isinstance(budgets, dict) else ()):
        pct = b.get("pct_used") if isinstance(b, dict) else None
        if not isinstance(pct, (int, float)):
            continue
        etat = "degrade" if pct >= 90 else "ok"
        lignes.append({
            "nom": f"{nom} (quota du mois)",
            "etat": etat,
            "detail": f"{pct:.0f} % utilisé" + (" : épuisé" if pct >= 100 else ""),
        })
    return lignes


def _source_onchain(entrees: list[dict[str, Any]], etat_onchain: Any = None) -> dict[str, str]:
    statuts = [e.get("onchain_status") for e in entrees]
    # Les entrées d'avant l'observateur on-chain (`None`) et les modules coupés exprès
    # (`disabled`) ne sont pas des pannes.
    mesures = [s for s in statuts if s in STATUTS_ONCHAIN_DEFAUT + STATUTS_ONCHAIN_LUS]
    total = len(mesures)
    defauts = sum(1 for s in mesures if s in STATUTS_ONCHAIN_DEFAUT)
    pct = 100 * defauts / total if total else 0.0
    nom = "analyse on-chain (Helius)"
    if total < MIN_ENTREES:
        return {"nom": nom, "etat": "inconnu",
                "detail": f"{total} entrée(s) mesurée(s) en {FENETRE_HEURES} h : trop peu pour juger"}
    erreur = statuts.count("error")
    absente = statuts.count("missing")
    quota = statuts.count("budget")
    etat = etat_depuis_pct(pct, total)
    # Quota du mois épuisé (l'observateur le dit, ou la plupart des défauts sont « quota ») : cause
    # connue, reprise automatique, rien à faire. « Dégradé » et pas « coupé » : sinon la veille
    # sonnait « données en panne » pendant des jours pour une limite d'abonnement.
    quota_connu = isinstance(etat_onchain, dict) and etat_onchain.get("quota_epuise")
    if etat == "coupe" and (quota_connu or (defauts and quota >= defauts / 2)):
        reprise = etat_onchain.get("reprise_dans_min") if quota_connu else None
        return {
            "nom": nom,
            "etat": "degrade",
            "detail": ("quota Helius du mois épuisé : reprise automatique"
                       + (f" (nouvel essai dans {reprise} min)" if reprise is not None else "")
                       + f", rien à faire ; {defauts}/{total} entrées sans lecture on-chain"),
        }
    return {
        "nom": nom,
        "etat": etat,
        "detail": (f"{defauts}/{total} entrées sans lecture on-chain "
                   f"(erreur {erreur}, absente {absente}, quota {quota})"),
    }


def _source_gmgn(entrees: list[dict[str, Any]]) -> dict[str, str]:
    """Mesurée seulement sur les entrées écrites APRÈS les correctifs du 2026-11-21 (fusion qui jetait
    les signaux, champ insiders mal nommé) : elles portent la clé `entry_manipulation_source`. Les
    entrées d'avant resteraient « coupé » 24 h de plus pour un défaut déjà corrigé."""
    recentes = [e for e in entrees if "entry_manipulation_source" in e]
    nom = "signaux de manipulation (GMGN)"
    if not recentes:
        return {"nom": nom, "etat": "inconnu",
                "detail": "aucune entrée depuis le correctif du 21/11 : à juger plus tard"}
    total = len(recentes)
    par_champ = {ligne["champ"]: ligne["vide_pct"] for ligne in champs_vides(recentes)}
    valeurs = [par_champ[c] for c in SIGNAUX_GMGN if c in par_champ]
    moyenne = sum(valeurs) / len(valeurs)
    return {
        "nom": nom,
        "etat": etat_depuis_pct(moyenne, total),
        "detail": (f"{moyenne:.0f} % de champs vides ({', '.join(SIGNAUX_GMGN)}) : "
                   "à vérifier côté GMGN"),
    }


def resume(etat: dict[str, Any] | None, entrees: list[dict[str, Any]]) -> dict[str, Any]:
    """Le résumé publié : sources (capacités, quotas, on-chain, GMGN) et champs vides.

    `etat` est le state.json du bot (ou `None`/vide s'il est absent : les sources en direct sont
    alors simplement omises, on ne les invente pas)."""
    etat = etat if isinstance(etat, dict) else {}
    vides = champs_vides(entrees)
    sources = _sources_capacites(etat.get("capabilities")) + _source_budgets(etat.get("budgets"))
    sources.append(_source_onchain(entrees, etat.get("onchain")))
    sources.append(_source_gmgn(entrees))
    return {"fenetre_h": FENETRE_HEURES, "entrees": len(entrees), "sources": sources, "champs_vides": vides}


def sources_coupees(donnees: dict[str, Any]) -> list[dict[str, str]]:
    """Les sources à l'état `coupe` : ce que la veille signale sur Telegram."""
    return [s for s in donnees.get("sources", []) if s.get("etat") == "coupe"]


# --- Lecture des journaux (seule partie qui touche au disque) ---------------------------------

def _fin_du_journal(chemin: str, max_octets: int = FIN_JOURNAL_OCTETS) -> list[dict[str, Any]]:
    """Lignes JSON de la fin du fichier ; la première, probablement coupée, est écartée."""
    try:
        taille = os.path.getsize(chemin)
        with open(chemin, "rb") as fh:
            if taille > max_octets:
                fh.seek(taille - max_octets)
                fh.readline()
            brut = fh.read()
    except OSError:
        return []
    lignes = []
    for ligne in brut.splitlines():
        try:
            lignes.append(json.loads(ligne))
        except ValueError:
            continue
    return lignes


def _moment(texte: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(texte))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def charger_entrees(maintenant: datetime, bras: list[str], chemin_journal) -> list[dict[str, Any]]:
    """Entrées des 24 dernières heures (une ligne par position sortie, bras actifs seulement).

    `chemin_journal(nom)` donne le journal d'un bras : injecté pour les tests. Les jambes
    partielles sont ignorées (`is_final_exit`) : leur contexte d'entrée est identique à celui de
    la sortie finale, les compter doublerait l'échantillon."""
    limite = maintenant - timedelta(hours=FENETRE_HEURES)
    entrees = []
    for nom in bras:
        for ligne in _fin_du_journal(chemin_journal(nom)):
            if not ligne.get("is_final_exit"):
                continue
            entree = _moment(ligne.get("timestamp_entry"))
            if entree is not None and entree >= limite:
                entrees.append(ligne)
    return entrees
