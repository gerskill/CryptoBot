# Product Specification: Alpha Loop Dashboard — Extension 4 vues

> Generated from brief: "Étendre le dashboard CryptobBot existant avec 4 nouvelles vues intégrées dans le même Shell"

## Vision

Alpha Loop est un terminal de trading algorithmique conçu pour être regardé des heures, la nuit, en prenant des décisions à faible erreur. L'extension ajoute quatre vues spécialisées sans rompre le contrat visuel existant : même palette sémantique, même hiérarchie typographique, même grammaire de composants. Le dashboar reste un outil, pas un produit.

## Contraintes absolues (non négociables)

- **Lecture seule** : aucun endpoint ne déclenche de trade, aucune mutation d'état bot.
- **Pas de nouvelle dépendance** sauf si techniquement impossible autrement. Le projet a déjà : framer-motion, zustand, tailwindcss v4, react 19, vite 8. Pas de recharts, chart.js, d3, react-router. Les graphiques restent en SVG inline, la navigation en state Zustand.
- **Design system strict** : uniquement les couleurs `void/surface/raised/edge/edge-strong/ink/muted/dim/toxic/blood/warn/gem`. Aucun bleu, aucun violet sauf `gem`. Aucun gradient décoratif. Aucun ombre portée épaisse.
- **Composants existants réutilisés** : `Panel`, `NoSource`, `Empty`, `Stat` de `Panel.tsx`. `usePulse` de `useLiveState.ts`. Toutes les fonctions de `format.ts`.

## Design Direction

- **Palette**: `void` (#08080c) fond, `surface` (#101017) cartes, `raised` (#16161f) éléments élevés, `edge` (#23232f) bordures, `ink` (#edeef2) texte principal, `dim` (#5a5c6a) texte secondaire, `toxic` (#2ee56b) gain/positif, `blood` (#ff4a6e) perte/négatif, `warn` (#ffab2e) alerte, `gem` (#7c6cff) accent unique.
- **Typographie**: SF Mono / JetBrains Mono pour les chiffres et labels (tabular-nums), Inter pour la prose. Echelle `micro` (0.625rem) → `tiny` (0.6875rem) → `body` (0.8125rem) → `lead` (1rem) → `figure` (1.75rem).
- **Layout**: Chaque nouvelle vue prend `max-w-[1800px] mx-auto px-5 py-5`, identique au `<main>` existant. Pas de sidebar. Une seule colonne maîtresse ou une grille en 2-3 colonnes selon la densité d'information.
- **Anti-patterns bannis**: Gradient de fond coloré, cards avec ombre portée, icônes SVG décoratifs, boutons avec fond coloré plein (utiliser `ring-1 ring-edge/60` comme les panels), animations d'entrée au-delà de 300ms, spinner circulaire animé (utiliser un état texte sobre).

## Navigation

### Architecture

Ajouter un slice `view` dans le store Zustand existant (`store.ts`) :

```ts
type View = 'dashboard' | 'live' | 'weekly' | 'config' | 'backtester'
// Ajouter dans Store :
view: View
setView: (view: View) => void
```

### Composant NavBar

Fichier : `dashboard/src/components/NavBar.tsx`

Intégré dans `Shell.tsx` immédiatement sous le `<header>`, avant le `<motion.main>`. La NavBar est sticky au scroll (fait partie du header sticky), ou simplement affichée entre le header et le main.

Structure :

```
[ Dashboard ]  [ Live Prices ]  [ Weekly ]  [ Config ]  [ Backtester ]
```

Style : bande fine `border-b border-edge/60`. Tabs en `font-mono text-[10px] uppercase tracking-[0.15em]`. Tab actif : `text-ink` + `border-b-2 border-gem` calé sur le bas de la bande. Tab inactif : `text-dim hover:text-ink`. Pas de fond coloré, pas de pill, pas de box-shadow.

La vue `dashboard` rend les 4 panels existants (comportement actuel inchangé). Les 4 nouvelles vues remplacent le contenu du `<motion.main>` quand elles sont actives.

Modifier `App.tsx` pour router selon `useStore(s => s.view)`.

---

## Features

### Sprint 1 : LivePrices

**Objectif** : tableau de prix en temps réel, mis à jour à chaque push WebSocket, sans nouvelle requête API.

**Composants à créer**

| Fichier | Rôle |
|---|---|
| `src/components/live/LivePrices.tsx` | Vue principale |
| `src/components/live/PriceRow.tsx` | Ligne token : symbole, prix actuel pulsant, variation |
| `src/components/live/PositionBlock.tsx` | Section positions ouvertes |
| `src/components/live/CandidatesBlock.tsx` | Section candidats du cycle |

**Données consommées**

- `useStore(s => s.state.positions)` — positions ouvertes avec `current_price`, `entry_price`, `symbol`, `pnl_pct`, `pnl_usd`, `arm`
- `useStore(s => s.state.candidates)` — candidats retenus avec `symbol`, `price_usd`, `price_change_5m`, `price_change_1h`, `alpha_score`, `liquidity_usd`
- `useStore(s => s.connected)` — badge offline
- Pas de fetch supplémentaire : la WS pousse déjà tout.

**Comportements attendus**

1. **Positions ouvertes** : chaque ligne affiche `symbol`, badge `arm`, prix d'entrée, prix actuel (avec `usePulse` — vert si monte, rouge si baisse), P&L % et $, durée.
2. **Candidats du cycle** : chaque ligne affiche `symbol`, `price_usd`, `price_change_5m` (coloré toxic/blood), `price_change_1h`, `alpha_score`, liquidité compacte.
3. **Mise à jour** : `usePulse` doit s'animer à chaque nouveau push WS. Les lignes ne se réordonnent pas à chaque update (utiliser une key stable = `position.id` / `candidate.token_address`).
4. **Offline** : si `!connected`, afficher une bannière `text-warn` sobre en haut de la vue (pas l'`OfflineBanner` de App, mais un équivalent inline dans LivePrices).
5. **Empty states** :
   - Zéro position : `<Empty>Aucune position ouverte. Le bot scanne.</Empty>`
   - Zéro candidat : `<Empty>Aucun candidat retenu ce cycle.</Empty>`
6. **Layout** : deux colonnes sur écran ≥ 1280px (`grid-cols-2 gap-4`), une colonne en dessous. Chaque section dans un `<Panel>`.
7. **Ticker-style header** : au-dessus des deux colonnes, une ligne `text-[10px] text-dim` indiquant `N positions · M candidats · mis à jour il y a Xs`.

**Critères de succès**

- Le prix d'une position clignote en toxic/blood lors d'un push WS.
- Aucun appel réseau supplémentaire généré par cette vue.
- La vue s'affiche instantanément (données déjà dans le store).
- Les deux sections existent avec leur empty state respectif.
- TypeScript strict : `position.current_price` est `number | null` — pas de coercition silencieuse, afficher `—` si null.

---

### Sprint 2 : WeeklyReport

**Objectif** : rapport hebdomadaire interactif calculé côté client depuis les données de trades existantes. Zéro endpoint supplémentaire.

**Composants à créer**

| Fichier | Rôle |
|---|---|
| `src/components/weekly/WeeklyReport.tsx` | Vue principale + orchestration |
| `src/components/weekly/WeeklyEquityCurve.tsx` | SVG courbe d'équité semaine |
| `src/components/weekly/ArmWeeklyTable.tsx` | Tableau per-arm : WR, PF, trades, P&L semaine |
| `src/components/weekly/BestWorstTrades.tsx` | Top 3 gagnants / Top 3 perdants |
| `src/components/weekly/WeekSelector.tsx` | Sélecteur semaine précédente / courante |
| `src/lib/weeklyStats.ts` | Fonctions pures de calcul (testables unitairement) |

**Données consommées**

- `useStore(s => s.trades)` — liste complète des trades (inclut `arm`, `pnl_usd`, `pnl_pct`, `timestamp_exit`, `token`, `exit_reason`, `peak_pct`, `duration_min`)
- `useStore(s => s.state.aggregate)` — pour la mise de départ de la courbe

**Calculs dans `weeklyStats.ts`** (fonctions pures, immutables)

```ts
// Filtre les trades d'une semaine ISO (lundi 00h00 → dimanche 23h59)
filterTradesByWeek(trades: Trade[], weekOffset: number): Trade[]

// Regroupe par arm et calcule WR, PF, P&L, trades count
computeArmStats(trades: Trade[]): ArmWeeklyStat[]

// Reconstruit une mini equity_series depuis les trades filtrés (triés par timestamp_exit)
buildWeeklyEquitySeries(trades: Trade[], startingEquity: number): number[]
```

**Comportements attendus**

1. **Sélecteur de semaine** : affiche "Cette semaine (lun 28 juil → dim 3 août)" avec deux boutons `←` / `→` pour naviguer. La semaine courante est le défaut. `→` désactivé si semaine courante.
2. **Headline P&L** : chiffre héroïque en `text-figure` ou plus grand, coloré `pnlColor`. Format `+$123.45 (+8.3%)`.
3. **Courbe d'équité hebdomadaire** : SVG identique à `EquityCurve` dans TheBrain, mais filtré sur la semaine sélectionnée. Points sur la courbe aux exits réels (pas d'interpolation temporelle). Si < 2 trades : `<NoSource>` avec explication.
4. **Tableau par bras** : colonnes `STRATÉGIE | TRADES | WR | PF | P&L`. Trié par P&L décroissant. Les zéros affichés comme `—`. Pas de hover state complexe.
5. **Best/Worst** : 3 cartes gagnantes (fond `bg-toxic/[0.04] border border-toxic/20`) et 3 cartes perdantes (fond `bg-blood/[0.04] border border-blood/20`), chacune montrant `token`, `arm`, `pnl_pct`, `exit_reason`, durée, pic si disponible.
6. **Empty state global** : si aucun trade cette semaine, message centré "Aucun trade clôturé cette semaine."

**Critères de succès**

- Le sélecteur de semaine fonctionne et recalcule toutes les métriques correctement.
- La courbe SVG s'anime (`pathLength: 0 → 1` comme dans TheBrain).
- `weeklyStats.ts` contient uniquement des fonctions pures (pas d'appels store, pas d'effets de bord).
- Le tableau per-arm est correct même si un bras n'a aucun trade cette semaine.
- Zéro fetch réseau déclenché par cette vue.

---

### Sprint 3 : ConfigEditor

**Objectif** : affichage structuré et lisible des paramètres de chaque bras via `/api/params`. Vue lecture seule. Gestion gracieuse du token d'authentification.

**Composants à créer**

| Fichier | Rôle |
|---|---|
| `src/components/config/ConfigEditor.tsx` | Vue principale avec sélecteur de bras |
| `src/components/config/ParamSection.tsx` | Section de paramètres nommée (ex: "Filtres") |
| `src/components/config/ParamValue.tsx` | Valeur formatée : nombre, booléen, null, objet imbriqué |
| `src/components/config/TokenGate.tsx` | Formulaire de saisie de token si nécessaire |
| `src/components/config/ArmPicker.tsx` | Sélecteur d'arm en tab horizontale |
| `src/lib/useArmParams.ts` | Hook custom de fetch avec cache local |

**Données consommées**

- `useStore(s => s.state.arms)` — liste des bras connus pour peupler l'`ArmPicker`
- Fetch `/api/params?arm={name}` sur sélection (+ `?token={token}` si token fourni)
- Réponse : `{ params: {...}, history: [...] }`

**Hook `useArmParams`**

```ts
type ArmParamsState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ok'; params: Record<string, unknown>; history: unknown[] }
  | { status: 'unauthorized' }
  | { status: 'error'; message: string }

function useArmParams(arm: string | null, token: string): ArmParamsState
```

- Cache en mémoire locale (`useRef<Map<string, ...>>`) : ne re-fetche pas si déjà chargé pour ce bras + même token.
- Status `unauthorized` si réponse 401 : déclenche l'affichage de `TokenGate`.
- Cleanup avec `AbortController` si l'arm change avant fin du fetch.

**Comportements attendus**

1. **ArmPicker** : tabs horizontales, même style que la NavBar (monospace, underline sur actif). Premier bras sélectionné par défaut.
2. **TokenGate** : si status `unauthorized`, affiche un champ `<input type="password">` sobre ("Ce bras nécessite un jeton — `DASHBOARD_TOKEN`"). Le token est stocké dans `useState` local uniquement, jamais dans le store ni dans localStorage.
3. **ParamSection** : regrouper les clés par section logique détectée depuis la structure du JSON retourné (`scan`, `filters`, `exit_rules`, `risk_rules`, `learning`, autres). Chaque section est un sous-groupe avec titre en `text-[10px] uppercase tracking-wide text-dim`.
4. **ParamValue** : les nombres sont en `font-mono text-ink`, les booléens en `text-toxic` (true) / `text-blood` (false), les null en `text-dim`, les objets imbriqués affichés en deux niveaux max (après : `{...}` cliquable pour expand local).
5. **History** : si `history.length > 0`, afficher les 5 derniers ajustements d'apprentissage sous forme de liste compacte, identique à `LearningFeed` dans TheBrain.
6. **Loading state** : texte sobre `Chargement des paramètres…` en `text-dim`, centré dans le Panel, sans spinner.
7. **Error state** : `<NoSource label="Paramètres indisponibles" why={message} />`.

**Critères de succès**

- Le fetch est déclenché une seule fois par bras (cache actif).
- Le token n'est jamais loggé, jamais dans le store.
- Status 401 → TokenGate visible, status 200 → paramètres affichés.
- L'arbre de paramètres ne crash pas sur des valeurs `null`, `undefined`, `[]`, `{}`.
- `useArmParams` utilise `AbortController` pour éviter les race conditions.

---

### Sprint 4 : Backtester

**Objectif** : visualisation du P&L cumulé et du drawdown par bras sur l'historique complet, avec comparaison de courbes multi-bras.

**Composants à créer**

| Fichier | Rôle |
|---|---|
| `src/components/backtester/Backtester.tsx` | Vue principale |
| `src/components/backtester/MultiCurveChart.tsx` | SVG multi-courbes P&L cumulé |
| `src/components/backtester/DrawdownChart.tsx` | SVG courbe de drawdown par bras |
| `src/components/backtester/ArmToggle.tsx` | Cases à cocher pour afficher/masquer un bras |
| `src/components/backtester/ArmSummaryTable.tsx` | Tableau récap : P&L total, drawdown max, WR, PF, trades |
| `src/lib/useArmEquity.ts` | Hook custom : fetch per-arm equity_series |
| `src/lib/drawdown.ts` | Calcul du drawdown max depuis une equity_series (fonction pure) |

**Données consommées**

- Fetch `/api/trades?arm={name}` pour chaque bras : la réponse contient `equity_series` (non bornée par `limit`). Cela donne la série chronologique complète du P&L de ce bras.
- `useStore(s => s.state.arms)` — liste des bras activés

**Hook `useArmEquity`**

```ts
type ArmEquityResult = {
  arm: string
  series: number[]   // equity_series brut (delta P&L par trade)
  status: 'loading' | 'ok' | 'error'
}

function useArmEquity(arms: Arm[]): ArmEquityResult[]
```

- Fetch en parallèle (`Promise.all`) pour tous les bras activés au montage.
- Stocké dans `useRef` pour éviter de re-fetcher à chaque rendu.
- Cleanup avec `AbortController`.

**SVG MultiCurveChart**

- Un SVG `viewBox="0 0 100 100" preserveAspectRatio="none"` commun à toutes les courbes.
- Chaque bras a une couleur unique tirée d'un tableau fixe de 7 couleurs dérivées du design system (pas de couleurs random) : `toxic`, `blood`, `warn`, `gem`, `muted`, `ink`, `dim/50`.
- Les courbes masquées (toggle) disparaissent avec `animate={{ opacity: 0 }}` framer-motion.
- Axes : min/max dynamiques sur les courbes visibles uniquement. Label Y en `$0`, label final en `$xxx`.
- La courbe agrégée (`arm=all`, via `equitySeries` déjà dans le store) est tracée en pointillés `stroke-dasharray="2 1"` comme référence.

**DrawdownChart**

- Calculé depuis `equity_series` via `drawdown.ts` :
  ```ts
  // Retourne un tableau de drawdown % à chaque point (0 = pas de drawdown)
  function computeDrawdownSeries(series: number[], startingEquity: number): number[]
  ```
- Même SVG, mêmes couleurs par bras, zone remplie en `fill-opacity: 0.1` sous chaque courbe.

**ArmSummaryTable**

- Colonnes : `BRAS | TRADES | WR | PF | P&L TOTAL | DRAWDOWN MAX`
- Calculé depuis `equity_series` + les trades du store pour WR/PF.
- Trié par P&L total décroissant.
- Highlight de la meilleure ligne (P&L max) avec `border-l-2 border-toxic`.

**Comportements attendus**

1. Au montage, fetcher toutes les equity_series en parallèle. Pendant le chargement : `text-dim` sobre dans chaque Panel.
2. `ArmToggle` : liste de checkboxes horizontale au-dessus du graphique. Style : badge `rounded px-2 py-0.5 text-[10px]` avec couleur de la courbe, coché = fond opaque, décoché = fond transparent.
3. Changer le toggle masque/affiche la courbe sans re-fetch.
4. Cliquer sur une ligne du tableau `ArmSummaryTable` met en avant cette courbe (opacité des autres à 0.2).
5. Empty state si aucun trade historique : `<Empty>Aucun historique de trades disponible.</Empty>`.
6. Offline : si `!connected`, bannière `text-warn` sobre.

**Critères de succès**

- Les fetches sont parallèles (pas de cascade sequentielle bras par bras).
- Le drawdown est calculé correctement (test : une equity_series `[10, -20, 5]` depuis capital 1000 donne un drawdown max à 20/(1000+10) = ~1.98%).
- Masquer tous les bras → le SVG est vide (ou affiche un placeholder).
- `drawdown.ts` est une fonction pure sans dépendance React.
- Aucune `console.log` dans le code livré.

---

## Architecture fichiers finale

```
dashboard/src/
├── components/
│   ├── NavBar.tsx                    NOUVEAU
│   ├── live/
│   │   ├── LivePrices.tsx            NOUVEAU
│   │   ├── PriceRow.tsx              NOUVEAU
│   │   ├── PositionBlock.tsx         NOUVEAU
│   │   └── CandidatesBlock.tsx       NOUVEAU
│   ├── weekly/
│   │   ├── WeeklyReport.tsx          NOUVEAU
│   │   ├── WeeklyEquityCurve.tsx     NOUVEAU
│   │   ├── ArmWeeklyTable.tsx        NOUVEAU
│   │   ├── BestWorstTrades.tsx       NOUVEAU
│   │   └── WeekSelector.tsx          NOUVEAU
│   ├── config/
│   │   ├── ConfigEditor.tsx          NOUVEAU
│   │   ├── ParamSection.tsx          NOUVEAU
│   │   ├── ParamValue.tsx            NOUVEAU
│   │   ├── TokenGate.tsx             NOUVEAU
│   │   └── ArmPicker.tsx             NOUVEAU
│   ├── backtester/
│   │   ├── Backtester.tsx            NOUVEAU
│   │   ├── MultiCurveChart.tsx       NOUVEAU
│   │   ├── DrawdownChart.tsx         NOUVEAU
│   │   ├── ArmToggle.tsx             NOUVEAU
│   │   └── ArmSummaryTable.tsx       NOUVEAU
│   ├── Panel.tsx                     EXISTANT (inchangé)
│   ├── Shell.tsx                     MODIFIÉ (NavBar intégrée)
│   ├── TheHunt.tsx                   EXISTANT (inchangé)
│   ├── TheArms.tsx                   EXISTANT (inchangé)
│   ├── ActivePositions.tsx           EXISTANT (inchangé)
│   └── TheBrain.tsx                  EXISTANT (inchangé)
├── lib/
│   ├── store.ts                      MODIFIÉ (+ view/setView)
│   ├── weeklyStats.ts                NOUVEAU
│   ├── useArmParams.ts               NOUVEAU
│   ├── useArmEquity.ts               NOUVEAU
│   ├── drawdown.ts                   NOUVEAU
│   ├── types.ts                      EXISTANT (inchangé ou extension minimale)
│   ├── format.ts                     EXISTANT (inchangé)
│   ├── useLiveState.ts               EXISTANT (inchangé)
│   └── empty.ts                      EXISTANT (inchangé)
└── App.tsx                           MODIFIÉ (routing par view)
```

## Technical Stack

- Frontend : React 19, Vite 8, TypeScript ~6.0, Tailwind CSS 4 (via plugin Vite), framer-motion 12, Zustand 5
- Backend : FastAPI Python, fichiers `state.json` / `trades_log.jsonl` / `shadow_log.jsonl`
- Graphiques : SVG inline (aucune librairie chart)
- Navigation : Zustand state (`view` string), pas de react-router
- Nouvelles dépendances autorisées : aucune

## Evaluation Criteria

Voir `eval-rubric.md`.

---

## Sprint Plan

### Sprint 1 : NavBar + LivePrices

**Goals**: Intégrer la navigation sans casser l'existant, puis la vue temps réel (zéro fetch).

**Fichiers modifiés**:
- `store.ts` : + `view`, `setView`
- `Shell.tsx` : + import `NavBar`, intégration dans le header sticky
- `App.tsx` : routing par `view`

**Fichiers créés**:
- `NavBar.tsx`
- `live/LivePrices.tsx`, `live/PriceRow.tsx`, `live/PositionBlock.tsx`, `live/CandidatesBlock.tsx`

**Definition of done**:
- Switching entre Dashboard et Live Prices fonctionne
- Les prix pulsent en vert/rouge à chaque push WS
- Aucun appel réseau depuis LivePrices
- TypeScript compile sans erreur (`tsc --noEmit`)

### Sprint 2 : WeeklyReport

**Goals**: Rapport hebdomadaire calculé côté client, navigable par semaine.

**Fichiers créés**:
- `weekly/WeeklyReport.tsx`, `weekly/WeeklyEquityCurve.tsx`, `weekly/ArmWeeklyTable.tsx`, `weekly/BestWorstTrades.tsx`, `weekly/WeekSelector.tsx`
- `lib/weeklyStats.ts`

**Definition of done**:
- Sélecteur de semaine fonctionne (← et →)
- La courbe SVG s'anime à l'affichage
- Le tableau par bras affiche des données cohérentes avec les trades du store
- `weeklyStats.ts` sans import React (pur TypeScript)

### Sprint 3 : ConfigEditor

**Goals**: Visualiser les paramètres de chaque bras, gérer le token d'auth.

**Fichiers créés**:
- `config/ConfigEditor.tsx`, `config/ParamSection.tsx`, `config/ParamValue.tsx`, `config/TokenGate.tsx`, `config/ArmPicker.tsx`
- `lib/useArmParams.ts`

**Definition of done**:
- Fetch déclenché une fois par bras (cache)
- 401 → TokenGate visible
- Paramètres imbriqués affichés sans crash
- AbortController actif

### Sprint 4 : Backtester

**Goals**: Courbes multi-bras, drawdown, comparaison.

**Fichiers créés**:
- `backtester/Backtester.tsx`, `backtester/MultiCurveChart.tsx`, `backtester/DrawdownChart.tsx`, `backtester/ArmToggle.tsx`, `backtester/ArmSummaryTable.tsx`
- `lib/useArmEquity.ts`
- `lib/drawdown.ts`

**Definition of done**:
- Fetches en parallèle au montage
- Toggle bras fonctionne (masque/affiche courbe)
- Cliquer une ligne du tableau met en avant la courbe
- `drawdown.ts` est une fonction pure testable
