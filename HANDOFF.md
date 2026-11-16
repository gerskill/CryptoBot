# HANDOFF — 2026-11-16

## Objectif en cours
Tester en PAPIER tous les domaines des marchés de prédiction Liquid, avec la
méthode des bras de CryptobBot, et les suivre dans l'onglet « Liquid » du
dashboard.

## Décisions confirmées
- Processus séparé, lecture seule de l'API publique Hyperliquid, aucune clé,
  aucun code d'exécution (ADR 015).
- Huit bras dans `config/prediction_arms.json`, capital fictif.
- Connecteur Liquid de Claude passé en mode paper le 2026-11-16 (accord Killian).
- Exemple fictif dans un dossier distinct, toujours sous bandeau.

## Fichiers concernés
`src/prediction/`, `scripts/run_prediction.py`, `scripts/prediction_demo.py`,
`scripts/export_vault_prediction.py`, `api/server.py` (routes
`/api/prediction/*`), `dashboard/src/components/liquid/`,
`tests/test_prediction.py`, `docs/PREDICTION_LIQUID.md`.

## Questions ouvertes
- Les formes de réponse de l'API n'ont pas été confrontées à un appel réel :
  lancer `python3 scripts/run_prediction.py --sonde` sur le Mac.
- Nom de classe des marchés « touche », disponibilité des bougies actions.
- Frais au règlement chez Liquid (0,90 % compté par prudence).
- Source de cotes pour `pm_sport_reference`.
- Le journal Obsidian de session n'a pas pu être écrit depuis cette session
  (pas d'accès au dossier du Mac).

## Prochaine action
Sur le Mac mini : `--sonde`, puis `--once`, puis laisser tourner jusqu'à 30
paris réglés sur `pm_temoin`. Aucun chiffre à annoncer avant, ni sans le juge.
