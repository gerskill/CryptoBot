# HANDOFF — 2026-11-17

## Objectif en cours
Liquid en papier « comme le réel » : chaque bras joue un compte de 46 $ (taille du vrai compte), avec un agent d'apprentissage par bras. Aucun ordre réel.

## Décisions confirmées
- Papier = réel (ADR 018, PR #13) : solde par bras, taille minimale Hyperliquid (règle la plus sévère), parts entières, carnet partagé, plus de pari « indéterminé » (perdu par prudence après 24 h), tri départagé.
- Agent d'apprentissage : diagnostic par bras ; fork à une variable, ≥ 20 paris réglés, segment ≥ 10, resserrer seulement, jamais de modification d'un bras existant.
- Saison 1 (1 000 $, mises de 20 $) archivée : `data/prediction/archive/saison1_1000usd_20261117/`. Ses 83 paris ouverts ne sont plus suivis.
- Hook `pre-commit` réparé, bascule Helius (PR #12). PR #13 part de la branche de #12 : fusionner #12 d'abord.
- Rapport par bras du 17/11 : verdict du juge REFUSÉ, aucun bras memecoin ni Liquid ne passe les critères (`scripts/out/rapport_bras/`).

## État sur le Mac mini
- Dépôt sur la branche `claude/liquid-compte-reel` (non fusionnée). Moteur Liquid et site relancés sur ce code.
- Site public : tunnel Cloudflare rapide (adresse dans `logs/tunnel.log`, change si le tunnel redémarre). `DASHBOARD_TOKEN` dans `.env`.
- Bot memecoin : processus de dimanche, ancien code en mémoire. Helius toujours épuisé.

## Questions ouvertes
- Taille minimale réelle (parts × min(p, 1 − p) ou parts × p ≥ 10 $) [À VÉRIFIER].
- Frais Liquid au règlement (0,9 % compté par prudence) [À VÉRIFIER].
- Adresse fixe : tunnel Cloudflare nommé (domaine requis) ou Tailscale.
- Rapport memecoin : ajouter coûts par position, base de capital des bras test, période (demandes du juge).

## Prochaine action
Fusionner #12 puis #13, puis relancer le bot memecoin pour qu'il prenne la bascule Helius. Revoir les bras Liquid quand chacun a 30 paris réglés.
