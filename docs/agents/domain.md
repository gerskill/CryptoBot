# Documents de domaine

Layout **mono-contexte** : un seul vocabulaire pour tout le repo.

| Document | Emplacement | Contenu |
|---|---|---|
| Contexte | `CONTEXT.md` (racine) | Vocabulaire métier, invariants, pièges connus |
| Décisions | `docs/adr/` (racine) | Une décision d'architecture par fichier |

Le bot Python (`src/`, `api/`) et le dashboard (`dashboard/`) sont deux stacks
mais un seul domaine : « candidat », « position », « score alpha absolu »,
« shadow trade » désignent la même chose des deux côtés. Les séparer
créerait deux glossaires à maintenir en parallèle.

## Règles de lecture

**Avant d'écrire du code** — lire `CONTEXT.md`. Il fixe le vocabulaire.
Un agent qui invente ses propres termes casse la cohérence du projet.

**Avant de modifier une décision d'architecture** — chercher l'ADR
correspondant dans `docs/adr/`. Beaucoup de choix de ce projet sont
contre-intuitifs et **mesurés** : ils ont l'air d'erreurs sans leur
justification. Exemples : le rug pull évalué avant le stop loss, le mode de
structure `balanced` plutôt que `strict`, la séparation score absolu /
score de classement.

**Après avoir pris une décision structurante** — écrire un ADR. Format :

```
docs/adr/NNN-titre-court.md

# NNN. Titre

Date : YYYY-MM-DD
Statut : accepté | remplacé par NNN | abandonné

## Contexte
Le problème, avec les mesures s'il y en a.

## Décision
Ce qui a été choisi.

## Conséquences
Ce que ça coûte, ce que ça empêche, ce qu'il faudra revoir.
```

## Règle de mesure

Ce projet privilégie la mesure sur l'intuition. Quand un ADR arbitre un
seuil ou un critère, il cite le chiffre observé et la taille d'échantillon.
Un ADR sans mesure sur un sujet mesurable est un ADR incomplet.
