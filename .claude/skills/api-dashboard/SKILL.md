---
name: api-dashboard
description: Modifier l'API FastAPI (api/server.py) ou le dashboard React/TypeScript (dashboard/) de CryptobBot. Utilise ce skill dès qu'il est question d'un endpoint /api/*, du WebSocket /ws, de state.json, d'un panneau, d'un graphique, d'un composant React, de types partagés, du token du dashboard, du backtester, du rapport hebdo affiché, ou d'« afficher X dans l'interface » — y compris pour un simple changement de libellé, car le panneau a déjà affiché le témoin en le présentant comme le bot.
---

# API et dashboard

Deux stacks, **un seul domaine**. « candidat », « position », « score alpha
absolu », « shadow trade » désignent exactement la même chose des deux côtés :
`dashboard/src/lib/types.ts` est le **miroir** de `src/core/state.py`. Un champ
ajouté d'un seul côté crée une divergence silencieuse — les modifier ensemble,
dans le même commit.

## L'API

FastAPI, lue seulement, servie par `uvicorn api.server:app --port 8000`.

| endpoint | contenu |
|---|---|
| `/api/state` | état courant (positions, candidats, bras) |
| `/api/trades?limit&arm` | journal, filtrable par bras |
| `/api/arms` | manifeste + état par bras |
| `/api/confluence` | votes du bras `consensus` |
| `/api/shadow?limit&arm` | rejets suivis |
| `/api/params?...` | document de paramètres d'un bras |
| `/api/metrics` | Prometheus, texte brut |
| `/api/health` | vivacité, fraîcheur de l'état |
| `/ws` | flux temps réel, `POLL_INTERVAL_SECONDS = 1.0` |

`STALE_AFTER_SECONDS = 180` : au-delà, l'état affiché est périmé et l'interface
doit le **dire** plutôt que de montrer de vieux chiffres comme s'ils étaient
vivants.

L'API **ne doit jamais écrire** dans `data/` ni dans les paramètres d'un bras :
seule la boucle est propriétaire de ces fichiers, et une écriture concurrente
casserait l'atomicité tmp + rename.

## Sécurité de l'API

`DASHBOARD_TOKEN` protège les routes ; `_require_token` accepte l'en-tête
`Authorization: Bearer`. Le jeton en **query string** est un point relevé à
l'audit (il fuit dans les logs et l'historique du navigateur) — préférer
l'en-tête, et ne jamais élargir le CORS ni exposer l'API hors de la machine
sans en parler explicitement à l'utilisateur.

## Le dashboard

React 19 + TypeScript + Vite + Tailwind 4 + zustand + framer-motion.

```bash
cd dashboard && npm run dev      # développement
npm run build                    # tsc -b && vite build — doit passer
npm run lint                     # oxlint
```

Structure : `components/` (`TheHunt`, `TheArms`, `TheBrain`,
`ActivePositions`), sous-dossiers `live/`, `weekly/`, `backtester/`,
`config/` ; `lib/` pour les hooks (`useLiveState`, `useArmEquity`,
`useArmParams`), le store, les types et le formatage.

## Le piège déjà tombé

**Le panneau a affiché le témoin et l'a présenté comme « le bot ».** Tout
chiffre affiché doit dire **de quel bras** il parle, ou être explicitement une
agrégation des sept. Un total sans bras nommé est un bug d'interface, pas un
détail de libellé. De même, un compte de trades doit venir des sorties finales
(`read_final_exits`), sinon TP1 et TP2 le doublent.

## Vérifier

```bash
python -m unittest tests.test_api_server tests.test_state tests.test_metrics \
  tests.test_state_position_payload
cd dashboard && npm run build && npm run lint
```
