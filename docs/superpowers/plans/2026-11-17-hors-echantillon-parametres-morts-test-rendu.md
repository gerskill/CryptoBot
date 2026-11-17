# Hors échantillon (A2), paramètres non appliqués (A3), test de rendu (A6) — plan d'exécution

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** la recherche de sorties de l'apprentissage n'applique un couple (stop, TP1) que s'il gagne aussi sur des positions plus récentes que celles qui l'ont choisi ; les paramètres déclarés mais non appliqués sont listés et verrouillés par un test ; le panneau « P&L par bras » a un test de rendu.

**Architecture:** A2 ajoute un découpage chronologique (`chronological_split`) dans `src/core/learning.py`, utilisé par `_search_exits` (verrou) et `validate_filter_changes` (mesure seule). A3 ajoute un dictionnaire `PARAMETRES_NON_APPLIQUES` et un test par analyse syntaxique de `src/`. A6 ajoute vitest au dashboard.

**Tech Stack:** Python 3.13 / unittest ; React 19 + TypeScript + Vite, vitest + jsdom + Testing Library.

**Spec:** `docs/superpowers/specs/2026-11-17-hors-echantillon-parametres-morts-test-rendu-design.md` (approuvée par Killian le 2026-11-17, y compris la section « Points tranchés après relecture »).

## Global Constraints

- Mode PAPER uniquement ; aucun code d'exécution réelle.
- Français pour docstrings, commentaires, noms de tests et messages ; les commentaires expliquent le **pourquoi**.
- **Ne jamais modifier `config/params.json` ni `config/arms/*.json`** (réécrits par l'apprentissage sur le Mac, conflit git sinon).
- Tests Python en classes `unittest.TestCase` (pas de fonctions nues) ; un test est nommé d'après le bug qu'il verrouille.
- Écriture de paramètre via `_bounded_set` seulement. Messages distincts « ajusté », « rien à ajuster », « pas assez de données ».
- Constantes existantes réutilisées telles quelles : `EXIT_BACKTEST_MIN_COVERAGE = 15`, `EXIT_BACKTEST_MIN_CHANGED = 5`, `BACKTEST_MIN_IMPROVEMENT = 0.10` (sélection sur l'apprentissage seulement), garde-fou « dégradé » à 25 %.
- Confirmation hors échantillon = borne basse > 0 de l'intervalle `bootstrap_mean` des différences appariées par position (amendement e de la spec).
- Nouvelle constante : `HOLDOUT_TRAIN_FRACTION = 2 / 3`.
- Dashboard : ne pas monter les versions existantes de Vite, React, TypeScript ; nouvelles dépendances de dev en versions exactes, publiées depuis ≥ 2 semaines.
- Vérification globale : `python -m unittest discover -s tests` vert ; `cd dashboard && npm run build && npm run lint && npm test` verts.
- Aucun chiffre de performance de bras annoncé.

## Review Focus

1. Position sans `timestamp_exit` ou avec une date illisible : exclue du découpage, comptée, aucune exception (Task 1).
2. Liste de positions non triée (empruntées avant les propres, récentes avant anciennes) : même décision qu'avec la liste triée (Task 2).
3. Gain de quelques centimes, ou différences appariées de signes opposés : jamais « confirmé » (Task 2).
4. Tiers de test majoritairement « dégradé » (ordre pic/creux deviné) alors que l'ensemble passe le garde-fou : « indéterminé » (Task 2).
5. Réponse `/api/arms/performance` sans aucun bras (`arms: []`) : le panneau affiche la ligne « flotte (somme) » sans planter (Task 5).

---

### Task 1: Briques sans fuite — valeur planifiée, P&L par position, mesure bornée, découpage dédoublonné (amendements a, c, d, e)

**Files:**
- Modify: `src/core/learning.py` — `_bounded_set` (l. ~410-470), `measured_breakeven_rest` (l. ~1107-1120), `simulate_exits` (l. ~1153-1273) ; nouvelles fonctions ci-dessous
- Create: `tests/test_hors_echantillon.py`

**Interfaces:**
- Produces:
  - `HOLDOUT_TRAIN_FRACTION: float = 2 / 3` (module `src.core.learning`).
  - `chronological_split(positions: list[dict], train_fraction: float = HOLDOUT_TRAIN_FRACTION) -> tuple[list[dict], list[dict], int]` (module) : **dédoublonne d'abord** (clé `(token_address, timestamp_entry)` ; si `timestamp_entry` manque, `position_id` ; en cas de doublon, garder la première occurrence de la liste reçue — l'appelant met les positions propres en tête), puis trie par `src.core.stats.exit_datetime` croissant, exclut les positions sans date lisible, renvoie `(apprentissage, test, nb_exclues)` avec `len(apprentissage) == ceil(n * train_fraction)` (`n` = positions datées et uniques). `train_fraction=1.0` → `test` vide.
  - `LearningEngine._planned_value(self, path: str, value: float) -> float` : la valeur que `_bounded_set` **écrirait**, sans rien écrire (pas max `_limit_step`, plafond du stop effectif, TP1 < TP2, bornes). Si `_starving(path, value)` bloquerait l'écriture, renvoie la valeur courante. `_bounded_set` appelle `_planned_value` (une seule source de vérité) et garde son comportement observable actuel.
  - `measured_breakeven_rest(self, rows, until: Optional[datetime] = None) -> float` : si `until` est donné, seules les jambes `BREAKEVEN_STOP` dont `timestamp_exit` (via `exit_datetime`) est ≤ `until` comptent ; jambe sans date lisible exclue dans ce cas. Sans `until` : comportement actuel inchangé.
  - `simulate_exits(..., until: Optional[datetime] = None)` : transmet `until` à `measured_breakeven_rest` ; le résultat gagne la clé `"pnl_par_position": list[float]` (P&L en $ de chaque position évaluée, dans l'ordre de la liste reçue ; positions sautées absentes). Clés existantes inchangées.

- [ ] **Step 1: Tests qui échouent** — `tests/test_hors_echantillon.py`. Construire le moteur et les positions comme `tests/test_exit_replay.py` (helper `position()`, `PARAMS`, `TradeJournal` temporaire, `journal._append`), avec `timestamp_exit` ISO :
  - classe `TestDecoupageChronologique` : 9 positions mélangées → 6 + 3 triées ; `train_fraction=1.0` → test vide ; liste vide → `([], [], 0)` ; `test_une_position_presente_deux_fois_nest_que_dun_cote` (même `token_address` et `timestamp_entry`, `position_id` différents) → comptée une fois ; `test_position_sans_date_exclue_et_comptee` (absente et `"pas une date"`) → `nb_exclues == 2`, aucune exception.
  - classe `TestValeurPlanifiee` : `test_le_pas_maximal_est_dans_la_valeur_planifiee` (TP1 courant 100, demandé 150 → planifié 115, rien d'écrit) ; `test_le_stop_effectif_positif_est_plafonne_dans_la_valeur_planifiee` (tampon 15, stop demandé -10 → planifié ≤ plafond `_stop_loss_ceiling(15)`) ; `test_bounded_set_ecrit_exactement_la_valeur_planifiee`.
  - classe `TestMesureSansFutur` : `test_le_reste_au_breakeven_ignore_les_jambes_posterieures` (jambes `BREAKEVEN_STOP` à -1 % avant `until`, à -30 % après → médiane -1) ; `test_sans_borne_le_reste_est_inchange`.
  - classe `TestPnlParPosition` : `pnl_par_position` a la longueur des positions évaluées et sa somme vaut `total_pnl_usd`.
- [ ] **Step 2: Lancer** `python -m unittest tests.test_hors_echantillon -v` → ERROR (imports manquants).
- [ ] **Step 3: Implémenter** les interfaces ci-dessus. Commentaires : le pourquoi de chaque brique (fuite I10 a, c, d, e de `docs/audits/2026-11-17-audit_architecture.md`).
- [ ] **Step 4: Lancer** `python -m unittest tests.test_hors_echantillon tests.test_exit_replay tests.test_learning_slow tests.test_calibration tests.test_garde_fous tests.test_slippage_buffer_recalibration -v` → OK (aucun test existant modifié dans cette tâche).
- [ ] **Step 5: Commit** `git commit -m "Apprentissage : briques sans fuite pour le hors échantillon (valeur planifiée, P&L par position, mesure bornée)"`

### Task 2: Recherche de sorties confirmée hors échantillon + ordre dans `run()` (A2.1-A2.4, amendements a, b, e)

**Files:**
- Modify: `src/core/learning.py` — `_search_exits` (l. ~1361-1420), `run()` (bloc sorties, l. ~635-648), `__init__` (attribut)
- Modify: `tests/test_hors_echantillon.py` (nouvelle classe)
- Modify (adapter, en le disant dans le rapport) : `tests/test_exit_replay.py` (`_log`, `_positions_qui_favorisent_un_tp1_haut`), `tests/test_learning_slow.py::test_search_exits_avance_par_petits_pas`

**Interfaces:**
- Consumes (Task 1) : `chronological_split`, `_planned_value`, `simulate_exits(..., until=)` et sa clé `pnl_par_position`, `src.core.stats.bootstrap_mean`.
- Produces : `LearningEngine.last_exit_search: Optional[dict]` — `None` avant tout appel ; après chaque `_search_exits`, au moins `"issue"` ∈ {`"pas assez de données"`, `"dégradé"`, `"rien à ajuster"`, `"indéterminé"`, `"rejeté hors échantillon"`, `"confirmé"`} et, dès le découpage, `"train_n"`, `"test_n"`, `"exclues"`. `_search_exits() -> list[str]` inchangé (vide sauf « confirmé »).

Algorithme de `_search_exits` (décide l'ordre, le reste est au builder) :
1. `positions = self._instrumented()` (positions propres en tête, inchangé) ; sous `EXIT_BACKTEST_MIN_COVERAGE` → « pas assez de données » ; garde-fou dégradé > 25 % sur l'ensemble (inchangé) → « dégradé ».
2. `apprentissage, test, exclues = chronological_split(positions)` ; `fin = exit_datetime(apprentissage[-1])`.
3. Sur `apprentissage`, avec `until=fin` : grille comme aujourd'hui, en **écartant les lignes dont stop + tampon courant ≥ 0** ; premier candidat qui change ≥ `EXIT_BACKTEST_MIN_CHANGED`, respecte les bornes et gagne ≥ 10 % de |référence d'apprentissage| ; aucun → « rien à ajuster ».
4. Valeurs planifiées : `sl = _planned_value(stop, candidat_sl)`, `tp1 = _planned_value(tp1, candidat_tp1)` ; si les deux égalent les valeurs courantes → « rien à ajuster ».
5. Sur `test` (sans `until`) : garde-fou dégradé > 25 % du test → « indéterminé » ; rejouer règles actuelles et règles planifiées ; `changed` planifié < `EXIT_BACKTEST_MIN_CHANGED` → « indéterminé » ; différences appariées `planifié − actuel` par position (`pnl_par_position`, même ordre) → `bootstrap_mean` ; `None` → « indéterminé » ; borne basse > 0 → « confirmé » (écrire via `_bounded_set` avec une raison citant les deux tranches, leurs effectifs propres/empruntés, P&L/trade avant → après, l'intervalle apparié) ; sinon « rejeté hors échantillon ». Une ligne `🧠 …` par issue.

`run()` : `_adjust_exits` → `validate_exit_changes` sur ses seuls changements (comme aujourd'hui) → **ensuite** `_search_exits` ; les changements de la recherche ne passent plus par `validate_exit_changes`.

- [ ] **Step 1: Tests qui échouent** — classe `TestRechercheHorsEchantillon` :
  - `test_couple_bon_sur_le_passe_mauvais_sur_le_recent_nest_pas_applique` : 14 anciennes positions favorables à un TP1 haut (pic 200, creux -5, `minutes_to_peak` 10, `minutes_to_trough` 40), puis 7 récentes qui le punissent (pic 30, creux -60, creux avant pic) → `[]`, TP1 inchangé, `issue == "rejeté hors échantillon"`.
  - `test_couple_bon_sur_les_deux_tranches_est_applique_et_cite_les_deux` : 21 positions favorables → liste non vide, `issue == "confirmé"`, la dernière raison de `parameter_adjustment_history` contient `"hors échantillon"`, `"14"` et `"7"`.
  - `test_on_rejoue_la_valeur_planifiee_pas_le_point_de_grille` : TP1 courant 100, grille qui vise 150 ; espionner `simulate_exits` (enveloppe) et vérifier que la règle rejouée sur le test porte `take_profit_1 == 115`.
  - `test_trop_peu_de_positions_changees_en_test_donne_indetermine` (pic et creux récents entre -5 et +5).
  - `test_ordre_melange_donne_la_meme_decision_que_trie` (positions récentes écrites d'abord).
  - `test_tiers_de_test_degrade_donne_indetermine` (récentes sans `minutes_to_trough`, total sous 25 % de dégradées).
  - `test_gain_de_quelques_centimes_nest_pas_confirme` : différences appariées tantôt +0,01 $, tantôt -0,01 $ → pas « confirmé ».
  - `test_la_validation_dans_lechantillon_ne_defait_pas_un_choix_confirme` : via `run()`, cadence atteinte, recherche confirmée → la valeur reste écrite ; aucune entrée « (annulé) » pour elle.
  - `test_sous_la_couverture_rien_comme_avant` (10 positions) → `issue == "pas assez de données"`.
- [ ] **Step 2: Lancer** → FAIL.
- [ ] **Step 3: Implémenter** selon l'algorithme ci-dessus ; commentaire du pourquoi (fuite n°1 de `chasse-au-lookahead`, I10).
- [ ] **Step 4: Adapter les tests existants** qui fabriquent des positions sans date : ajouter un `timestamp_exit` croissant (`tests/test_exit_replay.py` : `_log`, `_positions_qui_favorisent_un_tp1_haut` ; `tests/test_learning_slow.py` : les `[{}] * 20`, et le simulateur simulé doit rendre `pnl_par_position`). Ne changer aucune assertion ; si une assertion doit changer, l'expliquer dans le rapport avec la raison.
- [ ] **Step 5: Lancer** `python -m unittest tests.test_hors_echantillon tests.test_exit_replay tests.test_learning_slow tests.test_calibration tests.test_garde_fous tests.test_arms tests.test_slippage_buffer_recalibration -v` → OK ; puis `python -m unittest discover -s tests` → OK.
- [ ] **Step 6: Commit** `git commit -m "Apprentissage : la recherche de sorties confirme son choix hors échantillon (A2)"`

### Task 3: Mesure hors échantillon des filtres (A2.5) et tri dans `audit.py` (A2.6)

**Files:**
- Modify: `src/core/learning.py` (`validate_filter_changes`, l. ~1057-1088)
- Modify: `scripts/audit.py` (`section_walk_forward`, l. ~150-160)
- Modify: `tests/test_hors_echantillon.py` (nouvelle classe)

**Interfaces:**
- Consumes: `chronological_split` (Task 1).
- Produces: le message renvoyé par `validate_filter_changes` dans le cas validé devient `"filtres validés (P&L/trade a → b) ; hors échantillon (m récents) : P&L/trade c → d"`, suffixé de `" (indicatif)"` si `m < 5`, sans suffixe hors échantillon si `m == 0`. Les cas « annulés » et « trop restrictifs » sont inchangés, la décision est inchangée dans tous les cas.

- [ ] **Step 1: Tests qui échouent** — classe `TestMesureFiltres` dans `tests/test_hors_echantillon.py` ; s'inspirer de `tests/test_shadow.py` l. ~455-495 pour fabriquer 50 positions et un changement de filtres validé :
  - `test_filtres_valides_portent_la_mesure_hors_echantillon` : message contient `"hors échantillon ("` et `"récents)"`.
  - `test_la_mesure_ne_change_pas_la_decision` : mêmes entrées qu'un cas « annulés » de `tests/test_shadow.py` → message toujours `"filtres annulés …"` et filtres restaurés.
  - `test_peu_de_recents_marque_indicatif` : seules 3 lignes datées dans la fenêtre (les autres sans date) → message contient `"(indicatif)"`.
- [ ] **Step 2: Lancer** `python -m unittest tests.test_hors_echantillon.TestMesureFiltres -v` → FAIL.
- [ ] **Step 3: Implémenter** : dans la branche « validés » seulement, `chronological_split(rows)` → tranche test, `simulate_filters` avant/après sur cette tranche, suffixe conforme à l'interface. Dans `scripts/audit.py::section_walk_forward`, remplacer la liste `pos` par `chronological_split(pos, train_fraction=1.0)[0]` avant le seuil de 13 et `walk_forward`, avec un commentaire sur le pourquoi.
- [ ] **Step 4: Lancer** `python -m unittest tests.test_hors_echantillon tests.test_shadow -v` → OK ; `python -m scripts.audit` → s'exécute sans exception (sans données, il le dit).
- [ ] **Step 5: Commit** `git commit -m "Filtres : mesure hors échantillon dans le message ; audit trie avant le walk-forward (A2.5, A2.6)"`

### Task 4: Paramètres déclarés mais non appliqués (A3)

**Files:**
- Modify: `src/core/params.py` (nouveau dictionnaire de module)
- Create: `tests/test_parametres_appliques.py`
- Modify: `scripts/voir_bras.py` (`_afficher_bras`), `tests/test_voir_bras.py`
- Modify: `README.md` (section « Points à connaître » : paragraphe `min_liquidity_lock_days` et paragraphe « P&L papier optimiste »)

**Interfaces:**
- Produces:
  - `PARAMETRES_NON_APPLIQUES: dict[str, str]` dans `src/core/params.py`, contenant au moins `"entry_rules.min_liquidity_lock_days": "RugCheck expose lpLockedPct, pas une durée de verrou : aucune source."`.
  - `PARAMETRES_LUS_DYNAMIQUEMENT: dict[str, str]` dans `src/core/params.py` (vide si aucun cas ; valeur = endroit du code qui lit la clé).
  - `voir_bras.annotation(cle_pointee: str) -> str` : `""` ou `"  (non appliqué : <raison>)"`.

- [ ] **Step 1: Tests qui échouent** :
  - `tests/test_parametres_appliques.py`, classe `TestChaqueParametreEstLuOuDeclare` : collecte, par `ast`, les chaînes littérales de `src/**/*.py` **hors docstrings** (une chaîne pointée compte aussi pour son dernier segment) ; pour chaque clé des sections `filters`, `entry_rules`, `exit_rules`, `risk_rules` de `config/params.json` et de chaque `config/arms/*.json` (clés commençant par `_` ignorées, section absente ignorée), exige : nom présent, ou chemin pointé dans `PARAMETRES_NON_APPLIQUES` ou `PARAMETRES_LUS_DYNAMIQUEMENT`. Message d'échec : liste des orphelines + « la lire dans le code ou l'ajouter à PARAMETRES_NON_APPLIQUES avec sa raison ». Deuxième test : `test_une_cle_declaree_non_appliquee_est_vraiment_absente_du_code` — chaque clé de `PARAMETRES_NON_APPLIQUES` est **absente** des chaînes collectées (sinon la liste ment).
  - `tests/test_voir_bras.py` : `test_un_parametre_non_applique_est_signale` (`annotation("entry_rules.min_liquidity_lock_days")` contient `"non appliqué"`) et `test_un_parametre_applique_na_pas_dannotation` (`annotation("exit_rules.stop_loss_pct") == ""`).
- [ ] **Step 2: Lancer** `python -m unittest tests.test_parametres_appliques tests.test_voir_bras -v` → FAIL (import).
- [ ] **Step 3: Implémenter** les deux dictionnaires et `annotation`, l'afficher en suffixe dans « Valeurs EN VIGUEUR » de `voir_bras.py`. Lancer le test d'orphelines : pour **chaque** autre orpheline trouvée, vérifier dans le code ; vraiment non lue → `PARAMETRES_NON_APPLIQUES` avec sa raison ; lue sous un nom construit → `PARAMETRES_LUS_DYNAMIQUEMENT` avec l'endroit. Lister chaque cas dans le rapport. **Ne modifier aucun fichier de `config/`.**
- [ ] **Step 4: README** — paragraphe `min_liquidity_lock_days` : renvoyer à `PARAMETRES_NON_APPLIQUES` ; paragraphe « P&L papier optimiste » : remplacer par l'état réel (coût de sortie mesuré sur la jambe finale, ADR 009/010 ; estimé sur les jambes partielles, ADR 012 ; reste optimiste en ce qu'il n'y a ni ordre réel ni file d'attente).
- [ ] **Step 5: Lancer** `python -m unittest discover -s tests` → OK.
- [ ] **Step 6: Commit** `git commit -m "Paramètres déclarés mais non appliqués : liste explicite et test qui la verrouille (A3)"`

### Task 5: Test de rendu du panneau « P&L par bras » (A6)

**Files:**
- Modify: `dashboard/package.json`, `dashboard/package-lock.json` (via `npm install -D --save-exact`)
- Modify ou Create : configuration vitest (dans `dashboard/vite.config.ts` ou `dashboard/vitest.config.ts`, au choix) et, si nécessaire, `dashboard/tsconfig.app.json` pour exclure les `*.test.tsx` du build
- Create: `dashboard/src/components/weekly/ArmPerformance.test.tsx`
- Modify: `.claude/skills/api-dashboard/SKILL.md` (section Vérifier), `.claude/skills/tests-qualite/SKILL.md` (bloc de commandes), `docs/agents/workflow-git.md` (règle 5)

**Interfaces:**
- Consumes: `ArmPerformance` (`dashboard/src/components/weekly/ArmPerformance.tsx`), type `ArmsPerformanceResponse` (`dashboard/src/lib/types.ts`), hook `useArmPerformance` qui appelle `fetch('/api/arms/performance?window=<7d|30d|all>')`.
- Produces: script `"test": "vitest run"`.

- [ ] **Step 1: Outillage** — `cd dashboard && npm install -D --save-exact vitest@<v> jsdom@<v> @testing-library/react@<v>` (+ `@testing-library/dom` si pair requis), versions compatibles avec Vite/React/TypeScript actuels et publiées depuis ≥ 2 semaines ; environnement `jsdom` ; script `test`.
- [ ] **Step 2: Test qui échoue d'abord par absence de fichier, puis tests** — `ArmPerformance.test.tsx` avec `fetch` simulé (`vi.stubGlobal`) :
  - `chaque ligne de bras affiche son bras` : réponse à deux bras (`sniper`, `baseline`) + flotte → la première cellule de chaque ligne de bras vaut le nom du bras.
  - `la somme s'affiche flotte (somme), jamais sous un nom de bras`.
  - `cliquer 30 j redemande la fenêtre 30d` : un appel à `fetch` avec une URL contenant `window=30d`.
  - `une erreur HTTP affiche Erreur de chargement et aucune ligne chiffrée`.
  - `sans aucun bras, seule la ligne flotte s'affiche sans planter` (Review Focus 5).
- [ ] **Step 3: Lancer** `npm test` → OK ; `npm run build` → OK (aucun `*.test.tsx` dans `dist/`) ; `npm run lint` → 0 avertissement.
- [ ] **Step 4: Docs** — ajouter `npm test` aux trois fichiers listés.
- [ ] **Step 5: Commit** `git commit -m "Dashboard : test de rendu du panneau P&L par bras (A6)"`

## Fin (contrôleur, après la revue finale)

`docs/FEUILLE_DE_ROUTE.md` (A2, A3, A6 → « Fait », reste : verrou hors échantillon des filtres à décider sur mesure), `HANDOFF.md` (≤ 400 mots), PR avec preuves.
