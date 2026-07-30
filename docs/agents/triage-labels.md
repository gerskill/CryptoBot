# Vocabulaire de triage

Ce repo utilise les noms canoniques, sans renommage. Un skill qui applique
un état écrit exactement la chaîne de la colonne « valeur ».

| Rôle | Valeur | Signification |
|---|---|---|
| À évaluer | `needs-triage` | Personne n'a encore qualifié l'issue |
| Info manquante | `needs-info` | En attente d'une précision du rapporteur |
| Prête pour un agent | `ready-for-agent` | Assez spécifiée pour être traitée sans contexte humain |
| Prête pour un humain | `ready-for-human` | Demande une décision ou une intervention humaine |
| Abandonnée | `wontfix` | Ne sera pas traitée |

## Où l'état est écrit

Dans le champ `status` de l'en-tête YAML de chaque fichier d'issue —
voir [issue-tracker.md](./issue-tracker.md). Pas de système de labels
séparé : le fichier porte son propre état.

## Distinction à ne pas rater

`ready-for-agent` signifie qu'un agent peut prendre l'issue **sans aucun
contexte supplémentaire** : reproduction, attendu, constaté et critère de
réussite sont tous écrits. Une issue qui exige « demande à Killian ce qu'il
voulait dire » n'est pas `ready-for-agent`, elle est `needs-info`.
