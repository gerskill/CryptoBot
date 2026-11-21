# 022. Exiger la donnée : une option par bras, désactivée partout

Date : 2026-11-21
Statut : accepté pour le mécanisme ; **activation d'un bras : non décidée**

## Contexte

Invariant du dépôt : « une donnée absente ne rejette jamais ». Il protège la
boucle (un filtre qui lève sur `None` la tuerait) et la comparabilité des bras.

Mesuré le 2026-11-21 sur les entrées du 19/11 (OGTA) : `onchain_status = "error"`,
`entry_bundler_rate`, `entry_insider_rate`, `entry_sniper_count`,
`entry_entrapment_ratio` et `holders_at_entry` tous `None`. Les filtres sur ces
signaux existent (`max_bundler_rate`, `max_insider_rate`…), mais ils ne
s'appliquent que si la valeur est là (`src/pipeline.py`, `_manipulation_reason`) :
quand GMGN ou Helius est en défaut, le candidat passe **sans** ces contrôles, sans
que rien ne le dise. L'alerte « santé des données » (ce même jour) rend le défaut
visible ; elle ne le corrige pas.

## Décision

1. **Mécanisme** : `filters.require_data`, liste de noms parmi `holders`,
   `bundlers`, `insiders`, `snipers`, `entrapment`, `smart_money`
   (`DONNEES_EXIGIBLES` dans `src/pipeline.py`). Pour un bras qui la demande,
   une donnée `None` rejette avec « donnée manquante : <nom> » ; `0` est une
   valeur présente. Absente ou vide (tous les bras aujourd'hui), l'option ne fait
   rien : comportement strictement inchangé.
2. **Désactivée partout.** Aucun fichier de `config/` ne la contient, et
   `tests/test_donnee_requise.py::test_aucun_bras_ne_l_active_sans_adr` échoue si un
   bras l'active sans être listé dans `BRAS_AVEC_DONNEES_REQUISES` : l'activer
   demande un ADR (ou un amendement de celui-ci) et une décision de Killian.
3. **Famille de rejet à part** : `donnee_requise` (`reason_family`), **jamais
   relâchée** par l'apprentissage. Sans branche dédiée, « donnée manquante : holders »
   tombait dans la famille `holders` et l'apprentissage aurait baissé `min_holders`
   pour un rejet qui n'a rien à voir avec ce seuil.
4. **L'invariant 3 reste vrai par défaut** : l'exception est opt-in, bras par bras,
   et nommée dans `CONTEXT.md` et les skills.
5. **Mesurer avant d'activer** : `python -m scripts.analyse_donnees_manquantes`
   compare, par bras et par champ, le P&L des positions entrées sans la donnée et
   avec (intervalles bootstrap, verdict seulement avec 30 positions par groupe et
   intervalles disjoints).

## Pourquoi pas plus

- **Pas d'activation ici** : on ne sait pas encore si les entrées sans donnée valent
  moins que les autres. Rejeter des candidats sans le savoir prive peut-être un bras
  d'entrées rentables, et change sa comparaison au témoin.
- **Pas de blocage global** : un bras qui exige `holders` alors que Birdeye est mort
  ne trade plus du tout ; c'est un choix par bras, pas par défaut.
- **Pas de modification de `config/`** : les fichiers de bras sont réécrits par
  l'apprentissage et décidés par Killian.

## Ce que la mesure ne peut pas dire

- Elle ne voit que les entrées **prises** ; les candidats qu'`require_data` aurait
  rejetés restent inconnus (le shadow trading les suit, mais il arbitre des seuils,
  pas une performance : ADR 007).
- Les groupes « sans » et « avec » sont souvent de **périodes différentes** (une panne de
  source dure des jours) : le script affiche leurs dates et le recouvrement.
- Une même position de token entre dans plusieurs bras : ne pas additionner les bras.

## Suite possible

Si la mesure montre un écart net (intervalles disjoints, ≥ 30 positions par groupe,
périodes qui se recouvrent), proposer à Killian d'activer `require_data` sur **un**
bras d'abord, en shadow (le rejet est tracé, pas encore appliqué), puis de comparer.
