# Evaluation Rubric — Alpha Loop Dashboard Extension

> Seuil de succès : **7.0 / 10**
> Score pondéré = 0.25 × Design/UX + 0.15 × Originalité + 0.30 × Craft + 0.30 × Fonctionnalité

---

## Axe 1 — Design / UX (poids 25 %)

Cohérence avec le design system existant, lisibilité à haute densité, états vides et offline.

### Critères et points

| Score | Critère |
|---|---|
| 10 | Palette 100% respectée (`void/surface/edge/ink/dim/toxic/blood/warn/gem`). Aucune couleur extérieure. Typographie monospace sur tous les chiffres. Hiérarchie claire : un seul chiffre héroïque par vue. États vides explicites et utiles (pas de `null` silencieux). Badge offline cohérent avec l'existant. NavBar visuellement intégrée au header (pas un élément flottant). |
| 8 | 1 à 2 écarts mineurs de palette (ex: un bg bleu par inadvertance) ou un état vide manquant. NavBar reconnaissable mais légèrement décalée du style existant. |
| 6 | Couleurs non-design-system sur plusieurs composants, OU hiérarchie plate (tout à la même taille), OU états vides absents sur 2+ vues. |
| 4 | Design régressif vs l'existant : fond clair, gradients décoratifs, ombres portées épaisses, modales, icônes SVG non-système. |
| 0–2 | Vue non rendue ou crash immédiat à l'affichage. |

**Points de vérification spécifiques**

- [ ] NavBar : tabs en `font-mono uppercase text-[10px]`, underline actif en `gem`, pas de pill/bouton/fond coloré
- [ ] LivePrices : `usePulse` utilisé pour les prix (animation toxic/blood), pas de couleur statique
- [ ] WeeklyReport : courbe SVG identique à `EquityCurve` (TheBrain) — même épaisseur de trait, mêmes couleurs conditionnelles
- [ ] ConfigEditor : valeurs bool en `text-toxic`/`text-blood`, nombres en `font-mono`, null en `text-dim`
- [ ] Backtester : les 7 couleurs de courbes restent dans la palette (aucun #ff6600 ou bleu HTML)
- [ ] Offline sur LivePrices et Backtester : bannière `text-warn` sobre, pas de spinner coloré

---

## Axe 2 — Originalité (poids 15 %)

Vues non-triviales, non-génériques. Ce qui ferait dire "c'est clairement conçu pour ce bot, pas copié d'un template".

### Critères et points

| Score | Critère |
|---|---|
| 10 | Chaque vue apporte un insight que les 4 panneaux existants n'ont pas. LivePrices : le delta prix vs entrée visible d'un coup d'oeil. WeeklyReport : le sélecteur de semaine est fonctionnel et la courbe hebdomadaire a des labels informatifs. ConfigEditor : l'arbre de paramètres révèle la structure métier (filtres / règles de sortie / risque) sans être un dump JSON brut. Backtester : le toggle per-arm et la mise en avant au clic du tableau sont opérationnels. |
| 8 | 3 vues sur 4 originales, 1 est une liste générique sans valeur ajoutée. |
| 6 | 2 vues originales, 2 vues sont des listes triviales (tableau colonne-valeur sans hiérarchie). |
| 4 | Les 4 vues sont des variants de tableau générique sans insight spécifique au bot. |
| 0–2 | Vues non fonctionnelles ou affichant des données hardcodées. |

**Points de vérification spécifiques**

- [ ] WeeklyReport : le sélecteur de semaine navigue correctement (pas juste une range date fixe)
- [ ] Backtester : la courbe agrégée en pointillés (référence) est distincte des courbes per-arm
- [ ] ConfigEditor : regroupement par section logique (`filters`, `exit_rules`, etc.) — pas un dump `Object.entries` plat
- [ ] LivePrices : l'âge du dernier push WS est visible quelque part (pas seulement "offline")

---

## Axe 3 — Craft (poids 30 %)

TypeScript correct, hooks propres, immutabilité, pas de console.log, architecture cohérente avec l'existant.

### Critères et points

| Score | Critère |
|---|---|
| 10 | `tsc --noEmit` passe sans erreur. Aucun `any` dans les nouveaux fichiers. Aucun `console.log`. `AbortController` dans `useArmParams` et `useArmEquity`. `weeklyStats.ts` et `drawdown.ts` sont purs (pas d'import React, pas d'effets de bord). Aucune mutation de l'état du store autre que via les setters Zustand. Composants < 200 lignes chacun. `usePulse` réutilisé depuis `useLiveState.ts` plutôt que recréé. Dépendances `useEffect` exhaustives (pas de `// eslint-disable`). |
| 8 | 1 à 2 `any` isolés avec commentaire justifié, OU 1 composant > 200 lignes sans extraction, OU `AbortController` manquant dans 1 hook sur 2. |
| 6 | `any` fréquents sans justification, OU absence d'`AbortController` sur les deux hooks, OU `console.log` présents. |
| 4 | Erreurs TypeScript (`tsc` échoue), OU mutations directes du store Zustand, OU les fonctions pures importent des hooks React. |
| 0–2 | Le projet ne compile pas. |

**Points de vérification spécifiques**

- [ ] `store.ts` : `view` et `setView` ajoutés avec le bon type `View` (union littérale)
- [ ] `useArmParams.ts` : `useRef<AbortController>` ou équivalent, cleanup dans `useEffect` return
- [ ] `useArmEquity.ts` : fetch en `Promise.all`, pas de boucle `await` séquentielle
- [ ] `weeklyStats.ts` : exporte des fonctions `(trades: Trade[], ...) => ...`, pas de `useStore`
- [ ] `drawdown.ts` : exporte `(series: number[], startingEquity: number) => number[]`, testable sans React
- [ ] Aucun `console.log`, `console.error`, `console.warn` dans les fichiers livrés
- [ ] Les composants réutilisent `Panel`, `NoSource`, `Empty`, `Stat` de `Panel.tsx` — pas de réimplémentation
- [ ] `format.ts` (`usd`, `pct`, `pnlColor`, `price`, `compact`, `duration`) utilisé partout, pas de `toFixed(2) + '$'` inline

---

## Axe 4 — Fonctionnalité (poids 30 %)

Les 4 vues fonctionnent, la navigation est opérationnelle, les données réelles sont affichées.

### Critères et points

| Score | Critère |
|---|---|
| 10 | Navigation fonctionne (5 tabs, retour à Dashboard). Toutes les 4 vues s'affichent sans crash. LivePrices : les prix pulsent sur push WS. WeeklyReport : le sélecteur de semaine change les données. ConfigEditor : les paramètres d'au moins 1 bras sont lisibles (token optionnel géré). Backtester : les courbes per-arm s'affichent après fetch, le toggle masque/affiche. |
| 8 | 3 vues sur 4 pleinement fonctionnelles. 1 vue affiche correctement mais sans interactivité (ex: Backtester sans toggle). |
| 6 | 2 vues sur 4 fonctionnelles. Navigation présente mais 1 tab crashe. |
| 4 | Navigation cassée (retour au Dashboard impossible) OU 3+ vues crashent à l'affichage. |
| 0–2 | Aucune nouvelle vue ne s'affiche ou l'application crashe au montage. |

**Scénarios de test critiques**

1. **Navigation** : cliquer chaque tab dans l'ordre, vérifier que le contenu change et que `Dashboard` restaure les 4 panneaux originaux.
2. **LivePrices sans données** : avec un bot offline (`connected = false`), la vue affiche ses empty states — elle ne crash pas.
3. **WeeklyReport semaine vide** : naviguer vers une semaine sans trades → message "Aucun trade clôturé cette semaine" visible sur chaque section.
4. **ConfigEditor sans token** : appeler `/api/params` sans token (DASHBOARD_TOKEN non défini) → les paramètres s'affichent normalement sans passer par TokenGate.
5. **ConfigEditor avec 401** : si DASHBOARD_TOKEN est défini côté serveur et token absent côté client → TokenGate s'affiche, pas de crash.
6. **Backtester chargement** : au montage de la vue, les fetches per-arm sont lancés, un état de chargement sobre est visible, puis les courbes apparaissent.
7. **Backtester toggle** : décocher un bras → sa courbe disparaît. Le recoche → réapparaît. L'état du toggle survit à un changement de vue et retour (ou se remet à zéro, les deux sont acceptables si cohérent).
8. **Pulse LivePrices** : si le bot est en ligne et que des positions sont ouvertes, ouvrir DevTools, observer un push WS → les prix s'animent brièvement.

---

## Calcul du score

```
score = 0.25 × design + 0.15 × originalité + 0.30 × craft + 0.30 × fonctionnalité
```

Seuil de succès : **score ≥ 7.0**

| Fourchette | Verdict |
|---|---|
| 9.0 – 10.0 | Excellent — livrable en production |
| 7.0 – 8.9 | Succès — quelques ajustements mineurs |
| 5.0 – 6.9 | Échec — itération nécessaire |
| < 5.0 | Rejet — refaire depuis la spec |

---

## Pénalités automatiques

Ces conditions peuvent faire baisser le score d'un axe entier d'un niveau :

| Condition | Pénalité |
|---|---|
| `tsc --noEmit` échoue | Craft → max 4/10 |
| `console.log` présents dans les fichiers livrés | Craft → −1 point |
| Composants existants (Panel, TheHunt, etc.) modifiés sans justification dans la spec | Craft → −1 point |
| Nouvelle dépendance npm ajoutée sans justification | Originalité → −2 points |
| Couleur HTML non-design-system présente visuellement | Design → −1 point par occurrence (max −3) |
| Race condition avérée (fetch sans AbortController qui produit des données obsolètes) | Fonctionnalité → −2 points |
