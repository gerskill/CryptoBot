# HANDOFF — 2026-11-18

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
- Prochain chantier : **B1 positions zombies** (conception courte d'abord).
- Skill `rentabilite-annuelle` : pas de rendement annualisé sous 30 jours et
  30 positions ; aucune suppression de bras hors ADR 011.

## Preuves
- `python -m unittest discover -s tests` : 1296 OK.
- `cd dashboard && npm test && npm run build && npm run lint` : OK.
- Chaque tâche relue par un sous-agent indépendant, revue finale de branche
  sur le modèle le plus capable ; mutations vérifiées (retirer `until=` ou
  inverser l'ordre de `run()` fait échouer un test).

## À faire par Killian
1. Sur le Mac : `git pull`, `pip install -r requirements.txt`,
   `cd dashboard && npm ci && npm run build`, puis relancer le bot (ou
   `./scripts/installe_services.sh`, voir `docs/HEBERGEMENT.md`).
2. Supprimer les branches mortes (commande dans `docs/FEUILLE_DE_ROUTE.md`,
   K1) ; protéger `main` (K2).
3. Surveiller les lignes `🧠 recherche de sorties` par bras : si
   « indéterminé » domine, agrandir la fenêtre de test (pas baisser le seuil).

## Questions ouvertes
- Verrou hors échantillon des filtres : à décider sur mesure.
- `scripts/audit.py` mesure encore le reste au breakeven sur tout le journal.
- Liquid : taille minimale et frais au règlement [À VÉRIFIER] ; plafond de
  5 paris par domaine ; écran Liquid à clarifier.

## Prochaine action
Conception de B1 : borne de durée et sortie au pire cas pour une position
sans prix, positions ouvertes visibles dans les chiffres.
