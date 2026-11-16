# HANDOFF — 2026-11-16 (soir)

## Objectif en cours
Tester en PAPIER tous les domaines des marchés de prédiction Liquid avec des
bras rangés par famille, et les suivre dans l'espace « Liquid » du site
(séparé de l'espace « Memecoins »).

## Décisions confirmées
- Processus séparé, lecture seule de l'API publique Hyperliquid (ADR 015).
- Domaines, familles de bras, rôle `regle`, site en deux espaces (ADR 016).
- Modifications depuis le site par boîte de demandes : activer/désactiver,
  créer un fork à une variable (ADR 017). Rien vers la boucle memecoin.
- Quatorze bras dans `config/prediction_arms.json` (un désactivé).
- « Tout enregistrer » (accord Killian) : catalogue, perpétuels, relevé de
  tous les prix toutes les 15 minutes.
- Connecteur Liquid de Claude en mode paper.

## Preuves
- Formes réelles de l'API : `tests/fixtures/outcome_meta_2026-11-16.json`
  (relevé depuis le Mac mini). Avant correction : 4 marchés lus sur 309.
- Tests : `python -m unittest discover -s tests`.
- PR : https://github.com/gerskill/CryptoBot/pull/10

## Fichiers concernés
`src/prediction/`, `config/prediction_arms.json`, `api/server.py`
(`/api/prediction/*`, `/api/liquid/perps`), `dashboard/src/components/liquid/`,
`dashboard/src/components/NavBar.tsx`, `Shell.tsx`, `tests/test_prediction.py`,
`docs/PREDICTION_LIQUID.md`, `docs/adr/016-…`.

## Questions ouvertes
- Sens exact de `settledNamedOutcomes` : à lire sur le premier match réglé.
- Frais au règlement chez Liquid (0,90 % compté par prudence).
- Ordre des tranches de prix (`priceBucket`) : non documenté, non parié.
- Source de cotes pour `pm_sport_reference`.
- Bras papier sur perpétuels : pas décidé, aujourd'hui affichage seul.

## État sur le Mac mini (2026-11-16, 15:20 UTC)
- PR n° 10 NON fusionnée : `main` et le site du port 8000 n'ont pas l'espace Liquid.
- Copie à part `~/Projects/CryptobBot-liquid` (worktree de la branche) : la
  simulation y tourne (`scripts/run_prediction.py`, journal dans
  `data/prediction/runner.log`) et un aperçu du site est servi sur
  http://127.0.0.1:8001. Les deux s'arrêtent au redémarrage du Mac.
- Premier cycle réel : 55 paris papier ouverts, aucun réglé.
- Le journal d'incohérences contient encore des artefacts de marchés non cotés
  affichés vers 49 % : à filtrer par lecture du carnet avant d'en tirer un bras.

## Prochaine action
Killian fusionne la PR n° 10. Ensuite : `git pull` dans `~/Projects/CryptobBot`,
`cd dashboard && npm ci && npm run build`, déplacer
`CryptobBot-liquid/data/prediction` vers `CryptobBot/data/prediction`,
relancer `scripts/run_ui.py` et `scripts/run_prediction.py` depuis le dépôt
principal. Aucun chiffre à annoncer avant 30 paris réglés par bras, ni sans le juge.
