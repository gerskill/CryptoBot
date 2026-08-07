# Generator State — Iteration 001

## What Was Built

- Slice `view`/`setView` dans `store.ts` (`View = 'dashboard' | 'live' | 'weekly' | 'config' | 'backtester'`).
- `NavBar.tsx` intégré dans `Shell.tsx` entre le header et `<main>`, routing dans `App.tsx`.
- **Sprint 1 — Live Prices** : `LivePrices.tsx`, `PositionBlock.tsx`, `CandidatesBlock.tsx`, `PriceRow.tsx` (`PositionRow`/`CandidateRow`). Zéro fetch, tout vient du store poussé par le WS. Ticker header avec compteur de secondes retiqué localement (`updated_at`). Bannière offline inline. Empty states dédiés.
- **Sprint 2 — Weekly Report** : `weeklyStats.ts` (fonctions pures : `filterTradesByWeek`, `computeArmStats`, `buildWeeklyEquitySeries`, `getWeekRange`, `formatWeekLabel`), `WeekSelector.tsx`, `WeeklyEquityCurve.tsx` (SVG animé, motif `EquityCurve` de TheBrain), `ArmWeeklyTable.tsx` (complète les bras sans trade de la semaine avec une ligne zéro), `BestWorstTrades.tsx`, `WeeklyReport.tsx` (assembly).
- **Sprint 3 — Config Editor** : `useArmParams.ts` (cache `useRef` par bras+token, `AbortController`, statuts `idle/loading/ok/unauthorized/error`), `TokenGate.tsx` (jeton en `useState` local uniquement), `ArmPicker.tsx`, `ParamValue.tsx` (rendu récursif, 2 niveaux puis `{...}`/`[n]` cliquable, gère `null`/`undefined`/`[]`/`{}` sans crash), `ParamSection.tsx` (regroupement scan/filters/exit_rules/risk_rules/learning/autres), `ConfigEditor.tsx` (assembly + historique des 5 derniers ajustements).
- **Sprint 4 — Backtester** : `drawdown.ts` (fonction pure `computeDrawdownSeries`/`computeMaxDrawdown`, vérifiée sur le cas de référence de la spec : `[10,-20,5]` depuis 1000 → drawdown max ≈ 1.98 %), `useArmEquity.ts` (fetch parallèle `Promise.all` par bras, cache `useRef`, `AbortController`), `armColors.ts` (7 couleurs fixes du design system), `ArmToggle.tsx`, `MultiCurveChart.tsx` (SVG multi-courbes + courbe agrégée en pointillés), `DrawdownChart.tsx` (aires remplies `fill-opacity: 0.1`), `ArmSummaryTable.tsx` (tri P&L décroissant, meilleure ligne `border-l-2 border-toxic`, clic met en avant la courbe), `Backtester.tsx` (assembly, toggle sans re-fetch, offline banner, empty state).

## What Changed This Iteration

Première itération — pas de feedback préalable. Le NavBar, `store.ts` (view slice), `Shell.tsx`, `App.tsx`, ainsi que `PositionBlock.tsx`/`PriceRow.tsx` avaient déjà été amorcés avant le lancement du harness ; complétés et étendus (ajout de la liquidité compacte sur `CandidateRow`) plutôt que réécrits.

## Known Issues

- `ArmWeeklyTable`/`ArmSummaryTable` affichent `∞` quand un bras n'a que des gains sans perte (profit factor infini) — comportement volontaire, à valider par l'évaluateur.
- Le backtester calcule WR/PF/drawdown directement depuis `equity_series` (complet, non borné par `limit`) plutôt que depuis `s.trades` (borné à 100, toutes stratégies confondues) : plus correct par bras, mais diverge légèrement de la formulation littérale de la spec ("+ les trades du store pour WR/PF").
- Pas de tests unitaires écrits pour `weeklyStats.ts` / `drawdown.ts` (fonctions pures, faciles à tester, mais aucun harness de test n'existe encore dans `dashboard/`).

## Dev Server

- URL : http://localhost:5173 (Vite, proxy `/api` et `/ws` vers `http://localhost:8000`)
- Statut : déjà en cours d'exécution (PID 69525), aucun redémarrage nécessaire.
- API backend : déjà en cours d'exécution sur le port 8000 (uvicorn).
- Commande : `cd dashboard && npm run dev`
- Vérifications effectuées : `npx tsc -b --force` (0 erreur), `npx oxlint src` (0 warning/erreur), `npm run build` (succès, 377 kB JS / 35 kB CSS gzip-compressés), `grep -rn "console.log" src/` (aucune occurrence).
