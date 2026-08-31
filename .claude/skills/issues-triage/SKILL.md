---
name: issues-triage
description: Créer, lister, trier ou fermer une issue de CryptobBot dans le tracker markdown local (.scratch/). Utilise ce skill dès que l'utilisateur signale un bug, une anomalie, une idée à garder pour plus tard, demande « note ça », « où en est ce problème », « qu'est-ce qui reste à faire », ou qu'un travail en cours révèle un défaut hors périmètre — plutôt que de laisser le constat se perdre dans la conversation.
---

# Issues et triage

Les issues vivent dans des fichiers markdown sous `.scratch/`. Aucun service
externe, aucun compte : tout reste sur la machine. `.scratch/` est **gitignoré**
— ce sont des notes de travail, pas de la documentation livrée.

## Emplacement et nommage

```
.scratch/<feature>/NNN-titre-en-kebab-case.md
```

`<feature>` regroupe par thème (`dashboard`, `learning`, `execution`,
`pipeline`…), `NNN` est un numéro à 3 chiffres incrémenté **par feature**.

## Format

```markdown
---
title: Le SL sort 2 points sous son seuil
status: needs-triage
created: 2026-07-30
---

## Contexte
SL réglé à -25%, sortie effective à -27.2%.

## Attendu
Sortie au plus proche de -25%.

## Constaté
L'échantillonnage à 20s laisse passer 2 points de prix.
```

## Vocabulaire de triage — écrire exactement ces chaînes

| valeur | signification |
|---|---|
| `needs-triage` | personne n'a encore qualifié l'issue |
| `needs-info` | en attente d'une précision du rapporteur |
| `ready-for-agent` | assez spécifiée pour être traitée **sans aucun contexte humain** |
| `ready-for-human` | demande une décision ou une intervention humaine |
| `wontfix` | ne sera pas traitée |

Pas de renommage, pas de système de labels séparé : l'état vit dans le champ
`status` de l'en-tête YAML.

**La distinction à ne pas rater** : `ready-for-agent` signifie que
reproduction, attendu, constaté et critère de réussite sont **tous écrits**.
Une issue qui exige « demande à Killian ce qu'il voulait dire » n'est pas
`ready-for-agent`, elle est `needs-info`. Se tromper là-dessus fait démarrer un
agent sur une consigne incomplète.

## Opérations

| action | comment |
|---|---|
| créer | écrire le fichier avec `status: needs-triage` |
| lister | parcourir `.scratch/**/*.md`, filtrer sur `status` |
| changer d'état | modifier le champ `status` |
| fermer | `status: wontfix`, ou déplacer sous `.scratch/<feature>/done/` |

## La promotion en ADR

Une issue qui mérite d'être conservée durablement **ne reste pas dans
`.scratch/`** : elle devient un ADR dans `docs/adr/` (voir `adr-documentation`).
`.scratch/` disparaît avec la machine ; une décision, non.

Quand une mesure existe, la citer dans l'issue avec la commande qui la
reproduit — c'est ce qui permet de la traiter des semaines plus tard.
