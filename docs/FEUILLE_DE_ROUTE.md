# Feuille de route — priorités (impact / effort)

Établie le 2026-11-17 après relecture du dépôt (`CONTEXT.md`, `README.md`,
ADR 001-018). Versionnée exprès : `.scratch/` est gitignoré et n'atteint ni
GitHub ni les sessions cloud. Chaque ligne dit **qui** peut la faire et
**quand elle est finie**.

## Fait le 2026-11-17

- PR #12 et #13 fusionnées ; #4, #5, #8 fermées (contenu déjà dans `main`,
  preuve en commentaire) ; #11 fermée (HANDOFF dépassé).
- P&L par bras 7 j / 30 j / tout : `/api/arms/performance`, panneau
  « P&L par bras » (onglet Weekly), `scripts/voir_bras.py --perf`.
- Lecture d'un bras sans jeton : `scripts/voir_bras.py` (fichiers, pas l'API).
- Services launchd + veille Telegram : `scripts/installe_services.sh`,
  `scripts/veille.py`, `docs/HEBERGEMENT.md`.
- 56 tests style pytest qui ne tournaient jamais : branchés par
  `tests/test_pont_pytest.py`.

## À faire — par Killian (décision ou accès que les agents n'ont pas)

| # | action | effort | fini quand |
|---|---|---|---|
| K1 | Supprimer les branches mortes (session cloud : suppression refusée par le proxy git) — commande dans `HANDOFF.md` | 2 min | `git branch -r` ne liste que `main` et les branches ouvertes |
| K2 | Activer « Automatically delete head branches » et la règle de protection de `main` (`docs/agents/workflow-git.md`) | 5 min | un push direct sur `main` est refusé |
| K3 | Sur le Mac : arrêter les processus manuels, `./scripts/installe_services.sh`, `sudo pmset -a sleep 0 autorestart 1` | 10 min | `launchctl list \| grep cryptobbot` montre 4 services ; reboot → tout revient |
| K4 | Créer un check healthchecks.io, mettre `HEALTHCHECK_PING_URL` dans `.env` | 5 min | couper le Wi-Fi 20 min → e-mail reçu |
| K5 | Vérifier la facture Cloudflare (le quick tunnel est gratuit, voir `docs/HEBERGEMENT.md`) ; envisager Tailscale | 10 min | plus de ligne payée sans usage |
| K6 | Décider si le passage en LIVE doit être préparé (ADR d'abord, voir A4) | décision | ADR accepté ou refusé |

## À faire — par un agent (priorisé)

| # | sujet | impact | effort | skill | fini quand |
|---|---|---|---|---|---|
| A1 | **Mesurer le remplissage de `smart_money_buys_30m`** sur le Mac (`python scripts/feature_coverage.py --prefix entry_smart_money`, champ `entry_smart_money_buys_30m` du journal). Le champ vient de `gmgn-cli` ; il retombe à `None` si le CLI ou sa clé manque | haut (le filtre et le composant du score en dépendent) | 15 min | `pipeline-scan` | taux de remplissage chiffré dans `docs/ETAT_DU_PROJET.md` |
| A2 | **Validation hors échantillon des ajustements** : brancher `stats.walk_forward()` / `overfit_gap()` dans la décision de `LearningEngine` (aujourd'hui rejeu sur les mêmes trades seulement) | haut — protège les 19 bras du surapprentissage | moyen | `apprentissage-garde-fous`, `chasse-au-lookahead` | un ajustement qui gagne en échantillon et perd hors échantillon est annulé, test à l'appui |
| A3 | **`min_liquidity_lock_days` n'a aucun effet** : présent dans les paramètres, lu nulle part dans `src/`. Soit le vérifier on-chain (programmes de lock LP), soit le retirer des fichiers pour ne pas laisser croire qu'il filtre | moyen | retrait : faible ; vérification : élevé | `pipeline-scan` | paramètre appliqué avec test, ou retiré avec ADR |
| A4 | **Préparation du LIVE — l'ADR seulement**, pas le module : conditions mesurables (critères de `live_mode_allowed()`, IC95 concluant, coût réel par jambe), périmètre minimal, coupe-circuit | élevé à terme | faible (écrit) | `adr-documentation`, `securite-exploitation` | ADR « proposé » soumis à Killian ; aucun code d'exécution écrit |
| A5 | Coût réel (devis Jupiter) sur les jambes partielles, en échantillon, pour vérifier l'estimation de l'ADR 012 | moyen | moyen (quota Jupiter 1 req/s) | `sorties-positions` | écart estimation / devis mesuré et publié |
| A6 | Panneau « P&L par bras » : test de rendu qui vérifie que chaque ligne affiche son bras (aujourd'hui garanti côté serveur seulement) | faible | faible | `api-dashboard` | test navigateur en place |

## Skills et MCP — ce qui apporte vraiment quelque chose

- **Aucun nouveau skill à installer pour l'instant.** Les signaux de
  `gmgn-holder-analysis` (bundlers, insiders, snipers, rats) sont **déjà**
  des filtres du pipeline (`max_bundler_rate`, `max_insider_rate`,
  `max_sniper_count`, `max_entrapment_ratio`…), et `gmgn-track` alimente déjà
  `smart_money_buys_30m`. Mesurer A1 avant d'ajouter quoi que ce soit.
- **MCP utiles** :
  1. *GitHub* — déjà connecté ; sert au rangement des PR (fait aujourd'hui).
  2. *Desktop Commander* — déjà configuré, mais il agit sur la machine où
     tourne la session : vérifié le 2026-11-17, dans une session cloud il voit
     le conteneur Linux, pas le Mac. Le journal Obsidian
     (`~/Documents/Obsidian Vault/CryptoBot/Journal.md`) ne s'écrit donc que
     depuis une session lancée SUR le Mac, ou en attachant le dossier du Mac
     à la session cloud (Remote Control).
  3. *Un MCP RPC Solana en lecture seule* (Helius ou Chainstack) — pour
     A3 (verrous LP) et les enquêtes on-chain des agents ; à n'installer
     qu'avec une clé dédiée, distincte de celle du bot (quota Helius déjà
     épuisé, PR #12).
- **Écartés** : skills Jupiter Lend / VRFD (rien à voir avec le bot),
  CoinGecko (ne couvre pas les memecoins de quelques heures).
