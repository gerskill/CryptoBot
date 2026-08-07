# GAN Harness Build Report

**Brief:** Étendre le dashboard CryptobBot existant avec 4 nouvelles vues (Live Prices, Weekly Report, Config Editor, Backtester) intégrées dans le Shell existant, design system inchangé.
**Result:** PASS
**Iterations:** 1 / 15 (max)
**Final Score:** 8.05 / 10 (seuil 7.0)

## Score Progression

| Iter | Design | Originalité | Craft | Fonctionnalité | Total |
|------|--------|-------------|-------|-----------------|-------|
| 1 | 7.0 | 8.0 | 8.0 | 9.0 | **8.05** |

Convergence immédiate — pas d'itération 2 nécessaire, seuil dépassé dès le premier passage.

## Détail du score (poids : 25/15/30/30)

| Axe | Score | Contribution |
|---|---|---|
| Design/UX | 7/10 | 1.75 |
| Originalité | 8/10 | 1.20 |
| Craft | 8/10 | 2.40 |
| Fonctionnalité | 9/10 | 2.70 |

## Ce qui a été livré

- **NavBar** — 5 tabs (Dashboard / Live Prices / Weekly / Config / Backtester), routage via slice Zustand `view`, intégrée au header sticky existant.
- **LivePrices** — positions ouvertes + candidats du cycle, zéro fetch réseau (données déjà dans le store via WS), prix pulsants avec `usePulse`.
- **WeeklyReport** — rapport hebdomadaire calculé côté client (`weeklyStats.ts`, fonctions pures), sélecteur de semaine, courbe d'équité SVG animée, top 3 gagnants/perdants.
- **ConfigEditor** — paramètres complets par bras via `/api/params`, gestion du token (`TokenGate` si 401), rendu récursif borné (`ParamValue`).
- **Backtester** — courbes multi-bras P&L cumulé + drawdown, fetch parallèle per-arm (`useArmEquity`), toggle et highlight au clic.

28 fichiers livrés à l'itération 1, 1 fichier corrigé après évaluation.

## Vérifications

- `tsc -b --force` : 0 erreur (avant et après le fix)
- `oxlint src` : 0 warning
- `npm run build` : OK — 377 kB JS / 35 kB CSS gzip
- Aucun `console.log` dans le code livré
- Testé live via Playwright : navigation, empty states, offline banner, toggle Backtester, sélecteur de semaine — tous fonctionnels
- Drawdown vérifié contre le cas de référence de la spec : `[10, -20, 5]` depuis capital 1000 → ≈1.98%, exact

## Bug trouvé et corrigé post-évaluation

`ParamValue.tsx` ne repliait les valeurs qu'à partir de la profondeur 2, pas selon la longueur. Un array de premier niveau (`parameter_adjustment_history`, 50+ entrées) se rendait donc en entier, produisant une page ConfigEditor d'environ 8600px. **Fix appliqué** : seuil de longueur (5 éléments) qui replie l'array indépendamment de la profondeur. Vérifié par `tsc` après coup, commit `417cc30`.

## Problèmes mineurs restants (non bloquants, sous le seuil de correction automatique)

- `PriceRow.tsx:33` — un `toFixed()` contourne le helper `usd()` de `format.ts` au lieu de le réutiliser.
- `NavBar.tsx` — tabs qui débordent/se coupent à 375px sans affordance de scroll (mobile étroit).
- Le chemin `TokenGate`/401 n'a pas pu être testé en live (le serveur API tournant n'a pas `DASHBOARD_TOKEN` défini) ; vérifié par lecture de code (`useArmParams.ts`) uniquement.

## Fichiers créés

```
gan-harness/spec.md
gan-harness/eval-rubric.md
gan-harness/feedback/feedback-001.md
gan-harness/build-report.md
gan-harness/screenshots/iteration-1-*.png (18 captures)
```

## Commits

- `29a3d45` — feat: dashboard +4 vues (live, weekly, config, backtester) + NavBar
- `417cc30` — fix: borner les arrays longs dans ParamValue (ConfigEditor)
