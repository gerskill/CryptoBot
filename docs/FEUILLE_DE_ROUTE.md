# Feuille de route — priorités (impact / effort)

Établie le 2026-11-17 après relecture du dépôt (`CONTEXT.md`, `README.md`,
ADR 001-018). Versionnée exprès : `.scratch/` est gitignoré et n'atteint ni
GitHub ni les sessions cloud. Chaque ligne dit **qui** peut la faire et
**quand elle est finie**.

## Fait le 2026-11-18 (branche `claude/eager-franklin-p2gasg`, plan `docs/superpowers/plans/2026-11-17-…`)

- **A2** — la recherche de sorties choisit son couple (stop, TP1) sur les 2/3
  les plus anciens des positions instrumentées (triées, dédoublonnées entre
  bras à ±300 s) et ne l'applique que si un bootstrap apparié sur le tiers
  récent a une borne basse > 0 avec au moins 5 positions au P&L changé.
  Validation dans l'échantillon avant la recherche ; aucune mesure faite sur
  le futur. Reste : verrou hors échantillon des **filtres** (aujourd'hui
  mesure « tiers récent » seulement) — à décider sur mesure.
- **A3** — `PARAMETRES_NON_APPLIQUES` (6 clés, dont `max_tax_buy`/`max_tax_sell`)
  + test qui échoue sur tout nouveau paramètre mort ; `voir_bras.py` les signale.
- **A6** — `npm test` (vitest) dans `dashboard/` ; le panneau « P&L par bras »
  est verrouillé par un test de rendu.
- Skill `rentabilite-annuelle` ; aiguillage Liquid ; audit du 2026-11-17
  (`docs/AUDIT_2026-11-17.md`).

## Fait le 2026-11-18 — B1 positions zombies

Une position restée sans prix exploitable 30 min après son time stop est fermée
à -100 % (`ZOMBIE (sans prix depuis le time stop)`) ; `/STOP` ferme aussi les
positions sans prix (`PANIC_STOP (sans prix)`, après un nouvel essai de prix).
Le journal ne coupe plus le frais d'entrée à la relecture (plancher
-100 % − frais). **Au premier cycle après déploiement, les zombies existants
seront fermés : baisse brute et voulue des chiffres des bras concernés.**
Prochains candidats de l'audit : M2 (entrées perdues), B2 (verdict fragile),
B4 (gel des paramètres par période).

## À surveiller sur le Mac après fusion

Lignes `🧠 recherche de sorties` par bras : part de « indéterminé » avec moins
de 5 positions changées. Si elle domine, agrandir la fenêtre de test plutôt que
baisser le seuil de 5.

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
| A4 | **Préparation du LIVE — l'ADR seulement**, pas le module : conditions mesurables (critères de `live_mode_allowed()`, IC95 concluant, coût réel par jambe), périmètre minimal, coupe-circuit | élevé à terme | faible (écrit) | `adr-documentation`, `securite-exploitation` | ADR « proposé » soumis à Killian ; aucun code d'exécution écrit |
| A5 | Coût réel (devis Jupiter) sur les jambes partielles, en échantillon, pour vérifier l'estimation de l'ADR 012 | moyen | moyen (quota Jupiter 1 req/s) | `sorties-positions` | écart estimation / devis mesuré et publié |

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
