# 007. Suivi des rejets et bornes sur les paramètres

Date : 2026-07-30
Statut : accepté

## Contexte

Deux défauts structurels de la boucle d'auto-amélioration, indépendants mais
qui se renforcent.

**Biais du survivant.** Le bot n'apprend que des trades qu'il a pris. Un
candidat rejeté ne produit aucune donnée. Le seul signal disponible est donc
« mes trades perdent → resserrer ». Le système ne peut structurellement pas
découvrir qu'un filtre est **trop strict**.

**Cliquet monotone.** Toutes les règles d'ajustement de la spec ne font que
durcir : `min_age +0.5`, `max_age -0.5`, `min_liquidity +5000`. Aucune ne
relâche. Sur quelques semaines, les filtres convergent vers « plus aucun
candidat ne passe » et le bot s'arrête de trader définitivement.

Combinés : le bot resserre sur du bruit, ne peut jamais corriger, et se
paralyse.

## Décision

**`ShadowTracker`** enregistre chaque rejet avec son prix, puis revient
mesurer ce que le token est devenu sur 4 h. Si plus de 25% des rejets d'une
famille de motif auraient atteint +100%, le filtre correspondant est relâché.

Les familles `rugcheck` et `authority` ne sont **jamais** suivies : on ne
relâche pas un filtre honeypot, quel que soit le manque à gagner.

**`PARAM_BOUNDS`** borne chaque paramètre ajustable. `_bounded_set()` clampe
et retourne `None` si la valeur ne bouge pas — un paramètre à sa borne cesse
simplement d'être ajusté.

**Validation par backtest (étape 6.4)** : un ajustement de filtres rejoue les
20 derniers trades. Trois issues — validé si le P&L par trade gagne ≥ 10%,
annulé sinon, annulé aussi si moins de 5 trades survivent aux nouveaux
filtres.

## Conséquences

- Les shadow trades sont **fictifs** : pas de slippage, et une entrée réelle
  aurait bougé le prix sur 20K de liquidité. Ils arbitrent des seuils, ils ne
  mesurent pas une performance.
- Le backtest ne rejoue que les **filtres d'entrée**. Les règles de sortie
  demanderaient la trajectoire de prix de chaque position, que le journal ne
  stocke pas. Un changement de `stop_loss_pct` s'applique donc sans preuve.
- Un durcissement n'est mesurable que sur des trades qui passaient l'ancien
  seuil. Des perdants sous le filtre d'origine sont invisibles au backtest.
- Le shadow tracking alimente l'apprentissage bien plus vite que les trades :
  ~7 rejets par scan contre 1 à 5 trades par heure.
