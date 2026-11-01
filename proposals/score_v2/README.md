# Proposition score_v2 — NON APPLIQUÉE

Rapport : docs/score_calibration_2026-10-10.md

Contenu :
- `score_v2.patch` : diff unifié (git apply -p1) — ajoute `src/core/score_v2.py`, la porte opt-in dans
  `AlphaLoop._try_entries` (src/main.py, `entry_rules.score_v2_min_prob`, 0 = désactivée), le bras
  `score_v2` dans config/strategies.json (role test, learning false, capital_pct 0, override
  `entry_rules.score_v2_min_prob: 0.205`) et `tests/test_score_v2.py` (10 tests).
- `files/` : copies lisibles des deux nouveaux fichiers.
- `analysis/analysis.tgz` : scripts d'analyse + sortie complète (results.txt).

Validé dans une copie isolée du dépôt (sans .env ni data) : `git apply --check` OK, 839 tests passent
(829 existants + 10 nouveaux).

Déploiement (seulement après accord de Killian) :
1. `./scripts_backup.sh`
2. `git apply -p1 proposals/score_v2/score_v2.patch`
3. `/usr/local/bin/python3 -m pytest -q tests`
4. Redémarrer le bot seul (skill cryptobbot-demarrage-propre), sans couper le dashboard.
   `config/arms/score_v2.json` est créé au démarrage comme copie du document vivant du témoin.
Retrait : `"enabled": false` sur le bras dans config/strategies.json.
