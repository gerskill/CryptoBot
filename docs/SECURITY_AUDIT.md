# Audit de sécurité — CryptobBot

Date : 2026-08-07
Type : audit documentaire, aucune correction de code appliquée.
Périmètre : dépendances Python, secrets en dur, permissions fichiers `data/`, sécurité du dashboard (`api/server.py`).

## Résumé

**Aucune vulnérabilité critique trouvée.** Aucun secret réel exposé dans le code, la config, ou l'historique git. Niveau de risque global : **FAIBLE**, avec quelques points à surveiller (voir Moyen/Faible ci-dessous).

| Sévérité | Nombre |
|---|---|
| Critique | 0 |
| Élevé | 0 |
| Moyen | 2 |
| Faible | 3 |

---

## 1. Dépendances Python

`requirements.txt` liste 3 dépendances directes, non épinglées à une version exacte (`>=`) :

```
requests>=2.32.0
fastapi>=0.115.0
uvicorn>=0.32.0
```

**Méthode** : `pip-audit` installé dans un venv isolé (`/private/tmp/.../scratchpad/pip-audit-venv`), sans toucher aux versions déjà installées dans l'environnement du projet (requests 2.34.2, fastapi 0.136.3, uvicorn 0.49.0, vérifiées inchangées après l'audit).

`pip-audit -r requirements.txt` résout les dernières versions compatibles et leurs dépendances transitives, puis interroge la base OSV/PyPI :

```
Auditing requests (2.34.2), charset-normalizer (3.4.9), idna (3.18), urllib3 (2.7.0)
Auditing fastapi (0.141.1), starlette (1.4.1), pydantic (2.13.4), pydantic_core (2.46.4),
         anyio (4.14.2), typing_extensions (4.16.0), typing-inspection (0.4.2),
         annotated-types (0.8.0), annotated-doc (0.0.5)
Auditing uvicorn (0.52.1), click (8.4.2), h11 (0.16.0), certifi (2026.7.22)

No known vulnerabilities found
```

**Résultat : aucune CVE connue** sur les 3 dépendances directes ni sur leurs 14 dépendances transitives résolues.

### Finding [MOYEN] — Versions non épinglées

`requirements.txt` utilise `>=` sans borne haute. `pip-audit` a donc audité les dernières versions publiées (ex. fastapi 0.141.1), pas nécessairement celles réellement installées en production (0.136.3 dans cet environnement). Deux conséquences :
- Reproductibilité faible : un déploiement futur peut tirer une version différente de celle testée.
- Une vulnérabilité future sur une version intermédiaire ne serait pas forcément détectée par un audit qui ne regarde que « la dernière ».

**Recommandation** : épingler avec `==` (ou au minimum une borne haute `<X.0.0`) et regénérer via `pip freeze` ou un lockfile (`pip-tools`, `uv pip compile`). Réexécuter `pip-audit -r requirements.txt` régulièrement (CI ou cron) contre les versions réellement épinglées.

---

## 2. Secrets en dur

### Recherche effectuée
- Grep sur `src/`, `api/`, `dashboard/src/` pour les patterns `api_key=`, `token=`, `secret=`, `password=` avec valeur littérale (hors `os.getenv`/`process.env`/`import.meta.env`) → **aucun résultat**.
- Grep pour formats de clés connus (`sk-…`, tokens Telegram `\d{8,10}:[A-Za-z0-9_-]{30,}`, `AKIA…`, `AIzaSy…`, `ghp_…`, `xox[baprs]-…`) sur tout le dépôt (hors `.git`, `node_modules`) → **aucun résultat**.
- `config/*.json` et `config/arms/*.json` : uniquement des paramètres de stratégie (seuils, raisons d'ajustement mesurées) — **aucune clé ni token**.
- Vérification du code source : toutes les clés (`HELIUS_API_KEY`, `BIRDEYE_API_KEY`, `TWITTER_BEARER_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` dans `src/settings.py` ; `GMGN_API_KEY`/`GMGN_PRIVATE_KEY` dans `src/apis/gmgn.py` ; la clé Jupiter dans `src/apis/jupiter.py`) sont lues via `os.getenv(...)`, jamais en dur. Le commentaire dans `src/apis/jupiter.py` confirme explicitement : « La clé vient de l'environnement, n'est jamais loggée ».
- `src/apis/jupiter.py` : liste blanche `ALLOWED_PATHS = {"/price/v3", "/swap/v2/order", "/tokens/v2/search"}` confirmée dans le code — aucun chemin d'exécution de swap (`/swap/v2/execute`, `/swap/v2/build`) n'est accessible, cohérent avec la documentation dans `.env.example`.

### `.gitignore`
`.env` est listé en toute première ligne de `.gitignore`. Confirmé fonctionnel : `git ls-files | grep '^\.env$'` ne retourne rien, et `git log --all --full-history -- .env` est vide — `.env` n'a **jamais** été suivi ni commité, dans aucune branche.

### `.env.example` vs `.env` (vérification indépendante)
Le vrai `.env` existe dans le dépôt principal (`/Users/leclercq/Documents/Claude/Projects/CryptobBot/.env`), permissions `600` (propriétaire seul). Je n'ai lu ni affiché aucune valeur — seulement les **noms de clés** et si une ligne contient un caractère après `=`.

- Chaque ligne non-commentaire de `.env.example` est soit `NOM_CLE=` (valeur vide), soit `TRADING_MODE=PAPER` (une énumération documentée, pas un secret). **Confirmé indépendamment : aucune valeur réelle dans `.env.example`.**
- Écart mineur de couverture entre les deux fichiers (noms de clés uniquement) :
  - `FIRECRAWL_API_KEY` est présente dans le vrai `.env` mais **absente** de `.env.example`.
  - `GMGN_PRIVATE_KEY` est documentée dans `.env.example` mais absente du `.env` réel (non configurée — sans impact, cette clé n'est de toute façon jamais utilisée pour signer une transaction côté bot, cf. commentaire dans `.env.example`).

### Finding [FAIBLE] — `.env.example` incomplet

`FIRECRAWL_API_KEY` utilisée en pratique (présente dans `.env`) n'a pas de ligne correspondante dans `.env.example`. Purement une question de documentation/onboarding, aucun risque de fuite.

**Recommandation** : ajouter `FIRECRAWL_API_KEY=` à `.env.example` pour que le template reste une source de vérité complète.

### Recherche dans l'historique git

Recherche `git log --all -p -S"<CLE>="` sur les 57 commits du dépôt pour chacune des variables `HELIUS_API_KEY`, `BIRDEYE_API_KEY`, `TWITTER_BEARER_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `JUPITER_API_KEY`, `GMGN_API_KEY`, `GMGN_PRIVATE_KEY`, `DASHBOARD_TOKEN` : **aucune ligne ajoutée avec une valeur non vide** dans tout l'historique. Aucune fuite passée à purger.

---

## 3. Permissions fichiers (`data/`, `data/arms/*/`)

Vérification `ls -la` sur le dépôt principal (le worktree d'audit n'a pas de `data/` peuplé, les fichiers `.json`/`.jsonl` étant exclus par `.gitignore`).

- **Aucun fichier world-writable (`o+w`)** trouvé dans `data/`, `data/arms/*/`, ni `config/` (`find … -perm -o+w` → vide).
- `.env` : `600` (`rw-------`), propriétaire seul. Correct.
- Fichiers sensibles à état courant (`state.json`, `token_cache.json`, `api_budgets.json`, `open_positions.json`, `wallets_log_first_seen.json`, `data/arms/*/open_positions.json`) : `600`, lecture/écriture propriétaire seul. Bon réflexe.
- Journaux d'historique (`trades_log.jsonl`, `shadow_log.jsonl`, `counterfactual_log.jsonl`, `microstructure_log.jsonl`, `rsi_log.jsonl`, `volatility_log.jsonl`, `dev_history_log.jsonl`, `wallets_log.jsonl`, `funnel_log*.jsonl`, `telegram_reporter_log.jsonl`) : `644` (`rw-r--r--`), donc lisibles par tout utilisateur local mais **non modifiables** par un autre compte que le propriétaire.

### Finding [FAIBLE] — Journaux en lecture world-readable (644)

Les fichiers `*.jsonl` d'historique de trades sont lisibles par n'importe quel utilisateur local du système (pas seulement le propriétaire). Aucun de ces journaux ne contient de secret (confirmé par le grep de la section 2), mais ils exposent l'activité de trading complète (positions, PnL, tokens) à quiconque a un compte sur la machine.

**Recommandation** : si la machine est multi-utilisateurs, aligner ces fichiers sur `600` comme le sont déjà `state.json`/`open_positions.json`. Sur une machine mono-utilisateur (cas probable ici), impact nul.

---

## 4. Dashboard (`api/server.py`)

### Authentification
`DASHBOARD_TOKEN` protège uniquement `/api/params` — confirmé dans le code (`_require_token(token)` appelé seulement dans `get_params`). C'est le seul endpoint qui expose les paramètres complets de la stratégie (fenêtres, seuils, filtres) — la donnée qui permettrait de reproduire le bot. Les autres endpoints (`/api/state`, `/api/trades`, `/api/arms`, `/api/confluence`, `/api/shadow`, `/api/health`, `/ws`) sont volontairement publics.

**Évaluation** : ce choix reste cohérent avec ce qui est exposé sans token — P&L, positions, trades passés, résultats de shadow-trading — c'est-à-dire des *résultats*, pas des *règles*. Le commentaire dans le code (« l'API ne connaît pas la boucle de trading… lecture seule ») documente explicitement le compromis, et le serveur avertit au démarrage si `DASHBOARD_TOKEN` est absent (« aucune authentification, acceptable en local »). Ce n'est pas une vulnérabilité en soi ; c'est un choix assumé et documenté, cohérent tant que le port reste local.

Deux réserves à noter, non bloquantes :

### Finding [MOYEN] — Jeton transmis en query string

`GET /api/params?token=...` : le jeton `DASHBOARD_TOKEN` est passé en paramètre d'URL plutôt qu'en en-tête `Authorization`. La comparaison elle-même est correcte (`secrets.compare_digest`, temps constant), mais un jeton en query string se retrouve typiquement dans :
- les logs d'accès du serveur (uvicorn/proxy en amont),
- l'historique du navigateur,
- l'en-tête `Referer` si le dashboard fait un lien sortant depuis cette page.

**Recommandation** : si `DASHBOARD_TOKEN` est activé pour une exposition non-locale (tunnel, port ouvert), faire porter le jeton par un en-tête (`Authorization: Bearer …`) plutôt que par la query string. Actuellement `dashboard/src/lib/useArmParams.ts` le construit bien via `query.set('token', token)` — c'est le point à changer côté frontend si ce risque devient pertinent.

### CORS
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)
```
**Confirmé restreint** : pas de wildcard `allow_origins=["*"]`. Origines limitées au serveur de dev Vite en local. Méthodes limitées à `GET` (cohérent avec une API lecture seule — aucune route `POST`/`PUT`/`DELETE` n'existe dans `server.py`). Bon état.

### Finding [FAIBLE] — Rappel opérationnel : exposition non-locale

Le code documente déjà ce risque lui-même, mais il vaut la peine de le noter dans un audit : le jour où `uvicorn` écoute sur autre chose que `127.0.0.1` (tunnel ngrok, VPS, port ouvert sur le LAN), `DASHBOARD_TOKEN` doit être défini avant toute exposition — sinon `/api/params` (paramètres complets de stratégie) devient public. Voir aussi la règle mémorisée du projet : *API_TOKEN avant toute exposition non-localhost*.

**Recommandation** : pas de changement de code nécessaire — juste s'assurer que la checklist de déploiement (si elle existe) inclut explicitement « `DASHBOARD_TOKEN` défini » avant toute exposition du port 8000 au-delà de `localhost`. Envisager, si CORS doit un jour s'ouvrir au-delà de `localhost:5173`, de lister explicitement le(s) domaine(s) de prod plutôt qu'un wildcard.

---

## Ce qui est déjà bien fait

- **Aucun secret en dur nulle part** dans `src/`, `api/`, `dashboard/src/`, `config/`, ni dans l'historique git complet (57 commits vérifiés).
- Toutes les clés API sont lues exclusivement via `os.getenv(...)`, avec un commentaire explicite dans `jupiter.py` rappelant qu'elles ne doivent jamais être loggées.
- `.env` : permissions `600`, jamais suivi par git, jamais commité historiquement.
- `.env.example` : uniquement des clés vides ou des valeurs d'énumération documentées — pas de valeur réelle.
- Liste blanche de chemins API stricte dans `src/apis/jupiter.py` (`ALLOWED_PATHS`) qui exclut explicitement les endpoints d'exécution de swap — le bot ne peut pas passer d'ordre réel via Jupiter même en cas de bug applicatif.
- `secrets.compare_digest` utilisé pour la comparaison du jeton dashboard — évite une fuite de timing.
- Endpoint le plus sensible (`/api/params`) correctement identifié et protégé par jeton ; les autres endpoints exposent des résultats, pas des règles — compromis documenté dans le code lui-même.
- CORS restreint à l'origine de dev locale, méthodes limitées à `GET`.
- Fichiers d'état sensibles (`state.json`, `open_positions.json`, `token_cache.json`, `api_budgets.json`) déjà en `600`.
- Aucun fichier world-writable détecté dans `data/`, `config/`, ni ailleurs dans le dépôt.
- `pip-audit` : aucune CVE connue sur les 3 dépendances directes et leurs 14 dépendances transitives.

---

## Synthèse des recommandations (par priorité)

1. **[Moyen]** Épingler les versions dans `requirements.txt` (`==`) et ré-auditer périodiquement avec `pip-audit`.
2. **[Moyen]** Si le dashboard est un jour exposé hors `localhost`, faire porter `DASHBOARD_TOKEN` par un en-tête `Authorization` plutôt que par la query string `?token=`.
3. **[Faible]** Ajouter `FIRECRAWL_API_KEY=` à `.env.example`.
4. **[Faible]** Si la machine hôte est multi-utilisateurs, passer les journaux `*.jsonl` en `600` comme les fichiers d'état.
5. **[Faible]** Documenter dans la checklist de déploiement (si elle existe) : `DASHBOARD_TOKEN` obligatoire avant toute exposition non-locale du port 8000.
