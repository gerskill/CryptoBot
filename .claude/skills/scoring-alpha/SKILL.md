---
name: scoring-alpha
description: Modifier le scoring alpha de CryptobBot — composants, poids, saturations, seuil d'entrée (src/core/scoring.py). Utilise ce skill dès qu'il est question de score, de note d'un candidat, de pondération, d'ajouter un signal au score, de alpha_score vs alpha_score_absolute, de seuil d'entrée, ou d'un candidat « bien noté mais mauvais ». La confusion entre score absolu et score de classement est le bug le plus coûteux du dépôt : ne pas traiter cette demande sans ce skill.
---

# Scoring alpha

## Deux nombres, jamais interchangeables (ADR 003)

- `alpha_score_absolute` — seuils fixes, comparable d'un scan à l'autre.
  **Seul lui autorise une entrée.**
- `alpha_score` — `0.6 × absolu + 0.4 × rang dans le lot courant`.
  **Il ne sert qu'à trier les candidats entre eux.**

Comparer `alpha_score` à `scan.alpha_score_entry_threshold` fait entrer le bot
sur le moins mauvais déchet d'une nuit creuse : dans un lot pourri, le rang du
premier vaut 100. Toute nouvelle lecture de score commence par se demander
laquelle des deux grandeurs est pertinente, et le nom de variable doit le dire.

## Composants et redistribution

`COMPONENTS` associe chaque composant à sa fonction absolue. Un composant qui
retourne `None` est **exclu**, et son poids est **redistribué** sur les autres :
le score reste sur 100. `sub_scores._weights_used` dit quelle fraction du
barème est réellement couverte — c'est l'indicateur à lire avant de conclure
qu'un score est bon.

Un nouveau composant se branche ainsi :

1. Une fonction `<nom>_absolute(candidate) -> Optional[float]` bornée 0-100,
   qui retourne `None` quand la donnée manque — jamais `0.0`, qui serait un
   jugement « mauvais » là où on n'a rien mesuré.
2. Une constante de saturation explicite (`X_SATURATION_...`), commentée avec
   la mesure qui la justifie.
3. Une entrée dans `COMPONENTS` et un poids dans `params.json` / les overrides
   de bras — la somme des poids reste cohérente pour tous les bras.
4. Un test qui verrouille : donnée absente → composant exclu, poids redistribué,
   score toujours sur 100.

## Saturations en place

`LIQUIDITY_SATURATION_USD = 150_000`, `VOLUME_RATIO_SATURATION = 3.0`,
`SOCIAL_SATURATION_MENTIONS/AUTHORS/ENGAGEMENT`, `SPAM_RATIO_FLOOR = 0.5`,
`SMART_MONEY_SATURATION_BUYS = 10`,
`WALLET_RELIABILITY_SATURATION_HIT_RATE = 40.0`.

Elles existent pour qu'un token à 3 M$ de liquidité ne domine pas le classement
par une seule dimension. Relever une saturation, c'est rendre le score plus
sensible aux valeurs extrêmes : le justifier par une mesure, pas par « ça
paraît bas ».

## Ce que le score ne peut pas faire

- **RugCheck ne discrimine pas les tokens jeunes** : sous une heure, le score
  sort quasi systématiquement à 99. C'est un garde-fou, pas un signal de
  classement — ne pas augmenter son poids en espérant du tri.
- **`smart_money_buys_30m` est toujours `None`** (GMGN n'a pas d'API publique) :
  le composant est exclu à chaque scan. Un poids qu'on lui donne est en réalité
  redistribué ailleurs.

## Vérifier

```bash
python -m unittest tests.test_core tests.test_confluence
python -m scripts.analyse_rejets --heures 24
```

Un changement de poids se juge sur les trades, pas sur un candidat isolé — et
l'apprentissage n'a le droit de repondérer qu'à partir de 50 trades
(`MIN_TRADES_FOR_WEIGHTS`). Voir `apprentissage-garde-fous`.
