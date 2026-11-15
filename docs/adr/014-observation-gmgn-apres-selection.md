# 014. Observation GMGN après sélection, sans décision

Date : 2026-11-08
Statut : accepté

## Contexte

La boucle interroge déjà GMGN pour le classement, les bougies et le smart
money. Une vérification de plus — risques de contrat, concentration,
wallets étiquetés bundler / rat / sniper / dev — ne doit ni ralentir une
entrée ni devenir un filtre avant d'avoir été mesurée.

Le 8 novembre 2026 à 16:27 (Zurich), deux commandes ont reçu
`RATE_LIMIT_BANNED`, levée annoncée 17:27:31. La cause n'est pas établie.
Le client de la boucle limite à une soixantaine d'appels par minute : en
rajouter un second au même rythme augmenterait la pression.

## Décision

Un processus séparé, `scripts/gmgn_observe.py`, lit `data/state.json` et
écrit seulement dans `data/gmgn_observe/`. Il ne voit que les candidats
sans motif de rejet et les positions déjà ouvertes. Au premier passage, tout
ce qui est déjà là est ignoré. Il n'y a pas de rejeu de l'historique.

Trois lectures au plus par heure : `token security`, `token holders`,
`token traders`, une fois par mint, cache 24 h. Un 429 ou une erreur
d'authentification arrête tout de suite, sans seconde commande. Le premier
fichier d'état pose le cooldown jusqu'au 8 novembre 2026, 17:27:31 Zurich,
donc un lancement pendant ce créneau ne crée pas de sous-processus.

Le CLI est `gmgn-cli` en argv, sans shell. Les variables `GMGN_*` ne sont
pas lues ni transmises : le CLI charge `~/.config/gmgn/`. Aucun `swap`,
`order`, `cooking`, ni `config --apply`.

Un champ absent reste inconnu. `pretrade_guaranteed` est toujours faux :
`state.json` est un instantané écrasé, et une position veut dire que
l'entrée a déjà eu lieu.

## Conséquences

- La boucle n'est pas redémarrée et n'attend pas ces fichiers.
- Le plafond de 3 appels/h ne se soustrait pas au quota de la boucle. Les
  deux processus partagent le compte GMGN chez le fournisseur.
- Aucun drapeau documenté ne coupe le retry interne du CLI. Le processus
  n'est lancé qu'une fois, avec un délai de 15 s.
- Arrêt : Ctrl-C. Les données restent. Retirer le code : `git apply -R` sur
  le patch. Ne pas effacer `data/gmgn_observe/` pour annuler.
