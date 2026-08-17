#!/usr/bin/env python3
"""Le poll smart money coûte-t-il de l'alpha ? — mesure avant de construire.

LA QUESTION. Passer le flux smart money du POLL (GMGN, cache 120 s) à un
WEBHOOK Helius est un projet d'infrastructure : endpoint HTTPS public, service
récepteur, liste de wallets curée, enregistrement des webhooks. Avant de le
payer, il faut savoir ce qu'il rapporterait. Ce script le mesure sur les
données que `src/core/wallets.py` collecte déjà — coût zéro, aucun appel.

CE QU'IL CALCULE, ET POURQUOI CES TROIS MESURES.

  LATENCE DE DÉTECTION   `recorded_at - wallet_ts`. Le temps entre le trade
                         on-chain du wallet et le moment où NOUS l'apprenons.
                         C'est exactement, et uniquement, ce qu'un webhook
                         supprime. Borne HAUTE du gain.

  AVANCE                 `lead_minutes`, déjà calculé par `wallets.py` :
                         `bot_first_seen_ts - wallet_ts`. Positif = le wallet
                         a acheté avant que nous découvrions le token. NÉGATIF
                         = nous connaissions déjà le token. Un webhook ne sert
                         à rien sur les avances négatives : le token était
                         dans nos scans depuis longtemps.

  RÉCUPÉRABLE            `min(latence, avance)` sur le seul segment où
                         l'avance est positive. Réduire la latence en dessous
                         de l'avance ne rapporte rien de plus : on ne peut pas
                         entrer avant que le wallet ait acheté.

Lancement : python -m scripts.mesure_latence_smart_money
"""

import json
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402

# Au-delà, la ligne est une aberration d'horodatage (horloge du fournisseur,
# rejeu tardif) et pas une latence : l'inclure déplacerait les quantiles.
MAX_PLAUSIBLE_MINUTES = 600
# Un wallet vu moins souvent n'a pas de médiane interprétable — même plancher
# d'échantillon que partout ailleurs dans le dépôt.
MIN_OBSERVATIONS = 5
SOLID_OBSERVATIONS = 15
QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90, 0.99)


def quantile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(p * len(ordered)))]


def summarise(label: str, values: list[float], unit: str = "min") -> None:
    if not values:
        print(f"\n{label} : aucune donnée")
        return
    print(f"\n{label}  (n={len(values)})")
    for p in QUANTILES:
        print(f"   p{int(p * 100):02d} : {quantile(values, p):8.2f} {unit}")
    print(f"   médiane {statistics.median(values):.2f} {unit}")


def load(path: str) -> list[dict]:
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def main() -> int:
    rows = load(settings.WALLETS_LOG_PATH)
    if not rows:
        print(f"Aucune donnée dans {settings.WALLETS_LOG_PATH}.")
        return 1

    buys = [
        r for r in rows
        if r.get("side") == "buy"
        and r.get("wallet_ts")
        and r.get("recorded_at")
        and r.get("lead_minutes") is not None
    ]
    if not buys:
        print("Aucun achat exploitable (il faut wallet_ts, recorded_at et lead_minutes).")
        return 1

    def latence(row: dict) -> float:
        return (row["recorded_at"] - row["wallet_ts"]) / 60

    plausibles = [r for r in buys if 0 <= latence(r) < MAX_PLAUSIBLE_MINUTES]
    if not plausibles:
        print("Aucune latence plausible — horodatages inexploitables.")
        return 1
    print(f"Achats exploitables : {len(plausibles)} / {len(buys)} lignes retenues")

    summarise("LATENCE DE DÉTECTION — borne haute du gain d'un webhook",
              [latence(r) for r in plausibles])
    summarise("AVANCE DU WALLET SUR NOTRE DÉCOUVERTE",
              [r["lead_minutes"] for r in plausibles
               if abs(r["lead_minutes"]) < MAX_PLAUSIBLE_MINUTES])

    devant = [r for r in plausibles if r["lead_minutes"] > 0]
    part = 100 * len(devant) / len(plausibles)
    print(
        f"\nLe wallet est RÉELLEMENT en avance dans {len(devant)} cas "
        f"({part:.1f} %). Sur les {100 - part:.1f} % restants nous avions déjà "
        f"découvert le token : un webhook n'y change rien."
    )

    if devant:
        recuperable = [min(latence(r), r["lead_minutes"]) for r in devant]
        avances = [r["lead_minutes"] for r in devant]
        mangee = sum(1 for r in devant if latence(r) >= r["lead_minutes"])
        summarise("RÉCUPÉRABLE PAR UN WEBHOOK (segment en avance seulement)",
                  recuperable)
        print(
            f"   avance médiane du segment : {statistics.median(avances):.2f} min\n"
            f"   cas où notre latence mange TOUTE l'avance : {mangee}/{len(devant)} "
            f"= {100 * mangee / len(devant):.1f} %"
        )

    # PAR WALLET : un webhook se pose sur une LISTE CURÉE, pas sur le flux
    # entier. La question n'est donc pas « le flux est-il en avance » mais
    # « existe-t-il des wallets RÉGULIÈREMENT en avance à qui l'abonner ».
    par_wallet = defaultdict(list)
    for row in buys:
        par_wallet[row["wallet"]].append(row["lead_minutes"])
    eligibles = {w: v for w, v in par_wallet.items() if len(v) >= MIN_OBSERVATIONS}
    if not eligibles:
        print(f"\nAucun wallet avec ≥ {MIN_OBSERVATIONS} observations.")
        return 0
    positifs = {
        w: statistics.median(v) for w, v in eligibles.items()
        if statistics.median(v) > 0
    }
    print(
        f"\nWALLETS : {len(par_wallet)} distincts, {len(eligibles)} avec "
        f"≥ {MIN_OBSERVATIONS} observations, dont {len(positifs)} "
        f"({100 * len(positifs) / len(eligibles):.1f} %) à avance médiane positive."
    )
    solides = sorted(
        ((w, m) for w, m in positifs.items() if len(eligibles[w]) >= SOLID_OBSERVATIONS),
        key=lambda item: -item[1],
    )
    print(f"Candidats à une liste curée (≥ {SOLID_OBSERVATIONS} obs, avance > 0) : "
          f"{len(solides)}")
    for wallet, mediane in solides[:10]:
        print(f"   {wallet[:14]}…  {mediane:6.2f} min   n={len(eligibles[wallet])}")

    print(
        "\nLECTURE. Construire les webhooks n'a de sens que si l'avance des "
        "wallets curés dépasse NETTEMENT la latence de détection ci-dessus. "
        "Si la meilleure avance soutenue est du même ordre que la latence, le "
        "webhook déplace le goulot sans l'ouvrir — le facteur limitant est "
        "alors la découverte, pas le poll."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
