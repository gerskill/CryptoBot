# 005. Mode de structure `balanced` par défaut, pas `strict`

Date : 2026-07-29
Statut : accepté

## Contexte

La spécification exige, pour valider une entrée : « higher highs / higher lows
sur 3 bougies » **et** « volume en croissance sur les 3 dernières bougies ».

Observé : 12 évaluations techniques consécutives, 12 échecs, 0 entrée. Jamais
un seul passage.

Mesure sur **43 memecoins Solana réels**, bougies 1 m Birdeye :

| Critère | Seul | Croisé avec l'expansion de volume |
|---|---|---|
| HH + HL sur 3 bougies (spec littérale) | 2.3% | **0.0%** |
| Closes croissants ×3 | 13.6% | 0.0% |
| Tendance nette `close[-1] > close[0]` | 18.6% | **4.7%** |
| Expansion de volume seule | 39.5% | — |

Le critère de la spec n'est pas sévère : il est **inatteignable**. Un bot dans
ce mode ne prend aucun trade, ne produit aucun point d'apprentissage, et
l'étape d'auto-amélioration ne démarre jamais. Le système entier tourne à vide
indéfiniment.

Deux causes cumulées : exiger 3 higher highs **et** 3 higher lows consécutifs
sur du 1 m est un événement à 2.3% ; exiger le volume croissant bougie par
bougie sur une série bruitée n'arrive quasiment jamais.

## Décision

`entry_rules.structure_mode`, deux valeurs :

- `strict` — la spec à la lettre. Conservée pour référence, documentée à 0%.
- `balanced` — **défaut** : tendance nette `close[-1] > close[0]`.

Le test de volume compare la **moyenne** des 3 dernières bougies à celle des 3
précédentes, au lieu d'exiger 3 hausses consécutives.

Taux joint retenu : 4.7%, soit ~1 entrée toutes les 22 évaluations. À 2-3
candidats par cycle, de quoi accumuler les 20 trades papier de validation.

## Conséquences

- Les taux mesurés sont inscrits dans `STRUCTURE_MODES` — ils doivent être
  re-mesurés si les bougies changent d'intervalle.
- Première entrée réelle obtenue immédiatement après le changement : COPIUM,
  score absolu 93.16, structure `PASS`.
- `balanced` reste sélectif : le premier trade est sorti en `STOP_LOSS -27.2%`
  en une minute. Le critère filtre la structure, pas le risque.
