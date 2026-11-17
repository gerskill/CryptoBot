# HANDOFF — 2026-11-17 (après-midi)

## Objectif en cours
Rangement du dépôt et outillage d'exploitation : P&L par bras visible, lecture
d'un bras sans jeton, services qui redémarrent seuls, veille Telegram. PAPER.

## Décisions confirmées
- PR #12 puis #13 fusionnées (Liquid « comme le réel », ADR 018 ; bascule
  Helius). PR #4, #5, #8 fermées : contenu déjà dans `main` (preuves en
  commentaire). PR #11 fermée : HANDOFF dépassé.
- Le jeton reste obligatoire sur `/api/params` (tunnel Cloudflare = requêtes
  depuis 127.0.0.1). Lecture locale : `scripts/voir_bras.py`.
- Hébergement : rester sur le Mac mini, en services launchd
  (`docs/HEBERGEMENT.md`). Le quick tunnel Cloudflare est gratuit.
- Règles git : `docs/agents/workflow-git.md`.

## Fichiers concernés
`src/core/stats.py` (`position_stats`, `window_performance`), `api/server.py`
(`/api/arms/performance`), `dashboard/src/components/weekly/ArmPerformance.tsx`,
`scripts/voir_bras.py`, `scripts/veille.py`, `scripts/installe_services.sh`,
`scripts/launchd/`, `tests/test_pont_pytest.py`, `docs/FEUILLE_DE_ROUTE.md`.

## Preuves
- `python -m unittest discover -s tests` : 1242 OK, plus 56 tests style pytest
  via le pont (ils affichaient « Ran 0 » avant).
- `cd dashboard && npm run build && npm run lint` : OK, 0 avertissement.

## À faire par Killian (détail : `docs/FEUILLE_DE_ROUTE.md`, K1-K6)
1. Supprimer les branches mortes (refusé depuis la session cloud) :
   `git push origin --delete audit-outillage claude/liquid-prediction-paper claude/bascule-helius claude/liquid-compte-reel claude/handoff-apres-bascule cursor/helius-429-5b0b cursor/position-watch-5b0b cursor/remove-narrative-arm-16ef cursor/research-journal-5b0b cursor/tp1-floor-quality-consensus-8efc`
2. GitHub : suppression auto des branches fusionnées + protection de `main`.
3. Mac : `git pull`, `pip install -r requirements.txt`, `cd dashboard && npm ci && npm run build`,
   arrêter les processus manuels, `./scripts/installe_services.sh`,
   `sudo pmset -a sleep 0 autorestart 1`.
4. healthchecks.io → `HEALTHCHECK_PING_URL` dans `.env`.

## Questions ouvertes
- Taille minimale réelle Liquid et frais au règlement [À VÉRIFIER] (inchangé).
- Plafond de 5 paris ouverts par domaine : sport plein (333 refus au 16/11).
- Écran Liquid jugé peu clair : quoi afficher en premier ?
- Taux de remplissage réel de `entry_smart_money_buys_30m` (A1).

## Prochaine action
K1-K3 ci-dessus, puis A1 (mesure smart money) et A2 (validation hors
échantillon dans `LearningEngine`). Aucun chiffre de bras à annoncer sans le
juge ni sous 30 trades.
