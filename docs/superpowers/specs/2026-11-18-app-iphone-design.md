# Conception — app iPhone CryptoBot (PWA GitHub Pages + Supabase)

Date : 2026-11-18. Auteur : Claude, avec Killian. Mode PAPER, lecture seule.

## Intention

Killian veut suivre le bot depuis son iPhone, comme ses apps Coach Killian et
Prompt Studio (PWA sans build, hébergées sur GitHub Pages, installées par
Safari → « Sur l'écran d'accueil » ; Coach lit ses données dans Supabase avec
connexion e-mail + mot de passe et RLS par utilisateur). Les données de
CryptoBot vivent sur le Mac mini (`data/`) : le Mac publie des **résumés** dans
Supabase, l'app les lit. Aucun port ouvert, adresse fixe, l'app marche même si
le tunnel Cloudflare est tombé.

## Objectifs
Quatre écrans, dans cet ordre d'importance :
1. **État du bot + alertes** : en ligne ou non (âge du dernier cycle), boucle
   memecoin et Liquid, alertes récentes (fermetures `ZOMBIE`, pannes).
2. **Positions ouvertes** : par bras — token, P&L latent %, taille, durée.
3. **P&L par bras** : 7 j / 30 j / tout, comme `/api/arms/performance`
   (trades, WR avec IC95, PF, P&L, drawdown, verdict contre le témoin).
4. **Espace Liquid** : par bras de prédiction — solde (compte de 46 $),
   paris ouverts, réglés, gagnés nets.

## Non-objectifs
- Aucune action depuis l'app (pas de `/STOP`, pas de fork Liquid, pas de
  modification de paramètre).
- Pas de graphique historique au premier jet.
- Pas d'app native, pas d'App Store.

## Anti-objectifs (échec même si « ça marche »)
- **Montrer de vieux chiffres comme s'ils étaient frais.** Chaque écran affiche
  l'heure de publication ; au-delà de 10 min sans publication, bandeau
  « données périmées ». Le service worker ne met en cache que la coquille
  (HTML/CSS/JS/icônes), **jamais** les données.
- **Faire sortir la stratégie.** Les paramètres complets des bras
  (`/api/params`), les clés, le `.env`, les adresses de wallet ne sont jamais
  publiés.
- **Un chiffre sans bras.** Comme le dashboard : chaque ligne nomme son bras,
  les sommes sont dites « flotte (somme) ».
- **Faire tomber le bot.** La publication est un processus séparé ; Supabase
  injoignable ne touche jamais la boucle de trading.

## Contraintes
- Dépôt `gerskill/CryptoBot` **public** : la page est publique, les données
  non (RLS, connexion obligatoire, aucun droit `anon`).
- Pas de clé `service_role` : le script du Mac se connecte **avec le compte de
  Killian** (e-mail + mot de passe dans le `.env` du Mac, jamais commités) ;
  la RLS ne l'autorise qu'à ses propres lignes.
- La clé publique Supabase (`anon`) et l'URL du projet sont dans l'app, comme
  Coach : elles ne donnent aucun droit sans connexion.
- Pas de dépendance réseau tierce au chargement : `supabase-js` est copié dans
  `mobile/vendor/` (comme Coach), pas de CDN.
- Français partout ; tests du script Python en `unittest.TestCase`.
- Rien sous `config/` modifié.

## Approches considérées

| question | choix | écarté et pourquoi |
|---|---|---|
| app iPhone | **PWA dédiée sans build** dans `mobile/` (comme Coach et Prompt Studio) | réutiliser le dashboard React : conçu grand écran, câblé sur l'API locale et le WebSocket, un second mode « Supabase » partout serait fragile |
| envoi des données | **script séparé lancé par launchd toutes les 2 min**, connecté avec le compte de Killian | pousser depuis la boucle du bot : appel réseau dans la boucle de trading ; clé `service_role` : ouvre toute la base |
| hébergement | **GitHub Pages par une action GitHub** qui publie `mobile/` | GitHub Pages sur dossier `docs/` : déjà utilisé pour la documentation |

## Conception

### Supabase (`supabase/schema.sql`, à coller une fois)
Table `public.snapshots` sur le modèle de `user_data` de Coach :
`user_id uuid default auth.uid()`, `key text` (1-64), `value jsonb`,
`published_at timestamptz`, clé primaire `(user_id, key)`. RLS activée,
politiques select / insert / update pour `authenticated` sur ses lignes,
`revoke all from anon`. Clés publiées : `etat`, `positions`, `perf_7d`,
`perf_30d`, `perf_all`, `liquid`, `alertes`.

### Mac : `scripts/publie_supabase.py`
- Construit chaque résumé avec les fonctions existantes, **sans HTTP** :
  `api.server._state()` (état + positions), `api.server.get_arms_performance(window)`,
  `api.server.get_prediction_summary()`, alertes tirées des journaux (sorties
  `ZOMBIE` des dernières 24 h) et de `data/veille_etat.json`.
- Filtre explicite : liste blanche des champs publiés par clé (ce qui n'est
  pas listé ne part pas) ; test qui vérifie qu'aucun champ de paramètres de
  stratégie ni rien ressemblant à une clé n'est publié.
- Se connecte (`/auth/v1/token?grant_type=password`), garde le jeton en
  mémoire, `upsert` des lignes (`/rest/v1/snapshots`, `Prefer:
  resolution=merge-duplicates`), avec `requests` (déjà en dépendance).
- Variables `.env` : `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_EMAIL`,
  `SUPABASE_PASSWORD` ; absentes → message clair et sortie 0 (le service ne
  boucle pas en erreur).
- `--sans-envoi` : affiche ce qui serait publié.
- Service launchd `com.cryptobbot.publie` (modèle comme les autres,
  `StartInterval` 120) ; ajouté à `scripts/installe_services.sh`.

### App : `mobile/`
`index.html`, `app.js`, `style.css`, `manifest.webmanifest`, `sw.js`,
`icons/` (192, 512, maskable 512, apple-touch-icon), `vendor/supabase.js`,
`config.js` (URL + clé anon). Balises iPhone comme Coach
(`apple-mobile-web-app-capable`, `-status-bar-style`, `-title`,
`apple-touch-icon`, `viewport-fit=cover`). Écran de connexion (e-mail + mot
de passe, session persistée), puis quatre onglets en bas ; tirer pour
rafraîchir ; rafraîchissement automatique toutes les 60 s quand l'app est au
premier plan. Thème sombre par défaut, lisible en plein soleil (contraste).

### Déploiement : `.github/workflows/pages.yml`
Publie `mobile/` sur GitHub Pages à chaque push sur `main` touchant
`mobile/`. Adresse attendue : `https://gerskill.github.io/CryptoBot/`.

### Ce que Killian fait une fois (guide `docs/APP_IPHONE.md`)
1. Créer le projet Supabase « cryptobot », coller `supabase/schema.sql`,
   créer son compte (Authentication → Users), copier URL + clé anon.
2. Réglages GitHub du dépôt → Pages → Source : « GitHub Actions ».
3. `.env` du Mac : les 4 variables ; `./scripts/installe_services.sh publie`.
4. iPhone : Safari → l'adresse Pages → Partager → « Sur l'écran d'accueil ».

## Points tranchés après relecture (choix de Claude)

1. **Mac éteint.** L'app ne peut pas distinguer « bot arrêté » de « Mac ou
   publication arrêtés » : passé 10 min sans publication, le bandeau dit
   « aucune nouvelle depuis X min — Mac éteint, publication ou bot arrêté ».
   L'alerte poussée reste celle de la veille (Telegram) et de healthchecks.io
   (`HEALTHCHECK_PING_URL`, déjà prévu) ; l'app n'envoie aucune notification.
2. **« En ligne ».** L'app affiche l'âge réel du dernier cycle du bot
   (`maintenant − updated_at du bot` publié), pas le drapeau calculé au moment
   de la publication : un bot mort 1 min après une publication apparaît vite
   comme « dernier cycle il y a 6 min ».
3. **Alertes publiées** : sorties `ZOMBIE` et `PANIC_STOP` des dernières 24 h,
   services en panne d'après `data/veille_etat.json` (champ `ok` faux), au
   plus 20 lignes, les plus récentes d'abord.
4. **Positions ouvertes publiées** : bras, symbole, P&L latent %, taille $,
   durée, TP1 atteint oui/non. **Pas** de stop, de niveaux de TP ni d'adresse
   de token : ce sont des éléments de stratégie.
5. **Liquid publié** : par bras, les champs `compte` (solde) et `stats`
   (ouverts, réglés, gagnés nets) de `get_prediction_summary()` ; l'exemple
   fictif (`demo`) n'est jamais publié.
6. **Session Supabase du Mac** : mot de passe utilisé seulement si aucune
   session valide ; jeton de rafraîchissement gardé dans
   `data/supabase_session.json` (droits 600, `data/` est gitignoré), pour ne
   pas se reconnecter toutes les 2 min.
7. Projet Supabase **nouveau et dédié** (décision de Killian).

## Laissé au builder
Mise en page exacte, couleurs (cohérentes avec le dashboard), structure de
`app.js`, format interne des JSON publiés (dans la limite de la liste
blanche), découpage en commits.
