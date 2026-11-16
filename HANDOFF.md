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

## État sur le Mac mini (2026-11-16, 15:52 UTC)
- PR n° 10 fusionnée par Killian ; dépôt principal à jour (`8d6eaa2`).
- Port 8000 (`scripts/run_ui.py`) : espaces Memecoins et Liquid servis ensemble.
  Ancien site gardé dans `dashboard/dist.avant-liquid-20261116`.
- `scripts/run_prediction.py` tourne depuis le dépôt principal, données dans
  `data/prediction/`. Lancé à la main : ne redémarre pas seul avec le Mac.
- Sauvegarde avant bascule : `backups/20261116-164918`.
- Relevé à 15:49 UTC : 65 paris papier ouverts, 15 réglés (tous en seuils
  crypto, 4 au plus par bras), 4 indéterminés. Aucun chiffre à annoncer.
- Vault Obsidian : notes générées dans `CryptoBot/Liquid/`, analyse dans
  `CryptoBot/Liquid — analyse 2026-11-16.md`.

## Constats à trancher
- Plafond de 5 paris ouverts par domaine : le sport est plein (333 refus
  « exposition_max » au dernier cycle).
- Killian trouve l'écran Liquid peu clair : définir ce qui s'affiche en premier.
- Faux signaux restants dans le journal d'incohérences (marchés non cotés vers 49 %).

## Prochaine action
Refonte de l'écran d'accueil Liquid selon la réponse de Killian. Aucun chiffre
à annoncer avant 30 paris réglés par bras, ni sans le juge.
