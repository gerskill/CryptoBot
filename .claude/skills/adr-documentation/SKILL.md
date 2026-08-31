---
name: adr-documentation
description: Écrire un ADR ou mettre à jour la documentation de CryptobBot (docs/adr/, CONTEXT.md, docs/ETAT_DU_PROJET.md, AGENTS.md). Utilise ce skill dès qu'une décision structurante est prise ou remise en cause, qu'un seuil change pour une raison mesurée, qu'un terme du vocabulaire évolue, ou que la demande est « documente ça », « pourquoi ce choix », « mets à jour la doc » — et systématiquement après une modification qui change un comportement décrit dans CONTEXT.md.
---

# ADR et documentation

Layout **mono-contexte** : un seul vocabulaire pour tout le dépôt, bot Python
et dashboard compris. Deux glossaires en parallèle finiraient par diverger.

| document | contenu |
|---|---|
| `CONTEXT.md` (racine) | vocabulaire métier, invariants, pièges connus |
| `docs/adr/NNN-*.md` | une décision d'architecture par fichier |
| `docs/ETAT_DU_PROJET.md` | état opérationnel, mesures, chiffres réels |
| `AGENTS.md` | règles de travail pour les agents |
| `docs/agents/*.md` | issue tracker, triage, docs de domaine |

## Quand écrire un ADR

Après toute décision structurante : un ordre d'évaluation, un choix de seuil
arbitré par une mesure, une source de données abandonnée, un mécanisme de
sécurité. Beaucoup de choix de ce dépôt sont contre-intuitifs — sans leur
justification écrite, ils ressemblent à des bugs et quelqu'un les « corrige ».
Les ADR existants le montrent : score RugCheck inversé (001), watchlist
prioritaire sur le cache (002), score absolu séparé du classement (003), rug
pull évalué en premier (004), coût de sortie sur la jambe finale seulement
(009), désactivation d'un bras seulement sur verdict concluant (011).

## Format

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

`NNN` continue la numérotation existante. Un ADR n'est jamais réécrit après
coup : on en écrit un nouveau qui le remplace, et l'ancien passe en
`Statut : remplacé par NNN`. L'historique des décisions vaut autant que la
décision courante.

## La règle de mesure

Ce dépôt privilégie la mesure sur l'intuition. Quand un ADR arbitre un seuil,
il cite **le chiffre observé et la taille d'échantillon**, et si possible la
commande qui le reproduit. Un ADR sans mesure sur un sujet mesurable est un ADR
incomplet — le dire plutôt que d'inventer un chiffre plausible.

## Entretenir les autres documents

- **`CONTEXT.md`** : mettre à jour dès qu'un terme, un invariant ou un piège
  change. Un nouveau terme métier s'y déclare avant d'apparaître dans le code.
- **`docs/ETAT_DU_PROJET.md`** : ajouter une section datée plutôt que de
  réécrire l'historique ; c'est un journal d'état, pas une spécification.
- Ne jamais laisser un chiffre de doc contredire le code. En cas de doute,
  relancer la commande d'analyse et citer la date de mesure.

Style : français, phrases courtes, le **pourquoi** avant le comment, et le
bug évité cité quand il y en a un.
