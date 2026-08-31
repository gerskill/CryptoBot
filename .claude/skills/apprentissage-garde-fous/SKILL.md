---
name: apprentissage-garde-fous
description: Modifier la boucle d'auto-amélioration de CryptobBot (src/core/learning.py) — ajustement de filtres, de poids, de sorties, de risque, relâchements, bornes, backtests de validation. Utilise ce skill dès qu'il est question de LearningEngine, d'apprentissage, d'un paramètre qui bouge ou ne bouge pas, de PARAM_BOUNDS, de cadence d'ajustement, de shadow trading comme preuve, de surapprentissage, d'inactivité d'un bras, ou du passage en LIVE. Ce module peut dégrader silencieusement les sept bras à la fois : ne rien y toucher sans ce skill.
---

# Apprentissage et garde-fous

Le `LearningEngine` ajuste les paramètres d'**un bras** à partir de **ses**
trades. Le dépôt compte quelques dizaines de trades par bras : la contrainte
dominante n'est pas la finesse de l'algorithme, c'est la taille d'échantillon.

## Les garde-fous, et pourquoi chacun existe

| constante | valeur | rôle |
|---|---|---|
| `MIN_SEGMENT_SAMPLE` | 10 | pas d'ajustement sans 10 trades dans le segment |
| `MIN_TRADES_PER_ARM` | 15 | plancher par bras — le multi-bras divise l'échantillon par 7 |
| `MIN_FLOW_TO_TIGHTEN` | 2 | **casse la boucle** : interdit de resserrer quand le flux est déjà famélique. Relâcher reste toujours permis |
| `MIN_TRADES_FOR_WEIGHTS` | 50 | avant de repondérer un composant |
| `MIN_SIGNALS_PER_AGENT` | 20 | avant de juger un agent |
| `EXIT_BACKTEST_MIN_COVERAGE` | 15 | sous ce seuil, **aucun verdict** — ne pas savoir ≠ annuler |
| `PARAM_BOUNDS` (+ `bounds` par bras) | — | bornes dures, sinon les règles ne font que resserrer |
| `INACTIVITY_CYCLES` | 300 | au-delà, le seuil dominant d'un bras qui n'entre plus est desserré |
| `_relax_set` | — | un relâchement qui, une fois clampé, **resserrerait** est refusé |

Cadences : `FILTER_CADENCE = 5`, `WEIGHTS_CADENCE = 10`, `EXIT_CADENCE = 10`,
`RISK_CADENCE = 20` trades. Elles évitent qu'un même échantillon soit
re-exploité à chaque cycle jusqu'à ce qu'il rende un signal par hasard.

## Les trois relâchements sont indépendants

| mécanisme | preuve exigée | débloque |
|---|---|---|
| `_adjust_filters` | 15 trades du bras **et** 10 dans le segment | un bras qui trade et perd sur un segment |
| `_relax_from_shadow` | 15 rejets jugés dans une famille, > 25 % montés à +100 % | un bras dont les rejets gagnaient |
| `_relax_from_inactivity` | 300 cycles évalués sans **aucune** entrée | un bras qui ne joue pas du tout |

Le troisième existe parce que les deux premiers sont inatteignables pour un
bras qui n'entre jamais. Ne pas les fusionner : ils ne prouvent pas la même
chose.

Les familles `rugcheck` et `authority` ne sont **jamais** relâchées.

## Ce qu'un ajustement doit prouver

- **Filtres** : `simulate_filters` puis `validate_filter_changes` sur
  `BACKTEST_WINDOW = 20` trades, gain minimum `BACKTEST_MIN_IMPROVEMENT = 0.10`
  et `BACKTEST_MIN_KEPT = 5` trades conservés. Un backtest qui ne garde presque
  rien ne prouve rien.
- **Sorties** : `simulate_exits` / `exit_grid` sur les trades **instrumentés**
  seulement, avec `EXIT_BACKTEST_MIN_COVERAGE = 15` et
  `EXIT_BACKTEST_MIN_CHANGED = 5`. Sous ces seuils, le verdict est
  « indéterminé », et le code doit le dire au lieu d'annuler silencieusement.
- **Tampon de glissement** : recalibré sur le glissement mesuré
  (`measured_slippage`, défaut `DEFAULT_SL_SLIPPAGE = -4.4`), borné par
  `MAX_BUFFER_RELAX_STEP` et `SLIPPAGE_BUFFER_SLACK_PCT`.
- **Comparaison entre bras** : IC95 disjoints. Aucun bras n'est déclaré
  meilleur que le témoin sans séparation franche — sept stratégies comparées
  produisent un gagnant par hasard.

## L'angle mort que ces garde-fous ne couvrent pas

Tous les seuils ci-dessus protègent contre le **manque d'échantillon**. Aucun
ne protège contre une **fuite d'information** : un rejeu qui connaît l'avenir
a l'air excellent sur 15 trades comme sur 500. Avant d'accepter un gain mesuré
sur l'historique, passer par `chasse-au-lookahead`.

## Passage en LIVE

`live_mode_allowed()` : 20 trades papier, WR > 40 %, PF > 1.5. Même satisfait,
le passage reste **impossible** — le module d'exécution n'existe pas, et c'est
délibéré. Ne jamais écrire de code d'exécution réelle depuis ce skill ; toute
demande en ce sens passe par `securite-exploitation` et une décision explicite
du propriétaire.

## Écrire un nouvel ajustement

1. Nommer la preuve exigée et sa taille d'échantillon minimale, en constante
   commentée avec la mesure qui la justifie.
2. Passer par `_bounded_set` / `_relax_set` — jamais par une écriture directe
   dans `ParamsStore`.
3. Retourner une ligne de journal en français qui distingue « ajusté »,
   « rien à ajuster » et « pas assez de données ».
4. Test qui verrouille les trois cas, dont celui de l'échantillon insuffisant.

```bash
python -m unittest tests.test_calibration tests.test_garde_fous tests.test_arms \
  tests.test_slippage_buffer_recalibration tests.test_exit_replay
python -m scripts.audit          # IC95, walk-forward, ce qu'on peut conclure
```
