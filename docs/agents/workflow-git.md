# Workflow git — branches, PR, rangement

Établi le 2026-11-17 après un inventaire : sur 11 branches distantes, 2
portaient du travail non fusionné ; les 9 autres étaient fusionnées ou
remplacées (la synchro du Mac mini, PR #9, avait repris le travail de trois
branches Cursor sans les fermer).

## Qui écrit où

| auteur | préfixe de branche | exemple |
|---|---|---|
| Claude Code | `claude/<sujet>` | `claude/liquid-compte-reel` |
| Cursor / Grok | `cursor/<sujet>` | `cursor/helius-429-5b0b` |
| Killian (Mac mini) | `sync/mac-AAAA-MM-JJ` pour une synchro, sinon `feature/`, `fix/`, `experiment/` | `sync/mac-2026-11-15` |

Le préfixe d'agent est **gardé** : il dit qui a produit la branche, ce qui
compte quand deux agents travaillent en parallèle. Le sujet dit le quoi.
`experiment/` signale une branche qui n'a pas vocation à être fusionnée telle
quelle (un bras test, une mesure ponctuelle).

## Règles

1. **Rien ne va sur `main` sans PR**, y compris la synchro du Mac mini.
2. **Une PR = un sujet.** Une PR construite sur la branche d'une autre (cas
   #12 → #13) le dit dans sa description : « fusionner #12 d'abord ».
3. **Avant de fermer une PR comme remplacée**, vérifier fichier par fichier
   que `main` contient le travail (`git diff origin/main origin/<branche> -- <fichier>`),
   et écrire la preuve en commentaire de la PR.
4. **Une branche fusionnée est supprimée.** Réglage GitHub à activer une fois :
   *Settings → General → Pull Requests → Automatically delete head branches*.
5. **Les tests passent avant fusion** :
   `python -m unittest discover -s tests` et, si le dashboard est touché,
   `cd dashboard && npm test && npm run build && npm run lint`.
6. **Pas de chiffre de performance dans une PR sans le juge** (`CLAUDE.md`).

## Protection de `main` (à régler dans GitHub, une fois)

*Settings → Branches → Add branch ruleset* sur `main` :

- *Require a pull request before merging* (0 approbation requise : Killian
  est seul à fusionner, l'obligation sert à garder une trace) ;
- *Block force pushes* ;
- *Restrict deletions*.

Pas d'exigence de CI tant qu'aucune action GitHub n'existe : une règle
« checks obligatoires » sans check bloquerait toute fusion.

## Labels de PR

Les mêmes noms que le tracker local (`triage-labels.md`), sans en inventer :
`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`.
Sur une PR, `ready-for-human` signifie « relue, prête à fusionner par Killian ».

## Ménage

```bash
git fetch --prune origin                     # oublier les branches supprimées
git branch -r --merged origin/main           # candidates à la suppression
```

Supprimer une branche distante : `git push origin --delete <branche>`.
