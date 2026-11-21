# App iPhone CryptoBot — installation pas à pas

L'app (`mobile/`) montre sur l'iPhone l'état du bot, les positions ouvertes, le
P&L par bras et l'espace Liquid, et permet de **piloter** le bot depuis l'onglet
Pilotage (arrêt d'urgence, reprise, activer/désactiver un bras Liquid, créer un
bras Liquid). Mode PAPER : aucun ordre réel possible (ADR 021).

**État au 2026-11-21** : projet Supabase `uabnczoqnpzctgclmcrn` créé, schéma
exécuté, inscriptions fermées, compte de Killian créé, `mobile/config.js`
rempli, GitHub Pages activé. Reste l'étape 4 (le Mac) puis l'étape 5 (l'iPhone).

Comment ça marche : toutes les 2 min, le Mac publie des résumés dans Supabase
(`scripts/publie_supabase.py`) ; l'app, hébergée sur GitHub Pages, les lit
après connexion à ton compte. Passé 10 min sans publication, l'app affiche
« Aucune nouvelle depuis X min » et grise les chiffres.

À faire une seule fois, dans l'ordre.

## 1. Supabase : le projet, la table, ton compte

1. Sur [supabase.com](https://supabase.com), crée un **nouveau projet** dédié,
   par exemple « cryptobot ».
2. **SQL Editor → New query** : colle tout le contenu de `supabase/schema.sql`,
   puis **Run**. (Relançable sans risque.)
3. **Authentication → Users → Add user → Create new user** : ton e-mail et un
   mot de passe solide, case « Auto Confirm User » cochée.
4. **Authentication → Sign In / Providers → désactiver « Allow new users to
   sign up »** après avoir créé ton compte. Sinon n'importe qui pourrait se
   créer un compte avec la clé publique de l'app et stocker des données dans
   ton projet.
5. **Project Settings → API Keys** : copie la **Project URL** et la **clé
   publiable** (`sb_publishable_…`, ou l'ancienne clé « anon public »). Ne
   copie **jamais** la clé secrète (`sb_secret_…` / `service_role`).

## 2. Relier l'app à Supabase : `mobile/config.js`

Remplis les deux valeurs, puis commite et pousse sur `main` :

```js
window.CRYPTOBOT_CONFIG = {
  supabaseUrl: 'https://xxxxxxxx.supabase.co',
  supabaseAnonKey: 'sb_publishable_…',   // la clé publiable
};
```

La clé `anon` est faite pour être publique (le dépôt l'est) : sans connexion à
ton compte elle ne donne accès à rien (RLS, aucun droit `anon` sur la table).
Tant que ce fichier est vide, l'app affiche « L'app n'est pas encore reliée à
Supabase : voir docs/APP_IPHONE.md. »

## 3. GitHub Pages

Sur GitHub : dépôt **CryptoBot → Settings → Pages → Build and deployment →
Source : « GitHub Actions »**.

L'action `.github/workflows/pages.yml` publie `mobile/` à chaque push sur
`main` qui touche `mobile/` (ou à la main : onglet **Actions → App iPhone
(GitHub Pages) → Run workflow**). Adresse :
**https://gerskill.github.io/CryptoBot/**

## 4. Le Mac : publier les résumés

1. Dans le `.env` du Mac (jamais commité), ajoute les 4 variables (modèle dans
   `.env.example`) :
   ```
   SUPABASE_URL=https://xxxxxxxx.supabase.co
   SUPABASE_ANON_KEY=sb_publishable_…   # la même clé publiable que mobile/config.js
   SUPABASE_EMAIL=ton-email
   SUPABASE_PASSWORD=ton-mot-de-passe
   ```
2. Vérifie ce qui partirait, sans rien envoyer :
   `python scripts/publie_supabase.py --sans-envoi`
3. Installe le service (toutes les 2 min) :
   `./scripts/installe_services.sh publie`
   Journal : `logs/publie.log`.

## 5. L'iPhone

1. Ouvre **Safari** (pas un autre navigateur) à l'adresse
   https://gerskill.github.io/CryptoBot/
2. **Partager → « Sur l'écran d'accueil »** → Ajouter.
3. Ouvre CryptoBot depuis l'écran d'accueil et connecte-toi avec l'e-mail et
   le mot de passe de l'étape 1. La session reste ouverte.

## Ce que dit l'app

- **État** : une phrase (« Le bot tourne. Dernier cycle il y a 2 min. »), la
  boucle Liquid, et les alertes des dernières 24 h (sorties zombie, arrêts
  d'urgence, services en panne).
- **Positions**, **Bras** (7 j / 30 j / tout, avec l'intervalle de confiance du
  WR et le verdict face au témoin), **Liquid** : chaque ligne nomme son bras ;
  les totaux sont dits « flotte (somme) ».
- Actualisation toutes les 60 s quand l'app est ouverte, en tirant l'écran vers
  le bas, ou par le bouton « Actualiser ».
- Les chiffres ne sont jamais mis en cache : hors réseau, l'app dit l'âge de la
  dernière lecture.

## Pilotage (onglet Pilotage)

- **Arrêt d'urgence** : comme /STOP sur Telegram — ferme toutes les positions
  papier et met les entrées en pause. **Reprendre les entrées** : comme /RESUME.
- **Bras Liquid** : activer ou désactiver un bras (ses paris ouverts sont encore réglés).
- **Créer un bras Liquid** : copie d'un bras avec UNE variable changée, dans
  les bornes du site (ADR 017).

Chaque action demande confirmation, puis part dans la table `commandes`. Le Mac
la lit au plus 2 min plus tard, la valide comme le site, l'applique et écrit
« appliquée » ou « refusée » (avec la raison) dans **Dernières commandes**. Une
commande non lue sous 15 min (Mac éteint) est refusée, jamais appliquée en retard.
Telegram reste le chemin le plus rapide pour un arrêt d'urgence.

## Si ça ne marche pas

| ce que l'app affiche | cause probable |
|---|---|
| « pas encore reliée à Supabase » | `mobile/config.js` vide sur `main` |
| « E-mail ou mot de passe incorrect. » | compte absent ou non confirmé (étape 1.3) |
| « Pas encore de données : le Mac n'a rien publié. » | service `publie` non installé ou `.env` incomplet : voir `logs/publie.log` |
| « Aucune nouvelle depuis X min » | Mac éteint, publication ou bot arrêté |
| « Session expirée : reconnecte-toi. » | reconnecte-toi, c'est tout |
| commande restée « en attente » | service `publie` arrêté : voir `logs/publie.log` |
| commande « refusée — expirée » | le Mac ne l'a pas lue sous 15 min (éteint, en veille) |

Test de l'app sans Supabase : `mobile/test/README.md`.
