# App iPhone CryptoBot — plan d'exécution

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Killian suit le bot depuis son iPhone : une PWA sur GitHub Pages lit dans Supabase des résumés publiés toutes les 2 min par le Mac.

**Architecture:** `scripts/publie_supabase.py` (Mac, launchd) → table `snapshots` (Supabase, RLS) → `mobile/` (PWA sans build, GitHub Pages).

**Tech Stack:** Python 3.13 + requests + unittest ; HTML/CSS/JS sans build, supabase-js UMD local ; GitHub Actions Pages ; Supabase Postgres + Auth.

**Spec:** `docs/superpowers/specs/2026-11-18-app-iphone-design.md` (approuvée, avec « Points tranchés après relecture »).

## Critères de réussite
- L'app installée sur l'iPhone affiche les 4 écrans avec des données de moins de 3 min quand le Mac tourne.
- Mac éteint : l'app le dit en moins de 10 min, sans montrer de chiffres comme frais.
- Aucun paramètre de stratégie, clé ou réglage de bras dans Supabase (test).

## Global Constraints
- PAPER, lecture seule : aucune action depuis l'app.
- Français partout ; tests Python en `unittest.TestCase` nommés d'après le bug.
- Jamais de clé `service_role` ; le Mac se connecte avec le compte de Killian ; variables `.env` : `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_EMAIL`, `SUPABASE_PASSWORD`.
- Ne jamais modifier `config/params.json`, `config/arms/*.json`, `config/prediction_arms.json`.
- La boucle de trading n'est pas modifiée.
- Le service worker ne met en cache que la coquille, jamais les données.
- Pas de CDN : `supabase-js` copié en local (`mobile/vendor/supabase.js`, licence MIT jointe), depuis `/home/user/gerskill/coach-killian/vendor/supabase/` (même version que Coach).

## Contrat de données (table `snapshots`, une ligne par clé)

Chaque `value` porte `"publie_a"` (ISO UTC). Champs EXACTS — rien d'autre ne part :
- `etat` : `{"publie_a", "bot": {"dernier_cycle": ISO|null, "mode": "PAPER"}, "liquid": {"dernier_cycle": ISO|null}}`
- `positions` : `{"publie_a", "lignes": [{"bras", "symbole", "pnl_pct", "taille_usd", "duree_min", "tp1_atteint"}]}`
- `perf_7d`, `perf_30d`, `perf_all` : `{"publie_a", "fenetre", "capital_par_bras", "bras": [{"bras", "trades", "win_rate", "ic_bas", "ic_haut", "profit_factor", "pnl_usd", "max_drawdown_pct", "verdict"}], "flotte": {"trades", "win_rate", "pnl_usd", "profit_factor"}}`
- `liquid` : `{"publie_a", "bras": [{"bras", "famille", "actif", "ouverts", "solde_usd", "regles", "gagnes_net"}]}` — jamais `reglages`, `variables_forkables`, `apprentissage`, ni l'exemple fictif.
- `alertes` : `{"publie_a", "lignes": [{"quand": ISO, "type": "ZOMBIE"|"PANIC_STOP"|"PANNE", "bras": str|null, "texte"}]}` — 24 h, ≤ 20, plus récentes d'abord.

## Review Focus
1. Variables `.env` absentes : le script le dit et sort 0 (le service ne boucle pas en erreur).
2. Supabase injoignable ou 401 : aucune exception non gérée, sortie non nulle, aucune fuite de mot de passe dans les messages.
3. `data/` vide (bot jamais lancé) : publication d'un état « aucune donnée », pas d'exception.
4. Données de plus de 10 min dans l'app : bandeau « aucune nouvelle depuis X min », chiffres grisés.
5. Session expirée dans l'app : retour à l'écran de connexion, pas d'écran blanc.

---

### Task 1: publication depuis le Mac

**Files:**
- Create: `supabase/schema.sql` (table `snapshots` comme `user_data` de Coach : `user_id uuid default auth.uid() references auth.users`, `key text check 1..64`, `value jsonb`, `published_at timestamptz default now()`, PK `(user_id, key)`, RLS select/insert/update pour `authenticated` sur ses lignes, `revoke all from anon` ; relançable sans risque)
- Create: `scripts/publie_supabase.py`, `tests/test_publie_supabase.py`
- Create: `scripts/launchd/com.cryptobbot.publie.plist.modele` (comme `com.cryptobbot.veille`, `StartInterval` 120)
- Modify: `scripts/installe_services.sh` (service `publie` reconnu, motif `scripts/publie_supabase\.py`), `tests/test_veille.py` (le décompte des modèles passe à 5), `.env.example` (4 variables, commentées), `scripts/README.md`

**Interfaces:**
- Produces: `construire_snapshots(maintenant: datetime) -> dict[str, dict]` (pur, testable : les 7 clés du contrat, sans réseau) ; `publier(snapshots, session) -> None` ; CLI `python scripts/publie_supabase.py [--sans-envoi]`.
- Session : `data/supabase_session.json` (droits 600) avec le jeton de rafraîchissement ; mot de passe utilisé seulement sans session valide.

- [ ] **Step 1: tests qui échouent** — `TestContrat` (chaque clé a exactement les champs du contrat ; `test_aucun_parametre_de_strategie_ne_sort` : aucune clé/valeur contenant `stop_loss`, `take_profit`, `reglages`, `params`, `token_address`, `api_key`, `password`) ; `TestDonneesAbsentes` (data vide → snapshots « aucune donnée ») ; `TestAlertes` (ZOMBIE/PANIC_STOP 24 h, ≤ 20, ordre) ; `TestEnv` (variables absentes → sortie 0 et message) ; `TestReseau` (requests simulé : 401 puis reconnexion par mot de passe ; injoignable → sortie ≠ 0 sans mot de passe dans le texte) ; `TestLiquid` (exemple fictif jamais publié).
- [ ] **Step 2:** `python -m unittest tests.test_publie_supabase -v` → échec.
- [ ] **Step 3:** implémenter (réutiliser `api.server._state`, `get_arms_performance`, `get_prediction_summary` sans HTTP ; liste blanche explicite par clé).
- [ ] **Step 4:** `python scripts/publie_supabase.py --sans-envoi` s'exécute ; suite complète `python -m unittest discover -s tests` OK.
- [ ] **Step 5: Commit** `Publication Supabase des résumés pour l'app iPhone (lecture seule)`

### Task 2: l'app iPhone et son déploiement

**Files:**
- Create: `mobile/index.html`, `mobile/app.js`, `mobile/style.css`, `mobile/manifest.webmanifest`, `mobile/sw.js`, `mobile/config.js` (URL et clé anon à remplir, valeurs vides par défaut + message clair dans l'app si vides), `mobile/vendor/supabase.js` + `mobile/vendor/LICENSE`, `mobile/icons/` (192, 512, maskable-512, apple-touch-icon 180 ; générées avec Pillow, sobres)
- Create: `.github/workflows/pages.yml` (publie `mobile/` sur push `main` touchant `mobile/**`, via `actions/upload-pages-artifact` + `actions/deploy-pages`, versions épinglées)
- Create: `docs/APP_IPHONE.md` (guide pas à pas : projet Supabase, `schema.sql`, compte, URL + clé anon dans `mobile/config.js`, Pages → Source « GitHub Actions », `.env` du Mac, `./scripts/installe_services.sh publie`, Safari → Sur l'écran d'accueil)
- Create: `mobile/test/smoke.mjs` (Playwright, déjà installé : sert `mobile/` en local avec un faux client Supabase injecté, viewport iPhone, vérifie les 4 onglets, le bandeau de péremption, l'écran de connexion quand la session manque) et un script `mobile/test/README.md` qui dit comment le lancer

**Interfaces:**
- Consumes: le contrat de données ci-dessus (table `snapshots`, clés et champs exacts).

- [ ] **Step 1:** écrire `smoke.mjs` d'abord ; il échoue (pas d'app).
- [ ] **Step 2:** construire l'app (balises iPhone comme Coach : `apple-mobile-web-app-capable`, `-status-bar-style` black-translucent, `-title`, `apple-touch-icon`, `viewport-fit=cover` ; onglets en bas ; rafraîchissement 60 s au premier plan ; chaque chiffre nomme son bras ; bandeau > 10 min ; service worker coquille seule).
- [ ] **Step 3:** `node mobile/test/smoke.mjs` OK + capture iPhone (deviceScaleFactor 2) dans le rapport.
- [ ] **Step 4:** `python -m unittest discover -s tests` toujours OK.
- [ ] **Step 5: Commit** `App iPhone : PWA lecture seule sur GitHub Pages, guide d'installation`
