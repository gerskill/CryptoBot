# 016. Bras de prédiction par famille, et site en deux espaces

Date : 2026-11-16
Statut : accepté

## Contexte

L'ADR 015 a posé le moteur de paris papier. Deux constats le même jour.

1. **La lecture des marchés était fausse sur l'API réelle.** Écrite d'après la
   documentation, elle n'attendait que l'écriture
   `class:priceBinary|underlying:…`. La réponse réelle de `outcomeMeta`
   (relevée depuis le Mac mini le 2026-11-16, 309 marchés, 31 questions)
   utilise presque partout `name: "template:<modèle>"` avec des champs propres
   à chaque modèle. Résultat : 4 marchés lus sur 309.
2. **Killian veut des bras qui classent le marché** (sport, prix,
   pourcentages, questions) et un site avec un espace memecoins et un espace
   Liquid distincts.

Relevé réel des modèles : `binaryPrice` (104), `binaryPriceExternal` (1),
`Recurring priceBinary` (4), `priceTouch` (16), issues de matchs (75 + 30
matchs à deux côtés), issues de tournois (26), `companyIpo*` (16),
`policyRate*` (3), tranches de prix (3), marchés de repli (31).

## Décision

**Domaines.** Chaque marché reçoit un domaine, déduit de son modèle :
`crypto_seuil`, `actions_seuil`, `matieres_seuil`, `barriere`, `tranche_prix`,
`sport_match`, `sport_tournoi`, `evenement`. Un modèle inconnu va en `autre`
et n'est jamais parié. Une pièce `xyz:…` n'est jamais de la crypto.

**Familles de bras.** Un bras appartient à une famille et ne parie que sur
ses domaines :

| famille | bras | règle |
|---|---|---|
| transverse | `pm_temoin`, `pm_coherence`, `pm_consensus` | référence, surveillance, confluence |
| prix | `pm_vol_edge` → `pm_vol_normal`, `pm_cloture_actions`, `pm_matieres` ; `pm_barriere` | estimation sur l'historique du sous-jacent |
| sport | `pm_sport_favori` → `pm_sport_outsider` ; `pm_sport_reference` (désactivé) | règle fixe sur le prix, avant le coup d'envoi |
| questions | `pm_question_favori` | règle fixe sur le prix |
| pourcentages | `pm_temoin` → `pm_quasi_certain`, `pm_pile_ou_face` | même règle, autre tranche de probabilité |

« → » = fork : `parent` et `variable` sont écrits dans la configuration, une
seule variable change. Nouveau rôle `regle` : le bras achète le favori ou
l'outsider dans une fourchette de prix, sans estimation maison.

**Sport et questions sans estimation maison.** Aucune source de cotes ni de
sondages n'est branchée. Les bras de ces familles testent donc une seule
hypothèse mesurable sans donnée extérieure : le biais favori-outsider.

**Garde-fous ajoutés d'après le relevé réel.**
- Un marché affiché à 50 % peut avoir un carnet à 1 % / 99 % : entrée refusée
  si le prix payé dépasse le prix milieu de plus de 5 points
  (`carnet_trop_large`).
- Pas d'entrée après le coup d'envoi (`scheduledStart`).
- Les issues d'une question sont regroupées par identifiant de question, plus
  par nom : des dizaines de questions portent le même nom.
- `settledNamedOutcomes` n'est cru que si le dernier prix dit la même chose,
  tant qu'un vrai règlement n'a pas confirmé son sens.

**Tout enregistrer.** Chaque cycle écrit `catalogue.json` (tous les marchés et
leurs prix) et `perps.json` (marchés à terme, affichage seul). Toutes les
15 minutes, un relevé du prix de tous les marchés est ajouté à
`snapshots/AAAA-MM-JJ.jsonl`. Sans ces relevés, on ne peut pas mesurer plus
tard si un prix de 80 % gagne 80 % du temps.

**Site en deux espaces.** Un sélecteur « Memecoins | Liquid » au-dessus des
onglets. L'espace Liquid a cinq onglets : aperçu et bras, prédictions,
marchés, paris et graphique, incohérences. Son en-tête ne montre aucun P&L
global : les bras ne partagent pas de capital.

## Conséquences

- `sport_tournoi` et `tranche_prix` sont relevés mais jamais pariés
  (règlement en 2027 ; ordre des tranches non documenté).
- Les perpétuels sont affichés, aucun bras ne les trade. Un bras papier sur
  perpétuels serait une décision à part.
- Le règlement reste reconstitué. Le premier règlement réel d'un match dira si
  `settledNamedOutcomes` liste bien les gagnants.
- L'extrait réel est gardé dans `tests/fixtures/outcome_meta_2026-11-16.json` :
  un changement de format de l'API cassera un test au lieu de vider le journal.
