---
name: tests-qualite
description: Écrire, lancer ou réparer les tests de CryptobBot et faire passer le lint. Utilise ce skill dès qu'il est question de tests, de unittest, d'un test qui échoue, de couverture, de ruff, d'oxlint, de « vérifie que ça marche », ou avant de considérer une modification comme terminée — dans ce dépôt un test porte le nom du bug qu'il verrouille, et une correction sans test qui la verrouille sera refaite dans six semaines.
---

# Tests et qualité

## Lancer

```bash
python -m unittest discover -s tests            # toute la suite
python -m unittest tests.test_trading           # un module
python -m unittest tests.test_core.TestScoring  # une classe
cd dashboard && npm run build && npm run lint   # front (tsc + oxlint)
```

**Ruff n'est pas un garde-fou de ce dépôt aujourd'hui.** Le cache `.ruff_cache`
existe mais il n'y a ni `pyproject.toml` ni `ruff.toml` : lancé avec ses règles
par défaut, `ruff check` remonte plus de 600 constats préexistants, presque
tous stylistiques (`Optional[X]` contre `X | None`, `# noqa` jugés inutiles).
Un signal noyé dans 600 lignes n'est pas un signal. Tant qu'aucune
configuration n'est écrite, le seul garde-fou Python est la suite de tests —
et le dire vaut mieux que faire semblant de linter.

Les tests utilisent `unittest` (pas pytest, malgré le `.pytest_cache` résiduel)
et insèrent la racine du dépôt dans `sys.path` en tête de fichier. Aucun test ne
touche le réseau : les APIs sont doublées, et un test qui appellerait GMGN ou
Birdeye serait à la fois lent et faux.

## Écrire un test ici

- **En français**, docstring comprise.
- **Le nom décrit le bug verrouillé**, pas la méthode appelée :
  `test_donnee_absente_ne_rejette_pas` plutôt que `test_rejection_reason_3`.
  Quand le test cassera dans un an, son nom doit suffire à comprendre ce qu'on
  perdait.
- Un helper de construction plutôt qu'un objet géant recopié :
  `make_candidate(symbol="TEST", holders=None)` — voir `tests/test_core.py`.
- **Toujours le cas « donnée absente »** : c'est l'invariant le plus violé du
  dépôt. `None` doit passer, `0` doit être traité comme une vraie valeur.
- Pour l'immuabilité : vérifier qu'une opération retourne une **nouvelle**
  instance (`Candidate`, `Position` sont frozen).
- Pour l'apprentissage : verrouiller les trois états — ajusté, rien à ajuster,
  et **pas assez de données**. Confondre les deux derniers est le bug type.

## Ce que chaque module de test protège

| test | ce qu'il verrouille |
|---|---|
| `test_core` | rate limiter, cache, scoring, analyse technique |
| `test_pipeline_split` | collecte partagée / évaluation par bras |
| `test_funnel`, `test_funnel_rotation` | entonnoir de décision |
| `test_garde_fous` | filtres et invariants de rejet |
| `test_trading`, `test_exit_replay`, `test_ladder_breakeven` | sorties |
| `test_exit_fees`, `test_slippage_buffer_recalibration` | coût de sortie et tampon |
| `test_journal_positions` | sorties finales vs partielles |
| `test_arms`, `test_mise_en_commun` | manifeste, bornes par bras |
| `test_calibration` | ajustements et bornes |
| `test_shadow`, `test_correlation`, `test_economics` | shadow, corrélation, garde économique |
| `test_api_server`, `test_state`, `test_metrics` | contrat de l'API |
| `test_agents_*` | agents de mesure |
| `test_lock`, `test_dev_watchdog`, `test_quota_agent`, `test_budget` | exploitation |

## Définition de « terminé »

Une modification n'est finie que si : la suite passe en entier, un test nomme
le bug corrigé, le front compile et passe `oxlint` s'il a bougé, et le
`CONTEXT.md` ou l'ADR concerné est à jour si le comportement documenté a
changé.
