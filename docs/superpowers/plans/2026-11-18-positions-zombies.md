# B1 positions zombies — plan d'exécution

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** une position restée sans prix exploitable 30 min après son time stop est fermée à -100 % (raison `ZOMBIE …`) ; `/STOP` ferme aussi les positions sans prix à -100 %.

**Architecture:** une méthode de portefeuille `close_worthless` (sortie finale à prix 0, sans devis) appelée depuis `_monitor_arm` et `_panic_exit`.

**Tech Stack:** Python 3.13, unittest.

**Spec:** `docs/superpowers/specs/2026-11-18-positions-zombies-design.md` (approuvée).

## Global Constraints
- PAPER uniquement ; français ; commentaires du pourquoi ; tests `unittest.TestCase` nommés d'après le bug.
- Ne jamais modifier `config/params.json` ni `config/arms/*.json`.
- La boucle ne meurt jamais ; aucune autre règle de sortie modifiée ; écriture des positions via `save_positions` (existant).
- `ZOMBIE_GRACE_MINUTES = 30`.
- Raisons exactes : `ZOMBIE (sans prix depuis le time stop)` et `PANIC_STOP (sans prix)`.
- Lire d'abord `.claude/skills/sorties-positions/SKILL.md`.

## Review Focus
1. Position avec TP1 déjà encaissé puis zombie : le journal garde la jambe TP1, la jambe finale vaut -100 % de la part restante, `read_positions()` agrège correctement.
2. Prix inutilisable AVANT le délai : position gardée, comme aujourd'hui.
3. Redémarrage : la règle ne dépend que de `entry_time` (persisté) — une position restaurée et trop vieille est fermée au premier passage.
4. Frais d'entrée déjà payés (`entry_cost_version`) : le P&L journal de la jambe finale les compte comme `force_close` le fait.
5. `cooldown` / pertes consécutives : `_finalize` est appelé comme pour toute sortie.

---

### Task 1: fermeture d'office des positions sans prix

**Files:**
- Modify: `src/core/portfolio.py` (nouvelle méthode près de `force_close`, l. ~857)
- Modify: `src/main.py` (`_monitor_arm` l. ~1474-1497, `_panic_exit` l. ~978-1010 ; constante en tête de module)
- Modify: `src/core/stats.py` (`RAISONS_AUTOMATIQUES` : ajouter `"ZOMBIE"`)
- Create: `tests/test_positions_zombies.py`

**Interfaces:**
- Produces: `PaperPortfolio.close_worthless(self, position_id: str, reason: str) -> Optional[dict]` — sortie finale de toute la part restante à prix 0 : coût de sortie `None` (aucun devis possible), P&L de prix -100 %, P&L journal = -100 % moins le frais d'entrée comme `_exit_pnl`, capital crédité de 0, `journal.record_exit(..., exit_price=0.0, is_final=True, exit_cost_pct=None, …)`, puis `_finalize`. `None` si la position n'existe pas.
- Produces: `ZOMBIE_GRACE_MINUTES = 30` dans `src/main.py`.

- [ ] **Step 1: tests qui échouent** (`tests/test_positions_zombies.py`, s'inspirer de `tests/test_position_watch.py` et `tests/test_trading.py` pour construire un portefeuille et une boucle factice) :
  - `test_sans_prix_avant_le_delai_la_position_est_gardee`
  - `test_sans_prix_apres_time_stop_et_delai_la_position_est_fermee_a_moins_100` (raison exacte, `pnl_pct` de la jambe ≤ -100, position retirée, ligne `is_final_exit`)
  - `test_un_tp1_deja_encaisse_reste_compte_dans_la_position` (`read_positions()` : P&L = gain TP1 + perte de la part restante)
  - `test_le_prix_revenu_rend_la_main_aux_regles_normales`
  - `test_stop_ferme_aussi_une_position_sans_prix` (raison `PANIC_STOP (sans prix)`)
  - `test_zombie_compte_comme_sortie_automatique` (`position_stats` : `manual_closes == 0`)
- [ ] **Step 2:** `python -m unittest tests.test_positions_zombies -v` → échec.
- [ ] **Step 3:** implémenter les interfaces ; dans `_monitor_arm`, la fermeture se fait sous `_books_lock` comme les autres sorties et ne doit jamais lever (la boucle ne meurt pas) ; une ligne `[Monitor]` explicite.
- [ ] **Step 4:** `python -m unittest tests.test_positions_zombies tests.test_position_watch tests.test_trading tests.test_journal_positions -v` puis `python -m unittest discover -s tests` → OK.
- [ ] **Step 5: Commit** `git commit -m "Positions sans prix : fermées à -100 % après le time stop, et par /STOP (B1)"`
