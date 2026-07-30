# Issue tracker — markdown local

Les issues de ce repo vivent dans des fichiers markdown sous `.scratch/`.
Aucun service externe, aucun compte : tout reste sur la machine.

Ce repo n'a pas de remote GitHub. Si un remote est ajouté plus tard et que tu
veux basculer sur GitHub Issues, relance `/setup-matt-pocock-skills`.

## Emplacement et nommage

```
.scratch/<feature>/NNN-titre-en-kebab-case.md
```

- `<feature>` : regroupement thématique (`dashboard`, `learning`, `execution`…)
- `NNN` : numéro à 3 chiffres, incrémenté par feature (`001`, `002`…)

## Format d'une issue

En-tête YAML obligatoire, puis le corps en markdown libre :

```markdown
---
title: Le SL sort 2 points sous son seuil
status: needs-triage
created: 2026-07-30
---

## Contexte
COPIUM : SL réglé à -25%, sortie effective à -27.2%.

## Attendu
Sortie au plus proche de -25%.

## Constaté
L'échantillonnage à 20s laisse passer 2 points de prix.
```

Les valeurs autorisées pour `status` sont définies dans
[triage-labels.md](./triage-labels.md).

## Opérations

| Action | Comment |
|---|---|
| Créer | Écrire le fichier avec `status: needs-triage` |
| Lister | Parcourir `.scratch/**/*.md`, filtrer sur `status` |
| Changer d'état | Modifier le champ `status` de l'en-tête |
| Fermer | `status: wontfix`, ou déplacer sous `.scratch/<feature>/done/` |

## Règle importante

`.scratch/` est dans `.gitignore` : les issues ne partent jamais dans les
commits. C'est volontaire — ce sont des notes de travail, pas de la
documentation livrée. Si une issue mérite d'être conservée durablement,
elle doit devenir un ADR dans `docs/adr/`.
