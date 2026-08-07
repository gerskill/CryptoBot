# Feedback — Itération 1

## Score pondéré : 8.05 / 10

## Verdict : PASS (seuil 7.0)

| Axe | Score | Poids | Contribution |
|---|---|---|---|
| Design/UX | 7/10 | 25% | 1.75 |
| Originalité | 8/10 | 15% | 1.20 |
| Craft | 8/10 | 30% | 2.40 |
| Fonctionnalité | 9/10 | 30% | 2.70 |
| **TOTAL** | | | **8.05/10** |

---

## Méthodologie

Test live via Playwright (chromium) contre le dashboard Vite (`localhost:5173`, proxy `/api`+`/ws` vers l'API FastAPI sur `:8000`, PAPER mode réel, ~48 trades, 6 bras actifs). Navigation complète des 5 tabs, sélecteur de semaine (dont une semaine sans trade), toggle/highlight du Backtester, responsive 375px, stress test 10 clics rapides, lecture de code pour les 28 fichiers livrés, `tsc -b --force` et `oxlint src` réexécutés indépendamment (les deux passent, 0 erreur/warning). `git show 29a3d45 --stat` confirme un diff chirurgical : seuls `Shell.tsx` (+3 lignes), `store.ts` (+6 lignes) et `App.tsx` (+27 lignes) touchent l'existant — aucune modification de `Panel.tsx`, `TheBrain.tsx`, `TheHunt.tsx`, `TheArms.tsx`, `ActivePositions.tsx`, `format.ts`, `types.ts`, `useLiveState.ts`.

**Incident d'évaluation résolu en cours de route** : le premier passage a capturé une courbe `WeeklyEquityCurve` étirée sur ~1300px (le `h-24` Tailwind n'était pas dans le CSS servi par le dev server). `npm run build` frais confirme `.h-24{height:calc(var(--spacing) * 24)}` présent dans le CSS de prod — c'était un cache Vite dev stale (`node_modules/.vite`), pas un bug de code. Après purge du cache et redémarrage, la courbe s'affiche correctement à 96px. Noté pour transparence, non compté contre le score.

---

## Détail par axe

### Design/UX — 7/10

Points forts vérifiés :
- Palette 100% respectée sur les 24 nouveaux fichiers (`xargs grep` pour hex/blue/purple/indigo/violet/gradient → zéro occurrence).
- `NavBar.tsx` conforme au pixel près : `font-mono uppercase text-[10px] tracking-[0.15em]`, underline actif `bg-gem`, `<nav aria-label="Navigation principale">` intégrée directement sous le header (pas flottante) — capture `iteration-1-dashboard.png`.
- Empty states présents et utiles sur les 4 vues : `Aucun candidat retenu ce cycle.` (LivePrices), `Aucun trade clôturé cette semaine.` (Weekly, testé en naviguant à la semaine du 22-28 juin), `Aucun bras affiché — coche une stratégie ci-dessus.` + `Pas assez de données.` (Backtester, testé en décochant les 6 bras un par un).
- Bannières offline `text-warn` sobres présentes sur LivePrices et Backtester (`connected` du store, pas de spinner).
- Backtester : 7 couleurs de courbes toutes dérivées de variables CSS du design system (`var(--color-toxic/blood/warn/gem/muted/ink)` + `rgba(90,92,106,...)` pour dim/50) — aucune couleur random, aucun `#hexcode` HTML.

Problèmes trouvés :
1. **ConfigEditor devient un dump JSON brut sur le champ `parameter_adjustment_history`** (`dashboard/src/components/config/ParamValue.tsx`). Le cap "2 niveaux max" ne s'applique qu'à la *profondeur*, jamais à la *longueur* d'un tableau. Quand le JSON `/api/params` embarque nativement un historique de dizaines d'ajustements dans la section "autres" (redondant avec le `HistoryList` déjà bien formaté), `ParamValue` le déroule intégralement sans troncature — capture `iteration-1-config.png` fait **8641px de haut** en full-page pour un seul bras. Ça contredit directement le point de vérification Design "lisibilité à haute densité" et frôle l'anti-pattern "dump JSON brut" que l'Originalité pénalise explicitement.
2. **NavBar overflow à 375px** : `scrollWidth` mesuré à 458px pour un `viewport` de 375px — l'onglet `BACKTESTER` est tronqué à la lettre `B` sans indice visuel de scroll horizontal (capture `iteration-1-dashboard-mobile.png`). Le spec ne mandate pas explicitement le mobile pour ce terminal de trading desktop, donc pénalité mineure plutôt que majeure — mais la règle globale de test (320-1920px) le signale.
3. Mineur : `PriceRow.tsx:33` compose `${position.pnl_usd >= 0 ? '+' : ''}${position.pnl_usd.toFixed(2)}` inline au lieu de `usd(position.pnl_usd, 2)` — exactement l'anti-pattern cité au point de vérification Craft du rubric ("pas de `toFixed(2)` + `'$'` inline"), un seul site concerné.

### Originalité — 8/10

- **LivePrices** : delta prix vs entrée visible d'un coup d'œil (`pnl_pct`/`pnl_usd` colorés `pnlColor`), pulse `usePulse` réutilisé sur `current_price` et `price_usd`. Âge du dernier push visible via le ticker `"5 positions · 0 candidat · mis à jour il y a 3s"` — répond explicitement au point de vérification.
- **WeeklyReport** : sélecteur de semaine réellement fonctionnel (testé : navigation "Cette semaine (lun 3 août → dim 9 août)" → "Semaine de (lun 22 juin → dim 28 juin)", le P&L, la courbe, le tableau par bras et les best/worst trades se recalculent tous correctement, jusqu'à afficher l'empty state sur la semaine vide). Labels de courbe informatifs (`$42,000` → `$41,565.23`).
- **Backtester** : courbe agrégée en pointillés bien distincte visuellement (`stroke-dasharray="2 1"`, `var(--color-dim)`) des courbes per-arm colorées et pleines (capture `iteration-1-backtester.png`). Toggle et highlight au clic vérifiés en live (captures `iteration-1-backtester-baseline-off.png`, `iteration-1-backtester-all-off.png`, `iteration-1-backtester-row-highlight.png`).
- **ConfigEditor** : regroupement par section logique réel (`SCAN`, `FILTERS`, puis plus bas `AUTRES` avec `entry_rules`, `scoring_weights`, `api_budgets`) — ce n'est PAS un `Object.entries` plat pour les paramètres eux-mêmes. Seul le sous-champ `parameter_adjustment_history` dégénère en dump (cf. Design #1), ce qui retire un point plutôt que deux : le reste de la structure métier est authentiquement révélé.

### Craft — 8/10

Vérifications indépendantes toutes positives :
- `tsc -b --force` → 0 erreur (reproduit indépendamment de la revendication du générateur).
- `oxlint src` → 0 warning/erreur (reproduit).
- Zéro `any`, zéro `console.*`, zéro `eslint-disable` sur les 24 nouveaux fichiers (`xargs grep` exhaustif).
- `useArmParams.ts` : `AbortController` créé par fetch, `return () => controller.abort()` dans le `useEffect`, cache `useRef<Map>` clé `${arm}::${token}`.
- `useArmEquity.ts` : `Promise.all` sur les fetches (pas de boucle séquentielle), cache `useRef<Map>` par nom de bras, `AbortController` avec cleanup.
- `weeklyStats.ts` et `drawdown.ts` : zéro import React, zéro appel `useStore`, fonctions pures exportées avec la signature exacte demandée par le spec (`filterTradesByWeek`, `computeArmStats`, `buildWeeklyEquitySeries`, `computeDrawdownSeries`). Cas de référence `[10,-20,5]` depuis 1000 → drawdown ≈ 1.98% vérifié par lecture du code (formule correcte : `peak = Math.max(peak, running)`, `(peak-running)/peak*100`).
- Tous les composants < 200 lignes (le plus gros est `Backtester.tsx` à 140 lignes).
- `usePulse` importé depuis `useLiveState.ts` dans `PriceRow.tsx`, jamais recréé.
- `Panel`/`NoSource`/`Empty` réutilisés dans 6 fichiers de plus haut niveau (`WeeklyReport`, `WeeklyEquityCurve`, `CandidatesBlock`, `PositionBlock`, `ConfigEditor`, `Backtester`) — architecture cohérente avec l'existant.
- `store.ts` : `type View = 'dashboard' | 'live' | 'weekly' | 'config' | 'backtester'` en union littérale exacte du spec.

Déductions :
- Le bypass `toFixed(2)` isolé dans `PriceRow.tsx` (cf. Design #3).
- `ParamValue.tsx` ne plafonne la récursion que par profondeur, jamais par longueur de tableau — c'est un vrai gap d'architecture (pas juste une question de contenu de données), qui a permis le dump de Design #1. Le composant respecte la lettre du spec ("deux niveaux max") mais pas son intention ("sans être un dump JSON brut").

### Fonctionnalité — 9/10

Tous les scénarios critiques du rubric testés en live sauf un (justifié) :
1. **Navigation** : 5 tabs cliqués dans l'ordre + 10 clics rapides en boucle (stress test) → aucun crash, aucune `pageerror` console. Retour Dashboard restaure les 4 panels originaux (`voir tout (48)` toujours présent).
2. **LivePrices offline** : bannière/empty states vérifiés dans le code (`!connected` → `text-warn` inline), bot étant en ligne pendant le test je n'ai pas pu forcer un vrai offline sans couper l'API — logique correcte à la lecture.
3. **WeeklyReport semaine vide** : testé en live, message centré affiché correctement.
4. **ConfigEditor sans token** : testé en live — `DASHBOARD_TOKEN` non défini côté serveur (`grep DASHBOARD_TOKEN api/server.py` confirme `os.getenv(...) or None`), les paramètres de `baseline` puis `sniper` s'affichent directement sans passer par `TokenGate`.
5. **ConfigEditor avec 401** — **non testé en live** : `DASHBOARD_TOKEN` n'est pas configuré sur l'instance API en cours d'exécution (celle du bot PAPER réel), et je n'ai pas voulu redémarrer ce process pour y injecter la variable le temps du test. Vérifié uniquement par lecture de `useArmParams.ts` (`if (response.status === 401) setState({status:'unauthorized'})`) — le code est correct, mais ce chemin n'a pas de preuve d'exécution live cette itération.
6. **Backtester chargement** : `network log` confirme les 6 requêtes `/api/trades?arm=...` parties au même timestamp (fetch parallèle réel, pas de cascade).
7. **Backtester toggle** : testé exhaustivement — décocher un bras fait disparaître sa courbe (`iteration-1-backtester-baseline-off.png`), tout décocher affiche les deux empty states dédiés (`iteration-1-backtester-all-off.png`).
8. **Pulse LivePrices** : `usePulse` correctement câblé sur `current_price`/`price_usd` dans le code ; le bot étant en PAPER avec positions ouvertes réelles, les prix affichés changent bien entre captures successives (`$0.000116` → valeurs différentes au fil des cycles de 90s), confirmant que le flux WS alimente la vue sans fetch additionnel (network log : zéro requête `/api/*` déclenchée en restant sur l'onglet Live Prices).

Le seul défaut réel : la duplication de fetch au montage/démontage causée par `<StrictMode>` (visible dans le network log — chaque visite de Config/Backtester déclenche deux appels identiques). C'est un comportement standard React 18/19 en dev (double-invocation des effets), protégé par l'`AbortController`, donc pas une race condition réelle — mais ça aurait mérité d'être mentionné dans `generator-state.md` pour éviter toute confusion en review.

---

## Bugs / problèmes trouvés

1. **[Majeur]** `ParamValue.tsx` (dashboard/src/components/config/ParamValue.tsx) ne plafonne pas la longueur des tableaux, seulement leur profondeur → le champ `parameter_adjustment_history` (et probablement d'autres champs analytiques type `win_rate_by_liquidity`) produit un dump quasi brut de dizaines d'entrées, rendant la page Config de ~8600px de haut pour un seul bras. **Fix suggéré** : ajouter une constante `MAX_ARRAY_ITEMS` (ex. 5) à côté de `MAX_INLINE_DEPTH`, et faire en sorte que `ConfigEditor`/`ParamSection` exclue explicitement les clés déjà couvertes par `HistoryList` (`parameter_adjustment_history` en particulier) de l'arbre `ParamValue` générique, pour éviter la redondance.
2. **[Mineur]** `PriceRow.tsx:33` : `${position.pnl_usd >= 0 ? '+' : ''}${position.pnl_usd.toFixed(2)}` → remplacer par `usd(position.pnl_usd, 2)` (déjà importé dans le fichier).
3. **[Mineur]** NavBar tronquée à 375px sans scroll horizontal signalé visuellement. **Fix suggéré** : `overflow-x-auto` sur le conteneur des tabs + `flex-shrink-0` sur chaque `<button>`, ou compresser les labels sur mobile (`LIVE` au lieu de `LIVE PRICES`).
4. **[Non-bloquant, hors périmètre code]** Cache Vite dev (`node_modules/.vite`) stale ayant produit un CSS partiel au premier chargement de cette session d'éval — sans impact sur le build de prod (vérifié `.h-24` présent après `npm run build` frais). À surveiller si ça se reproduit systématiquement après un ajout de nouvelle classe Tailwind en cours de session longue.

## Ce qui a bien fonctionné

- Diff chirurgical sur l'existant (3 fichiers touchés, +36 lignes cumulées hors nouveaux fichiers) — exemplaire par rapport à la contrainte "composants existants réutilisés".
- Les deux hooks de fetch (`useArmParams`, `useArmEquity`) sont un exemple propre d'`AbortController` + cache `useRef` + `Promise.all`, directement conformes aux specs techniques.
- Empty states et bannières offline pensés dès la conception, pas ajoutés après coup — visibles et cohérents sur les 3 vues qui en ont besoin.
- Toggle/highlight du Backtester fonctionnellement complet et testé exhaustivement en live (y compris le cas "tout décoché").

## Recommandations pour l'itération suivante

1. **Prioritaire** : plafonner `ParamValue` par nombre d'éléments en plus de la profondeur, et/ou filtrer `parameter_adjustment_history` (et champs analytiques similaires volumineux) hors de l'arbre générique dans `ConfigEditor`/`ParamSection` puisqu'il fait déjà l'objet d'un traitement dédié via `HistoryList`.
2. Remplacer le `toFixed(2)` inline de `PriceRow.tsx` par `usd()`.
3. Ajouter `overflow-x-auto` à la NavBar pour un dégradé propre sous 400px, ou prévoir des labels courts en dessous d'un breakpoint.
4. Si le temps le permet, valider en live le chemin `TokenGate`/401 (démarrer une instance API secondaire avec `DASHBOARD_TOKEN` défini sur un port distinct, sans toucher au process PAPER en production) plutôt que de se reposer uniquement sur la lecture de code.

## Screenshots

Tous dans `gan-harness/screenshots/`, préfixe `iteration-1-` : `dashboard.png`, `dashboard-return.png`, `dashboard-mobile.png`, `live-prices.png`, `live-prices-mobile.png`, `weekly.png`, `weekly-prev.png` (semaine vide), `config.png`/`config-viewport.png`/`config-bottom.png`/`config-expanded.png` (dump problématique)/`config-sniper.png`, `backtester.png`/`backtester-fresh.png`/`backtester-baseline-off.png`/`backtester-all-off.png`/`backtester-row-highlight.png`/`backtester-loading.png`, `after-rapid-clicks.png` (stress test navigation).
