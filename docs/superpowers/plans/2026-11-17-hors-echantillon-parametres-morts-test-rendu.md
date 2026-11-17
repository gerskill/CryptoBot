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
- Constantes existantes réutilisées telles quelles : `EXIT_BACKTEST_MIN_COVERAGE = 15`, `EXIT_BACKTEST_MIN_CHANGED = 5`, `BACKTEST_MIN_IMPROVEMENT = 0.10`, garde-fou « dégradé » à 25 %.
- Nouvelle constante : `HOLDOUT_TRAIN_FRACTION = 2 / 3`.
- Dashboard : ne pas monter les versions existantes de Vite, React, TypeScript ; nouvelles dépendances de dev en versions exactes, publiées depuis ≥ 2 semaines.
- Vérification globale : `python -m unittest discover -s tests` vert ; `cd dashboard && npm run build && npm run lint && npm test` verts.
- Aucun chiffre de performance de bras annoncé.

## Review Focus

1. Position sans `timestamp_exit` ou avec une date illisible : exclue du découpage, comptée, aucune exception (Task 1).
2. Liste de positions non triée (empruntées avant les propres, récentes avant anciennes) : même décision qu'avec la liste triée (Task 1).
3. Référence du tiers de test nulle ou négative avec un gain nul ou négatif : jamais « confirmé » (Task 1).
4. Tiers de test majoritairement « dégradé » (ordre pic/creux deviné) alors que l'ensemble passe le garde-fou : « indéterminé » (Task 1).
5. Réponse `/api/arms/performance` sans aucun bras (`arms: []`) : le panneau affiche la ligne « flotte (somme) » sans planter (Task 4).

---

### Task 1: Hold-out chronologique dans `_search_exits` (A2.1 à A2.4)

**Files:**
- Modify: `src/core/learning.py` (constante près de `EXIT_BACKTEST_MIN_CHANGED`, l. ~141 ; fonction de module `chronological_split` ; méthode `_search_exits`, l. ~1361-1420 ; attribut initialisé dans `__init__`, l. ~335)
- Create: `tests/test_hors_echantillon.py`
- Modify (adapter, en le disant dans le rapport) : `tests/test_exit_replay.py` (helper `position()` et `_positions_qui_favorisent_un_tp1_haut`, `_log`), `tests/test_learning_slow.py::test_search_exits_avance_par_petits_pas`

**Interfaces:**
- Produces:
  - `HOLDOUT_TRAIN_FRACTION: float = 2 / 3` (module `src.core.learning`).
  - `chronological_split(positions: list[dict], train_fraction: float = HOLDOUT_TRAIN_FRACTION) -> tuple[list[dict], list[dict], int]` (module `src.core.learning`) : trie par `src.core.stats.exit_datetime` croissant, exclut les positions sans date lisible, renvoie `(apprentissage, test, nb_exclues)` avec `len(apprentissage) == ceil(n * train_fraction)` où `n` = positions datées. Avec `train_fraction=1.0`, `test` est vide et `apprentissage` = toutes les positions datées triées.
  - `LearningEngine.last_exit_search: Optional[dict]` — `None` avant tout appel ; après chaque appel de `_search_exits`, un dict avec au moins `"issue"` ∈ {`"pas assez de données"`, `"dégradé"`, `"rien à ajuster"`, `"indéterminé"`, `"rejeté hors échantillon"`, `"confirmé"`} et, dès que le découpage a eu lieu, `"train_n"`, `"test_n"`, `"exclues"`.
  - `_search_exits()` garde sa signature `-> list[str]` (liste vide sauf issue « confirmé »).

- [ ] **Step 1: Écrire les tests qui échouent** — `tests/test_hors_echantillon.py`, classe `TestRechercheHorsEchantillon(unittest.TestCase)`. Construire le moteur comme `tests/test_exit_replay.py::TestGrilleEtRecherche` (params `PARAMS`, journal temporaire) et des positions instrumentées avec `peak_pct`, `trough_pct`, `minutes_to_peak`, `minutes_to_trough`, `pnl_pct`, `pnl_usd`, `position_size`, `exit_reason`, `is_final_exit`, `position_id`, et `timestamp_exit` ISO croissant. Tests :
  - `test_couple_bon_sur_le_passe_mauvais_sur_le_recent_nest_pas_applique` : 14 anciennes positions qui favorisent un TP1 haut (pic 200, creux -5, comme `_positions_qui_favorisent_un_tp1_haut`) puis 7 récentes qui le punissent (pic 30, creux -60, `minutes_to_trough` < `minutes_to_peak`) → `_search_exits() == []`, `take_profit_1` inchangé, `last_exit_search["issue"] == "rejeté hors échantillon"`.
  - `test_couple_bon_sur_les_deux_tranches_est_applique_et_cite_les_deux` : 21 positions qui favorisent toutes un TP1 haut → liste non vide, `issue == "confirmé"`, et la raison du dernier élément de `parameter_adjustment_history` contient `"hors échantillon"` et les deux effectifs (`"14"` et `"7"`).
  - `test_trop_peu_de_positions_changees_en_test_donne_indetermine` : tranche récente où le couple choisi ne franchit aucun seuil (pic et creux entre -5 et +5) → `[]`, `issue == "indéterminé"`, distinct de `"rejeté hors échantillon"`.
  - `test_ordre_melange_donne_la_meme_decision_que_trie` : mêmes positions que le cas « rejeté », écrites dans le journal récentes d'abord → même `issue` et mêmes paramètres finaux.
  - `test_position_sans_date_est_exclue_sans_exception` : 21 positions datées + 1 avec `timestamp_exit` absent et 1 avec `"pas une date"` → pas d'exception, `last_exit_search["exclues"] == 2`.
  - `test_sous_la_couverture_rien_comme_avant` : 10 positions → `[]`, `issue == "pas assez de données"`.
  - `test_reference_nulle_et_gain_nul_nest_jamais_confirme` (Review Focus 3) : tranche récente dont le P&L/trade de référence et du candidat valent 0 → pas « confirmé ».
  - `test_tiers_de_test_degrade_donne_indetermine` (Review Focus 4) : anciennes positions avec `minutes_to_trough`, récentes sans (`None`), le total restant sous 25 % de dégradées → `issue == "indéterminé"`.
  - Classe `TestDecoupageChronologique` : `chronological_split` sur 9 positions mélangées → 6 + 3, triées ; `train_fraction=1.0` → test vide ; liste vide → `([], [], 0)`.

- [ ] **Step 2: Lancer et constater l'échec**

Run: `python -m unittest tests.test_hors_echantillon -v`
Expected: ERROR `cannot import name 'chronological_split'`.

- [ ] **Step 3: Implémenter** `HOLDOUT_TRAIN_FRACTION`, `chronological_split` et la nouvelle `_search_exits`, conformément à la spec (A2.1-A2.4 et points tranchés 2, 3, 5). Ordre dans `_search_exits` : couverture (sur toutes les positions) → garde-fou dégradé sur l'ensemble (inchangé) → `chronological_split` → sélection sur `apprentissage` avec la règle existante (changement ≥ `EXIT_BACKTEST_MIN_CHANGED`, bornes, gain ≥ 10 % de |référence d'apprentissage|) → sur `test` : garde-fou dégradé > 25 % → « indéterminé » ; `changed` du candidat < `EXIT_BACKTEST_MIN_CHANGED` → « indéterminé » ; `gain_test > 0 and gain_test >= BACKTEST_MIN_IMPROVEMENT * abs(reference_test)` → « confirmé », sinon « rejeté hors échantillon ». La raison passée à `_bounded_set` cite : effectifs d'apprentissage et de test, positions propres / empruntées de chaque tranche (propres = `position_id` présent dans `self.journal.read_positions()`), P&L/trade avant → après sur chaque tranche, écart apprentissage − test des gains, nombre d'ambiguës. Chaque issue imprime une ligne `🧠 …` en français. Commenter le pourquoi (fuite n°1 de `chasse-au-lookahead`).

- [ ] **Step 4: Adapter les tests existants** qui fabriquent des positions sans date : ajouter un `timestamp_exit` croissant dans `_log`, `_positions_qui_favorisent_un_tp1_haut` (`tests/test_exit_replay.py`) et dans les `[{}] * 20` de `tests/test_learning_slow.py::test_search_exits_avance_par_petits_pas`. Ne changer aucune assertion ; si une assertion doit changer, s'arrêter et l'expliquer dans le rapport.

- [ ] **Step 5: Lancer**

Run: `python -m unittest tests.test_hors_echantillon tests.test_exit_replay tests.test_learning_slow tests.test_calibration tests.test_garde_fous tests.test_arms tests.test_slippage_buffer_recalibration -v`
Expected: tous OK.

- [ ] **Step 6: Suite complète** — `python -m unittest discover -s tests` → OK.

- [ ] **Step 7: Commit**

```bash
git add src/core/learning.py tests/test_hors_echantillon.py tests/test_exit_replay.py tests/test_learning_slow.py
git commit -m "Apprentissage : la recherche de sorties confirme son choix hors échantillon (A2)"
```

### Task 2: Mesure hors échantillon des filtres (A2.5) et tri dans `audit.py` (A2.6)

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

### Task 3: Paramètres déclarés mais non appliqués (A3)

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

### Task 4: Test de rendu du panneau « P&L par bras » (A6)

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
