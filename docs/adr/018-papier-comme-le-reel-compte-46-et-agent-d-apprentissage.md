# ADR 018 — Liquid : le papier joue comme le vrai compte de 46 $, et chaque bras a un agent d'apprentissage

Date : 2026-11-17. Statut : accepté. Complète les ADR 015 à 017.

## Contexte

Le 17 novembre, Killian demande que les résultats Liquid soient « les vrais, comme
si je te demandais après de passer en réel » : le papier doit se comporter
exactement comme le compte réel (≈ 47 USDC), seulement avec du faux argent.

Le juge a refusé le rapport du 17 novembre. L'arithmétique était juste, mais il a relevé cinq écarts avec le réel :

1. 11 paris « indéterminés » étaient sortis des chiffres. Un vrai compte n'en a
   pas : un pari est gagné ou perdu. Cause : un marché réglé **disparaît** de
   l'API (`outcomeMeta`, `allMids`, `l2Book` et `candleSnapshot` répondent
   absent ou HTTP 500), et sur un prix collé au seuil, nos sources ne s'accordent pas.
2. Cinq bras achetaient le même marché au même prix, chacun comme s'il était seul.
3. 1 000 $ fictifs et 20 $ par pari, contre ≈ 46 $ sur le vrai compte.
4. Aucune taille minimale d'ordre, alors qu'Hyperliquid en impose une.
5. Le drawdown dépendait de l'ordre de l'API (79 paris pour 9 horaires de règlement).

## Décision

- **Un compte de 46 $ par bras** (`paper.py::account`). Chaque mise et ses frais d'achat quittent le
  solde, et la recette du règlement y revient. Un pari que le solde ne couvre pas est refusé
  (`solde_insuffisant`). La mise vaut 22 % de la valeur du compte, soit ≈ 10 $, le minimum.
- **Taille minimale d'Hyperliquid** (`min_shares`) : parts × min(prix, 1 − prix) ≥ 10 $,
  en parts entières. Une autre source donne parts × prix ≥ 10 $, et la documentation
  officielle n'a pas pu être lue [À VÉRIFIER]. La règle la plus sévère est retenue :
  le papier ne doit jamais accepter un ordre que le réel refuserait. Conséquence
  mesurée au premier cycle : 295 refus `sous_taille_minimale`. Un favori à 0,90
  exige 100 parts, soit 90 $, plus que le compte. `pm_quasi_certain` ne peut plus parier.
- **Carnet partagé** (`remaining_asks`) : les parts achetées ce cycle par un bras ne
  sont plus à vendre pour le suivant.
- **Règlement sans « indéterminé »** : une issue illisible laisse le pari ouvert (mise
  engagée) et l'annote `a_regler_depuis`. Après `delai_prudence_heures` (24), il est **compté perdu**
  (`regle_par_prudence`). C'est l'hypothèse qui protège le capital, et elle est signalée dans les chiffres.
- **Ordre stable** : les paris réglés sont triés par heure de règlement, puis heure d'entrée, puis identifiant.
- **Gains nets à côté des gains** : `gagnes_net` et `pertes_reelles`, parce qu'un pari
  gagné à 0,99 perd de l'argent après frais (cas de `pm_quasi_certain`).
- **Agent d'apprentissage** (`learning.py`), un diagnostic par bras, au plus une fois par heure.
  Il mesure chaque bras par tranche de prix, par domaine et par espérance au carnet. Il
  propose un fork par la boîte de demandes (ADR 017) seulement si :
  - le bras a au moins 20 paris réglés ;
  - le segment perdant a au moins 10 paris ;
  - le fork ne change qu'UNE variable, dans les bornes d'`overlay.py` ;
  - le fork ne fait que **resserrer** (fourchette de prix plus étroite, espérance minimale plus haute).

  Il ne modifie jamais un bras existant, n'active ni ne désactive rien, ne forke pas
  ses propres forks, et se limite à 1 fork par bras et par jour, 10 au total.

## Conséquences

- Les chiffres de la première saison (1 000 $, mises de 20 $) ne se comparent pas à
  ceux du compte de 46 $. Ils sont archivés dans `data/prediction/archive/` (non versionné) et
  la nouvelle saison repart de zéro. Les 83 paris encore ouverts de la première saison ne
  sont plus suivis.
- Avec 46 $ et ≈ 10 $ par pari, un pari risque ≈ 22 % du compte. C'est la réalité du compte
  réel, pas un choix de réglage. Les drawdowns en pourcentage seront grands.
- Les frais de règlement (0,9 %) restent comptés par prudence tant que Liquid ne
  les a pas confirmés. Hyperliquid seul facture 0 à l'ouverture et 7 points de base au
  règlement (source tierce) ; la commission propre à Liquid au règlement n'est pas documentée.
- Rien ne passe en réel : verrou du projet (≥ 20 paris, WR > 40 %, PF > 1,5,
  drawdown ≤ 10 %, verdict du juge VALIDÉ, et demande explicite de Killian avec montant).
