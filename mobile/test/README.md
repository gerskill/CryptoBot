# Test de fumée de l'app iPhone

```
node mobile/test/smoke.mjs
node mobile/test/smoke.mjs --captures /chemin/vers/dossier   # + captures d'écran iPhone
```

Prérequis : Node 18+ et Playwright avec Chromium (`npm i -g playwright && npx playwright install chromium`).

Le test sert `mobile/` en local, remplace `vendor/supabase.js` par un faux
client Supabase (aucun réseau) et `config.js` par des valeurs factices, puis
vérifie dans un profil iPhone 15 : le message quand `config.js` est vide,
l'écran de connexion sans session, les quatre onglets avec des données de la
forme exacte du contrat (`fixtures.mjs`), le bandeau « Aucune nouvelle » au-delà
de 10 min, le retour à la connexion quand la session a expiré, l'absence de
requête hors de l'app et les zones tactiles de 44 px.

Sortie attendue : une ligne `ok` par vérification, puis `Tout est vert.` (code 0).
