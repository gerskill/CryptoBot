# gbrain — recherche sur les décisions du dépôt

Le dépôt est enregistré comme **source isolée** dans gbrain (déjà branché en
MCP côté Claude Code). Isolée veut dire : interrogée seulement quand on la
nomme, jamais mélangée au reste du cerveau personnel.

```bash
gbrain sources list                       # cryptobot -> chemin du dépôt
gbrain search "<question>" --source cryptobot
gbrain import <chemin-du-depot> --no-embed --source cryptobot   # rafraîchir
```

Exemple qui marche : `gbrain search "rug pull evalue avant le stop loss"
--source cryptobot` rend l'ADR 004 en premier résultat.

## Ce qui est indexé, et ce qui ne l'est pas

29 fichiers markdown : `CONTEXT.md`, `AGENTS.md`, les onze ADR, l'état du
projet, l'audit de sécurité, les docs d'agents, les skills. **Pas** le journal
de trades, pas `data/`, pas le code Python.

## Deux limites à connaître avant de s'appuyer dessus

**Recherche par mots-clés seulement.** Ce cerveau a été initialisé sans
fournisseur d'embeddings : `gbrain search` (tsvector) fonctionne, la recherche
sémantique et `gbrain query` non. Une question posée avec d'autres mots que
ceux du document ne trouvera rien. Pour l'activer :
`gbrain config set embedding_model <fournisseur>:<modèle>` puis un ré-import.

**`gbrain anomalies` ne regarde pas le trading.** Cette commande détecte des
anomalies statistiques dans l'activité des *pages du cerveau* (cohortes par
tag ou par type), pas dans une série de trades. Vérifié le 2026-08-31 : sur
cette source elle signale « 29 notes importées aujourd'hui contre une base à
0 », ce qui est exact et sans rapport avec les bras. Pour détecter une rupture
de régime sur le journal, il faut une mesure écrite dans ce dépôt, pas cette
commande.

## Ce que gbrain ne doit jamais devenir ici

La source unique du vocabulaire reste `CONTEXT.md` (règle mono-contexte, voir
`domain.md`). gbrain **indexe et retrouve**, il ne définit pas. Un second
glossaire divergerait en quelques semaines, et personne ne saurait lequel fait
foi.
