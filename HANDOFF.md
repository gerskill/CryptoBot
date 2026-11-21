# HANDOFF — 2026-11-21 (soir)

## Objectif en cours
Rendre la mesure des bras digne de confiance avant toute question de
rentabilité (« quel bras fait +120 %/an ? »). PAPER uniquement.

## Décisions confirmées (Killian)
- Audit du 2026-11-17 : `docs/AUDIT_2026-11-17.md` (constats vérifiés),
  rapports bruts dans `docs/audits/`. Aucune conclusion de rentabilité par
  bras n'est possible tant que B1-B4 ne sont pas réglés.
- A2/A3/A6 faits (plan `docs/superpowers/plans/2026-11-17-…`, spec
  `docs/superpowers/specs/2026-11-17-…`) : recherche de sorties confirmée
  hors échantillon, paramètres non appliqués listés et verrouillés par test,
  `npm test` dans `dashboard/`.
- B1 fait : positions sans prix fermées à -100 % après le time stop + 30 min, et par `/STOP` (spec `docs/superpowers/specs/2026-11-18-positions-zombies-design.md`).
- App iPhone faite (spec `docs/superpowers/specs/2026-11-18-app-iphone-design.md`) : PWA GitHub Pages + Supabase, lecture seule ; à mettre en route avec `docs/APP_IPHONE.md`.
- 2026-11-21 : app iPhone reliée à Supabase + pilotage (ADR 021), en service.
  Mac sur `main` (il tournait sur `claude/liquid-compte-reel`, déjà incluse) ;
  5 services launchd : bot, ui, prediction, veille, publie. Publication
  vérifiée dans Supabase (toutes les ~2 min). Zombies au redémarrage : 0
  (17 positions, toutes avec prix ; `grep ZOMBIE logs/bot.log` vide).
- 2026-11-21 (soir) : PR #20 (seuil Liquid 600 s), #21 (cache des carnets
  Hyperliquid, cycle 146 s → 100 s), #22 (app iPhone en charte Alpha Loop) et
  correction de `installe_services.sh` (attente du bootout + 4 essais ; avant,
  « Bootstrap failed: 5 » laissait ui/prediction arrêtés). Consigne de
  Killian : faire commit, push, PR et fusion sans redemander, et proposer des
  idées en plus.
- Constat 2026-11-21 (journal du Mac) : OGTA est resté 31 h 47 ouvert sans que
  le stop -25 % joue (sortie à -100,9 %, ancien code) = le cas B1. Contrôles
  on-chain en `error` depuis au moins le 19/11 (Helius), Birdeye à court de
  quota : bundlers/insiders/snipers vides, et le pipeline ne rejette pas un
  champ vide (`src/pipeline.py` ~981-1005). `anti_rug` et les consensus ne
  sont pas jugeables tant que ces données manquent.
- Skill `rentabilite-annuelle` : pas de rendement annualisé sous 30 jours et
  30 positions ; aucune suppression de bras hors ADR 011.

## Preuves
- `python -m unittest discover -s tests` : 1355 OK ; `node mobile/test/smoke.mjs` : 79 ok.
- `cd dashboard && npm test && npm run build && npm run lint` : OK.
- Chaque tâche relue par un sous-agent indépendant, revue finale de branche
  sur le modèle le plus capable ; mutations vérifiées (retirer `until=` ou
  inverser l'ordre de `run()` fait échouer un test).

## À faire par Killian
1. Tester une commande depuis l'onglet Pilotage (ex. désactiver puis
   réactiver un bras Liquid) : statut attendu « appliquée ».
2. Supprimer les branches mortes (commande dans `docs/FEUILLE_DE_ROUTE.md`,
   K1) ; protéger `main` (K2).
3. Surveiller les lignes `🧠 recherche de sorties` par bras : si
   « indéterminé » domine, agrandir la fenêtre de test (pas baisser le seuil).

## Questions ouvertes
- Verrou hors échantillon des filtres : à décider sur mesure.
- `scripts/audit.py` mesure encore le reste au breakeven sur tout le journal.
- Liquid : taille minimale et frais au règlement [À VÉRIFIER] ; plafond de
  5 paris par domaine ; écran Liquid à clarifier.

- Birdeye : quota mensuel épuisé le 2026-11-21 (module désactivé) ; les
  entrées jusqu'au renouvellement ont moins de données. Helius : 429 vus.
- Lecture du 2026-11-21 (juge : REFUSÉ puis corrigée) : flotte négative sur
  toutes les périodes disjointes ; aucun bras démontré rentable.

## Prochaine action
Relire le 7 j dans une semaine (fenêtre disjointe, pas emboîtée). Puis
choisir M2, B2 ou B4.
