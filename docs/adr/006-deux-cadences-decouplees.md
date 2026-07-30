# 006. Scan et monitoring tournent à des cadences différentes

Date : 2026-07-29
Statut : accepté

## Contexte

La spécification prévoit un cycle unique de 90 secondes pour tout : découverte
et surveillance des positions.

Deux besoins opposés dans la même boucle :

- **Découverte** — DexScreener agrège ses données toutes les ~30-60 s.
  Scanner plus vite renvoie les mêmes octets. 90 s convient.
- **Monitoring** — un stop loss à -25% échantillonné toutes les 90 s sur un
  token qui perd 40% en 30 s fait sortir bien plus bas que le seuil.

La détection de rug pull (ADR 004) travaille sur une fenêtre de 2 minutes : à
90 s d'échantillonnage, elle dispose de 1 à 2 points. Inexploitable.

## Décision

Deux cadences indépendantes :

- `scan.interval_seconds` : 90 s — découverte
- `scan.monitor_interval_seconds` : **20 s** — positions ouvertes

`_sleep_monitoring()` intercale les ticks de monitoring pendant l'attente
entre deux scans. Les ticks rapprochés sont silencieux (`verbose=False`) :
seules les sorties sont logguées.

Coût : 1 requête par position et par tick, sur un quota DexScreener de
300/min. Négligeable.

## Conséquences

- L'écart résiduel persiste : mesuré à -27.2% pour un SL à -25%. Réduire
  l'intervalle le diminue sans jamais l'annuler — un memecoin peut gapper
  entre deux mesures.
- Le dashboard est republié à chaque tick de monitoring, donc rafraîchi toutes
  les 20 s quand une position est ouverte.
- Une exception dans le monitoring rapproché est attrapée localement : elle ne
  doit pas interrompre l'attente du prochain scan.
