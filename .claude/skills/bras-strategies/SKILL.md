---
name: bras-strategies
description: Ajouter, modifier, désactiver ou comparer un bras de stratégie de CryptobBot (config/strategies.json, config/arms/, src/core/arm.py). Utilise ce skill dès qu'il est question d'un bras (baseline, sniper, scalp, runner, quality, narrative, consensus), du manifeste des stratégies, de capital_pct, de bornes par bras, de verdict_vs_reference, de confluence, d'un bras qui ne trade pas, ou de « tester une nouvelle stratégie » — même formulé comme un simple changement de seuil dans un fichier de config.
---

# Bras de stratégie

Sept bras partagent une collecte et sont évalués en CPU pur. Chacun a ses
filtres, ses sorties, son portefeuille, son journal, son shadow log, son
`LearningEngine` et ses bornes.

| bras | âge | liq min | SL | TP1 | hold |
|---|---|---|---|---|---|
| baseline | 1,5-6 h | 25 K | −10 % | +100 % | 4 h |
| sniper | 1m30-1,5 h | 4 K | −40 % | +40 % | 30 min |
| scalp | 30 m-4 h | 5 K | −25 % | +25 % | 1 h |
| runner | 1-12 h | 10 K | −35 % | +150 % | 6 h |
| quality | 4-48 h | 20 K | −25 % | +50 % | 4 h |
| narrative | 6 h-∞ | 40 K | −30 % | +80 % | 24 h |
| consensus | 1m30-24 h | 4 K | −25 % | +50 % | 3 h |

## La règle du manifeste qui piège tout le monde

Les `overrides` de `config/strategies.json` ne s'appliquent **qu'à la création**
de `config/arms/<nom>.json`. Ensuite le fichier appartient au `LearningEngine`
du bras : modifier le manifeste n'a plus aucun effet. Pour repartir du
manifeste, il faut **supprimer le fichier du bras** — ce qui jette aussi ses
paramètres appris. Le dire à l'utilisateur avant de le faire, et sauvegarder
(`./scripts/backup.sh`).

`capital_pct` doit sommer à 1.0, sinon le bot **refuse de démarrer** : du
capital inventé rendrait toute comparaison entre bras fausse. En PAPER chaque
bras dispose de 1000 $ indépendants et `capital_pct` n'est pas appliqué —
sinon un bras à 5 % prendrait des positions dix fois plus petites et
afficherait un P&L moindre pour une raison d'allocation, pas de qualité.

## Ajouter un bras

1. Entrée dans `arms` de `config/strategies.json` : `name`, `role`
   (`voter` ou `consensus`), `enabled`, `capital_pct` (rééquilibrer les autres
   pour retomber à 1.0), `notify` (`all` / `exits` / `none`), `description`
   qui dit **quelle hypothèse le bras teste**, `bounds` si ses bornes doivent
   sortir des `PARAM_BOUNDS` globales, `overrides`.
2. Choisir un **point** sur la courbe âge/liquidité : jeune ET liquide
   n'existe pas (mesuré — âge ≤ 1 h + liq ≥ 15 K = 0 token). Un bras dont la
   fenêtre est vide ne trade pas, ce n'est pas un bug, mais c'est un bras
   inutile.
3. Vérifier que `discovery_envelope` reste assez lâche pour couvrir sa fenêtre.
4. `python -m unittest tests.test_arms tests.test_mise_en_commun`.

## Désactiver un bras

`enabled: false` l'exclut de `bootstrap_arms()` ; son fichier de paramètres et
son journal **restent sur disque**. Ce n'est légitime que sur un
`verdict_vs_reference` **concluant et défavorable** — IC95 entièrement négatif
contre le témoin (ADR 011). Jamais sur une sous-performance simple ni sur un
P&L négatif brut : sept stratégies comparées produisent un perdant apparent par
hasard.

## Le témoin

`baseline` est la référence de `verdict_vs_reference` : sa série de trades est
continue depuis avant le multi-bras. Il n'est **plus gelé** depuis le
2026-08-03 — il apprend comme les six autres, décision explicite du
propriétaire. Ne pas « rétablir » son gel sans le lui demander.

## Confluence

Le bras `consensus` n'entre que si au moins deux bras retiennent le même
candidat (`CONSENSUS_MIN_RATIO`, `src/core/signals.py` — vote avec abstention,
quorum sur les présents seulement). Un bras absent ne compte pas comme un vote
contre.

## Vérifier

```bash
python -m unittest tests.test_arms tests.test_confluence tests.test_mise_en_commun
python -m scripts.analyse_rejets --bras <nom>
python -m scripts.rapport_hebdo --jours 7 --bras <nom>
```
