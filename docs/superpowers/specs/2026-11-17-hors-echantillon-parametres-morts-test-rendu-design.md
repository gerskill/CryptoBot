# Conception — validation hors échantillon (A2), paramètres morts (A3), test de rendu (A6)

Date : 2026-11-17. Auteur : Claude, avec Killian. Mode PAPER uniquement.
Origine : `docs/FEUILLE_DE_ROUTE.md`, lignes A2, A3, A6, choisies par Killian.

## Intention

Trois défauts trouvés en relisant le dépôt, sans lien entre eux :

- **A2** — l'apprentissage choisit ses règles de sortie sur des positions passées
  puis les juge **sur ces mêmes positions**. Un choix fait ainsi gagne toujours
  sur le papier, sans rien prédire. C'est la seule fuite que les garde-fous
  chiffrés du dépôt (tailles d'échantillon, bornes) n'attrapent pas
  (skill `chasse-au-lookahead`, fuite n°1).
- **A3** — `entry_rules.min_liquidity_lock_days` (30 jours) figure dans
  `config/params.json` et les 18 fichiers `config/arms/*.json`, mais aucun
  code ne le lit. Le lecteur croit qu'un filtre protège ; il n'existe pas.
- **A6** — le panneau « P&L par bras » (PR #14) ne doit jamais afficher un
  chiffre sans le bras qui le porte. Le serveur le garantit
  (`tests/test_arm_performance.py`), l'affichage non.

## Objectifs, non-objectifs, anti-objectifs

**Objectifs**
- A2 : un couple (stop loss, TP1) trouvé par la grille n'est appliqué que s'il
  gagne aussi sur des positions **plus récentes** que celles qui ont servi à le
  choisir.
- A3 : tout paramètre d'entrée ou de sortie déclaré est soit lu par le code,
  soit inscrit dans une liste explicite « non appliqué », avec sa raison — et
  un test le vérifie pour les paramètres futurs.
- A6 : `npm test` existe dans `dashboard/` et vérifie que chaque ligne du
  panneau nomme son bras.

**Non-objectifs**
- Pas de verrou hors échantillon sur les **filtres** : leurs règles sont à
  seuil unique (pas de grille). On **mesure** seulement (voir A2.5).
- Pas de vérification on-chain de la durée de verrou LP (A3).
- Pas de refonte de `_adjust_exits` (règles fixes) ni de `validate_exit_changes`.

**Anti-objectifs (échec même si ça « marche »)**
- Un apprentissage des sorties **figé pour toujours** : le dépôt a déjà dû
  débloquer un apprentissage qui ne pouvait rien faire (`_search_exits`,
  docstring). « Indéterminé » doit rester l'exception tant que les données
  manquent, pas l'état permanent.
- Toucher aux fichiers `config/params.json` ou `config/arms/*.json` : ils sont
  suivis par git **et réécrits par l'apprentissage sur le Mac mini** (dernière
  synchro `b691db4`, 15/11). Les modifier ici provoque un conflit au prochain
  `git pull` du Mac.
- Un découpage passé/futur fait sur une liste non triée : la validation
  serait fausse sans le signaler.

## Contraintes

- `CLAUDE.md`, `CONTEXT.md` et `AGENTS.md` s'appliquent : PAPER, français,
  commentaires qui expliquent le pourquoi, tests nommés d'après le bug.
- Invariants : donnée absente ne rejette jamais ; écriture de paramètre via
  `_bounded_set` seulement ; trois messages distincts « ajusté », « rien à
  ajuster », « pas assez de données ».
- Suite : `python -m unittest discover -s tests` (1242 tests aujourd'hui) doit
  rester verte ; `cd dashboard && npm run build && npm run lint` aussi.
- Aucun chiffre de performance annoncé sans le sous-agent juge.

## Approches considérées

### A2

| approche | ce qu'elle coûte | retenue ? |
|---|---|---|
| **① Hold-out chronologique sur la recherche de sorties** : choisir sur les 2/3 anciens, confirmer sur le 1/3 récent ; filtres : mesure seulement | les sorties bougent moins souvent | **oui — choix de Killian** |
| ② Walk-forward 3 plis + `overfit_gap` comme verrou | tranches de test de 2 à 6 trades par bras aujourd'hui → presque tout « indéterminé » ; seuil d'écart arbitraire (0,5 $/trade dans `audit.py`) | non |
| ③ Observation seule, aucun verrou | aucune protection immédiate | non |

### A3

Seule voie raisonnable : ne pas modifier les fichiers de config (anti-objectif),
déclarer les paramètres non appliqués dans le code et verrouiller par un test.
Écartées : retirer la clé des 19 fichiers (conflit git sur le Mac) ;
vérification on-chain (quota Helius épuisé, effort élevé).

### A6

| approche | coût | retenue ? |
|---|---|---|
| **vitest + jsdom + Testing Library** en dépendances de développement, `npm test` | 3-4 paquets de dev, `npm ci` sur le Mac | **oui — choix de Killian** |
| Test navigateur Playwright piloté depuis Python | lourd, dépend d'un navigateur installé sur le Mac | non |

## Conception — A2 (hold-out sur la recherche de sorties)

Fichier : `src/core/learning.py`, méthode `_search_exits`. Tests :
`tests/test_hors_echantillon.py` (nouveau). Lire d'abord les skills
`apprentissage-garde-fous` et `chasse-au-lookahead`.

### Aujourd'hui

1. `positions = self._instrumented()` — positions du bras (50 dernières) puis
   empruntées au pool (50 dernières, filtrées par `_in_window`). **Pas dans
   l'ordre chronologique** : celles du bras d'abord, puis les empruntées.
2. Si moins de `EXIT_BACKTEST_MIN_COVERAGE` (15) : rien.
3. Si plus de 25 % de positions « dégradées » (ordre pic/creux deviné) : rien.
4. `exit_grid(positions, current)` : 36 couples, triés par P&L/trade.
5. Premier couple qui change au moins `EXIT_BACKTEST_MIN_CHANGED` (5)
   positions et respecte les bornes du bras ; appliqué s'il gagne au moins
   `BACKTEST_MIN_IMPROVEMENT` (10 %) sur la référence — **mesuré sur les mêmes
   positions**.

### Demain

Les étapes 1 à 3 restent identiques, puis :

**A2.1 — Tri chronologique.** Trier les positions par instant de clôture
(`src.core.stats.exit_datetime`, qui lit `timestamp_exit` et traite une date
sans fuseau comme UTC). Une position **sans date lisible** est **exclue du
découpage** (ni apprentissage ni test) : la placer d'un côté ou de l'autre
risquerait de mettre du futur dans l'apprentissage. Le nombre d'exclues figure
dans la ligne de journal.

**A2.2 — Découpage.** `apprentissage` = les `ceil(2n/3)` plus anciennes,
`test` = les autres (les plus récentes). Constante commentée
`HOLDOUT_TRAIN_FRACTION = 2 / 3` : un tiers en test donne au moins 5 positions
dès le seuil de couverture actuel (15), soit le minimum `EXIT_BACKTEST_MIN_CHANGED`
déjà utilisé pour juger un rejeu.

**A2.3 — Sélection sur l'apprentissage seulement.** Même règle qu'aujourd'hui
(étape 5 : changement minimal, bornes, gain ≥ 10 % sur la référence), mais
`exit_grid` et la référence sont calculés **sur `apprentissage`**.

**A2.4 — Confirmation sur le test.** Rejouer sur `test` le couple choisi et
les règles actuelles (`simulate_exits`). Trois issues, chacune avec sa ligne
de journal en français (format `🧠 …`, comme le code existant) :

| issue | condition | effet |
|---|---|---|
| **confirmé** | le couple change ≥ `EXIT_BACKTEST_MIN_CHANGED` positions du test **et** gagne ≥ 10 % de \|P&L/trade de référence du test\| | appliqué via `_bounded_set` ; la raison donne apprentissage (n, avant → après) **et** test (m, avant → après) et l'écart apprentissage − test |
| **rejeté hors échantillon** | assez de positions changées en test, mais gain insuffisant | rien n'est modifié |
| **indéterminé** | moins de `EXIT_BACKTEST_MIN_CHANGED` positions changées en test | rien n'est modifié ; message distinct de « rejeté » |

« Indéterminé » ne modifie rien : la recherche **propose** un changement, et
ne pas savoir ne justifie pas de bouger. (À l'inverse, `validate_exit_changes`
ne revient pas en arrière quand il ne sait pas — les deux règles disent la
même chose : le statu quo par défaut.)

Le verdict du dernier passage est aussi exposé dans un attribut lisible par
les tests (nom au choix du builder, par ex. `self.last_exit_search`), pour que
les trois issues soient testables sans capturer la sortie console.

**A2.5 — Filtres : mesure seulement.** Dans `validate_filter_changes`, quand
le verdict est « filtres validés », ajouter au message l'effet sur le tiers
le plus récent de la fenêtre (même découpage chronologique, `simulate_filters`
avant/après sur ce tiers), par ex. « hors échantillon (17 récents) : P&L/trade
a → b ». **Aucun changement de décision.** Si le tiers récent est vide, ne
rien ajouter.

**A2.6 — `scripts/audit.py`.** `section_walk_forward` découpe
`journal.read_positions()` sans tri explicite : trier par instant de clôture
avant `walk_forward`, même raison qu'A2.1.

### Ce qui ne change pas

`_adjust_exits`, `validate_exit_changes`, `_instrumented` (sauf si le builder
préfère y faire le tri — le contrat est que `_search_exits` travaille sur une
liste triée), les grilles `SL_GRID`/`TP1_GRID`, les bornes, les cadences.

### Tests attendus (`tests/test_hors_echantillon.py`)

Construire des positions instrumentées synthétiques (`peak_pct`,
`trough_pct`, `minutes_to_peak`/`minutes_to_trough` si nécessaires à
`simulate_exits`, `timestamp_exit`, `pnl_usd`) — s'inspirer de
`tests/test_exit_replay.py`. Cas minimaux :

1. **Le bug verrouillé** : un couple excellent sur les anciennes positions et
   mauvais sur les récentes n'est **pas** appliqué (aujourd'hui il l'est).
2. Un couple bon sur les deux est appliqué, et la raison cite les deux tranches.
3. Trop peu de positions changées en test → « indéterminé », rien appliqué,
   message distinct de « rejeté ».
4. Ordre d'entrée mélangé (récentes d'abord) → même décision qu'avec une
   liste triée : le tri est fait par le code.
5. Position sans `timestamp_exit` → exclue, pas d'exception.
6. Sous 15 positions → rien, comme aujourd'hui.
7. A2.5 : le message de filtres validés contient la mesure hors échantillon ;
   la décision est identique à avant.

Tests existants à garder verts : `tests.test_calibration`,
`tests.test_exit_replay`, `tests.test_garde_fous`, `tests.test_arms`,
`tests.test_slippage_buffer_recalibration`. Si un test existant supposait
l'ancien comportement (choix sur tout l'échantillon), l'adapter **en le
disant** dans le rapport, sans affaiblir ce qu'il protège.

## Conception — A3 (paramètres déclarés mais non appliqués)

**A3.1 — Liste explicite.** Un dictionnaire dans le code (emplacement au
choix du builder, par ex. `src/core/params.py`), clé = chemin pointé, valeur
= raison :

```python
PARAMETRES_NON_APPLIQUES = {
    "entry_rules.min_liquidity_lock_days":
        "RugCheck expose lpLockedPct, pas une durée de verrou : aucune source.",
}
```

**A3.2 — Test qui verrouille** (`tests/test_parametres_appliques.py`) : pour
chaque clé des sections `filters`, `entry_rules`, `exit_rules`, `risk_rules`
de `config/params.json` **et** de chaque `config/arms/*.json` (clés
commençant par `_` exclues : ce sont des notes), le nom de la clé doit
apparaître dans le code source de `src/` (recherche textuelle du nom simple),
**ou** le chemin pointé doit figurer dans `PARAMETRES_NON_APPLIQUES`.
Message d'échec : la liste des clés orphelines, avec la consigne « la lire
dans le code ou l'ajouter à PARAMETRES_NON_APPLIQUES avec sa raison ».
Si d'autres clés orphelines apparaissent au premier passage, le builder
vérifie chacune : vraiment non lue → l'ajouter à la liste avec sa raison ;
lue sous une autre forme (nom construit dynamiquement) → la documenter dans
une seconde liste « lue dynamiquement » avec l'endroit du code. Les lister
dans le rapport.

**A3.3 — Affichage.** `scripts/voir_bras.py` marque ces clés dans la section
« Valeurs EN VIGUEUR » (par ex. suffixe « non appliqué : <raison> »), avec un
test dans `tests/test_voir_bras.py`.

**A3.4 — Doc.** README, section « Points à connaître » : renvoyer à la liste.
Corriger au passage le paragraphe « P&L papier optimiste… sans slippage ni
frais », devenu faux : le coût de sortie est appliqué depuis les ADR 009/010
(jambe finale) et estimé sur les jambes partielles depuis l'ADR 012.

**Interdit** : modifier `config/params.json` ou `config/arms/*.json`.

## Conception — A6 (test de rendu du panneau « P&L par bras »)

**A6.1 — Outillage.** Dans `dashboard/` : `vitest`, `jsdom`,
`@testing-library/react` (et `@testing-library/dom` si requis comme pair) en
`devDependencies`, **versions exactes** (`npm install -D --save-exact`),
publiées depuis au moins deux semaines. Script `"test": "vitest run"`.
Configuration de l'environnement `jsdom` au choix du builder ; contraintes :
`npm run build` (`tsc -b && vite build`) et `npm run lint` (oxlint) restent
verts, et les fichiers `*.test.tsx` n'entrent pas dans le bundle.

**A6.2 — Test** (`dashboard/src/components/weekly/ArmPerformance.test.tsx`) :
`fetch` simulé renvoyant une réponse `/api/arms/performance` avec deux bras et
la ligne flotte (forme : `ArmsPerformanceResponse` dans
`dashboard/src/lib/types.ts`). Vérifier :
1. chaque ligne de bras affiche le nom de son bras dans sa première cellule ;
2. la ligne de somme s'affiche « flotte (somme) » et jamais sous un nom de bras ;
3. cliquer « 30 j » redemande `/api/arms/performance?window=30d` ;
4. une erreur HTTP affiche « Erreur de chargement » et aucune ligne chiffrée.

**A6.3 — Doc.** Ajouter `npm test` aux commandes de vérification :
`.claude/skills/api-dashboard/SKILL.md` (section Vérifier),
`.claude/skills/tests-qualite/SKILL.md`, `docs/agents/workflow-git.md` (règle 5).

## Fin du travail

- `docs/FEUILLE_DE_ROUTE.md` : A2, A3, A6 passent dans « Fait », avec ce qui
  reste (verrou hors échantillon des filtres : à décider sur mesure).
- `HANDOFF.md` mis à jour (≤ 400 mots).
- Suite Python, build, lint et `npm test` verts ; preuves dans la PR.

## Points tranchés après relecture (choix de Claude, à vérifier par Killian)

Un relecteur indépendant a posé huit questions. Aucune ne demandait une
décision de Killian ; voici les réponses retenues, chacune marquée comme un
choix de Claude.

1. **Seuil de 5 positions changées en test — conservé.** Mesure sur
   l'historique des ajustements (`config/arms/*.json`, `params.json`) : les
   recherches de sorties appliquées ont porté sur **40 positions en médiane**
   (minimum 15, maximum 100), soit un tiers de test d'environ 13 positions.
   Exiger 5 positions changées sur 13 est atteignable ; seuls les bras à 15-20
   positions resteront « indéterminés », ce qui est le comportement voulu
   quand les données manquent. Pas de seuil réduit : il affaiblirait la preuve.
2. **Positions ambiguës ou dégradées dans le test.** `simulate_exits` est
   appelé comme aujourd'hui (`ambiguity="resolve"`). En plus, le garde-fou des
   25 % de positions dégradées est appliqué **aussi au tiers de test seul** :
   au-delà, issue « indéterminé ». Sinon la confirmation reposerait sur un
   ordre pic/creux deviné.
3. **Référence proche de zéro.** Confirmé exige `gain_test > 0` **et**
   `gain_test ≥ 10 % × |référence_test|`. Pas de plancher absolu : aucune
   mesure ne le justifie aujourd'hui.
4. **Tiers de test fait de positions empruntées.** Accepté : la mise en commun
   des trajectoires pour les sorties est une décision existante du dépôt
   (`_instrumented`, filtrée par `_in_window`). La ligne de journal donne le
   nombre de positions propres / empruntées dans chaque tranche.
5. **Fenêtre puis tri.** `_instrumented` garde sa sélection (50 dernières
   lignes écrites, le journal étant écrit à la clôture, donc quasi
   chronologique) ; le tri par `timestamp_exit` se fait sur cette sélection
   dans `_search_exits`. `validate_exit_changes` n'est pas touché.
6. **A2.5 (filtres).** Mêmes règles qu'A2.1 : lignes sans date exclues et
   comptées. La mesure est ajoutée dès qu'il y a au moins une ligne récente ;
   sous 5 lignes, elle porte la mention « (indicatif) ».
7. **A3.2, recherche du nom de clé.** Par analyse syntaxique (`ast`) des
   fichiers `src/**/*.py` : seules les **chaînes littérales hors docstrings**
   comptent (les commentaires sont ignorés d'office). Une chaîne pointée
   (`"filters.min_age_hours"`) compte pour son dernier segment. Garantie
   plus forte qu'une recherche textuelle, toujours sans exécuter le code.
8. **Versions vitest/jsdom.** Les plus récentes compatibles avec la chaîne
   existante (Vite, React 19, TypeScript du `package.json`), publiées depuis
   au moins deux semaines, figées exactement. **Ne pas monter** les versions
   existantes de Vite, React ou TypeScript ; si aucune version compatible
   n'existe, le dire dans le rapport au lieu de monter la chaîne.

## Amendements après l'audit d'architecture (I10) — proposés par Claude

L'audit (`docs/audits/2026-11-17-audit_architecture.md`, § I10) a trouvé des
fuites que la version ci-dessus laisse passer ; (a) et (c) re-vérifiées dans
le code. Ces amendements **priment** sur les sections A2.2 à A2.4 et sur le
point tranché 3.

- **(a) Rejouer ce qui sera appliqué.** `_bounded_set` modifie la valeur après
  l'évaluation (pas maximal ±15 pts sur TP1 et ±2 pts sur le stop via
  `_limit_step`, bornes, plafond du stop effectif, TP1 < TP2). Extraire le
  calcul de la valeur dans une fonction pure (`_planned_value(path, value)`,
  aucune écriture) que `_bounded_set` appelle ; la confirmation sur le tiers
  de test rejoue **les valeurs planifiées**, pas le point de grille. Les
  lignes de grille dont le stop effectif (stop + tampon) est ≥ 0 sont
  écartées : elles ne peuvent pas être appliquées.
- **(b) La validation dans l'échantillon ne défait plus un choix confirmé hors
  échantillon.** Dans `run()`, `_adjust_exits` puis `validate_exit_changes`
  (inchangés), **ensuite seulement** `_search_exits`.
- **(c) Pas de mesure faite sur le futur.** `measured_breakeven_rest` lit tout
  le journal (`learning.py:1107-1120`, l'argument `rows` est ignoré). Lui
  ajouter une borne `until` (instant de clôture) ; pour la sélection sur
  l'apprentissage, glissement et reste sont mesurés sur les jambes closes au
  plus tard à la fin de la tranche d'apprentissage.
- **(d) Dédoublonner avant de couper** : une même position (même
  `token_address` et même instant d'entrée) ne peut pas être des deux côtés ;
  la version propre au bras est gardée.
- **(e) Critère apparié au lieu de « 10 % relatif ».** `simulate_exits` rend
  aussi le P&L par position (`pnl_par_position`, même ordre que l'entrée). Sur
  le tiers de test : différences par position (planifié − actuel), puis
  `bootstrap_mean` ; **confirmé si la borne basse de l'intervalle est > 0** et
  si au moins `EXIT_BACKTEST_MIN_CHANGED` positions changent. Fin du seuil
  relatif à une référence de quelques centimes.
- **(g)** La phrase « 50 dernières lignes écrites, donc quasi chronologique »
  (point tranché 5) est retirée : le tri par instant de clôture est fait dans
  `_search_exits` dans tous les cas.

Restent hors périmètre, et inscrits dans `docs/AUDIT_2026-11-17.md` : filtres
sans verrou hors échantillon (I1), ordre pic/creux deviné (I6), emprunts
dominés par `consensus` (I5), mélange de régimes dans un journal (B4).

## Laissé au builder

Noms internes (attribut de verdict, fonctions d'aide), emplacement de
`PARAMETRES_NON_APPLIQUES`, configuration vitest, structure des fixtures de
test, découpage en commits.
