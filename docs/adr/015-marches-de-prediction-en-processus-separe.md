# 015. Marchés de prédiction Liquid : processus séparé, papier, lecture seule

Date : 2026-11-16
Statut : accepté

## Contexte

Le 16 novembre 2026, Killian a connecté Liquid, qui donne accès aux marchés de
prédiction HIP-4 d'Hyperliquid (seuils de prix crypto et actions, barrières,
sport, événements). Le compte Liquid est réel (environ 47 USDC). Le dépôt est
en mode PAPER et le juge refuse tout travail branché sur un compte réel.

L'API publique d'Hyperliquid (`POST /info`) sert ces marchés sans clé :
`outcomeMeta`, `allMids`, `l2Book`, `candleSnapshot`.

## Décision

Un processus séparé, `scripts/run_prediction.py`, sur le modèle de
`scripts/gmgn_observe.py` (ADR 014). Il lit l'API publique et n'écrit que dans
`data/prediction/`. La boucle memecoin ne le connaît pas et ne peut pas tomber
à cause de lui.

Aucun code d'exécution n'existe : pas d'appel à `/exchange`, pas de clé, pas de
wallet. Le connecteur Liquid de Claude n'est pas utilisé par le bot.

Un pari simulé :

- entre au prix MOYEN du carnet vendeur pour la mise demandée, jamais au prix
  milieu affiché ; carnet trop mince = pas de pari ;
- paie 0,90 % à l'achat (annoncé par Liquid) et 0,90 % au règlement (non
  confirmé, compté par prudence, réglable) ;
- est tenu jusqu'au règlement : une ligne par pari dans `bets.jsonl` ;
- est réglé à partir de deux sources, la bougie 1 min du sous-jacent à
  l'échéance et le dernier prix du pari. Désaccord, ou prix à moins de 0,05 %
  du seuil : le pari est « indéterminé » et sort de tous les chiffres.

Les bras vivent dans `config/prediction_arms.json`, à capital fictif. Un bras
qui dérive d'un autre ne change qu'une variable.

## Conséquences

- Les formes de réponse de l'API viennent de la documentation publique, pas
  d'un appel réel : `--sonde` est la première commande à lancer sur le Mac.
- Le règlement est une reconstitution, pas le règlement officiel. Le taux de
  paris indéterminés est un chiffre à surveiller ; s'il est élevé, brancher
  l'endpoint de règlement d'Hyperliquid.
- Le nom de classe des marchés « touche » n'est pas connu : `domain_of` les
  reconnaît à « touch » ou « barrier » dans la classe. À confirmer par la sonde.
- Les bougies des actions (NVDA, MU…) ne sont peut-être pas servies sous ce
  nom : `pm_cloture_actions` ne parie alors pas, et le compte le dit
  (`historique_insuffisant`).
- Aucun résultat de ces bras ne vaut avant 30 paris réglés et le verdict du
  juge.
