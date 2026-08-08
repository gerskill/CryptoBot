# 009. Le coût de sortie réel n'est mesuré qu'une fois, sur la jambe finale

Date : 2026-08-06
Statut : accepté

## Contexte

`PaperPortfolio` calculait le P&L réalisé sur le prix nu (`position.pnl_pct(price)`).
`round_trip_cost_pct` de Jupiter existait déjà, mais ne servait qu'à FILTRER
l'entrée (`economics.evaluate`) et DIMENSIONNER la position (`size_for_cost`)
— jamais à corriger le P&L affiché. Sur 20K de liquidité, l'aller-retour coûte
plusieurs points de pourcentage (médiane 3,06 % mesurée) ; ne pas le déduire
rend le papier optimiste par rapport au réel.

Une position peut sortir en plusieurs jambes : TP1 vend une fraction, TP2 une
autre fraction du reste, et la sortie finale (stop loss, time stop, trailing,
TP3, rug pull) liquide ce qui reste. Se posait la question de mesurer un coût
à chaque jambe ou une seule fois.

## Décision

Le coût n'est mesuré qu'**une seule fois par position, sur la jambe finale**
(`action.is_final`, qui n'est vrai que lorsque `fraction == remaining_fraction`
— la seule jambe où « ce qui sort » coïncide avec « tout ce qui reste »). Les
jambes partielles (TP1, TP2) restent au prix nu, sans coût déduit, comme
avant.

`force_close()` (le panic button) route par **le même mesureur de coût** que
la sortie normale — un panic close arrive typiquement dans les pires
conditions de marché (congestion, stress), donc c'est précisément le moment
où laisser le P&L au prix nu serait le plus trompeur.

Le notionnel envoyé au devis est la valeur **courante** de ce qui est vendu
(`size_usd * fraction * price / entry_price`), pas la mise figée à l'entrée :
sur une position qui a fait x3, sous-dimensionner le notionnel sous-estime le
devis d'impact de prix. Trouvé en revue adversariale Codex.

Deux composantes mesurées indépendamment dans `src/core/exit_fees.py` :

- **Impact de prix** — `jupiter.round_trip_cost_pct` : un devis Jupiter réel
  (achat puis vente), pas une formule. Voir ADR 010.
- **Priority fee** — `helius.get_recent_prioritization_fee_lamports` :
  médiane des prix récents par unité de calcul, multipliée par un budget de
  CU hypothétique (le vrai budget n'est connu qu'à la construction de la
  transaction, hors périmètre du bot).

Si une seule composante est mesurable, le coût est quand même déduit avec
cette seule composante — `ExitCost.partial` et `TradeJournal` enregistrent
que la mesure est partielle plutôt que de l'ignorer silencieusement. Si
aucune des deux n'est mesurable, `measure_exit_cost` retourne `None` et
l'appelant garde le P&L au prix nu — jamais de coût inventé.

## Conséquences

- Le coût réel n'apparaît que sur la clôture qui liquide le solde d'une
  position. Une position sortie en trois jambes (TP1 partiel, TP2 partiel,
  solde en stop) n'a de coût mesuré que sur la troisième. Les deux premières
  restent optimistes.
- Un seul devis Jupiter par position fermée, pas un par jambe — coût en
  requêtes maîtrisé, cohérent avec le rate limit Jupiter (1 req/s).
- `journal.record_exit` porte `exit_cost_pct` et `exit_cost_partial` sur
  chaque ligne ; `None` sur les jambes partielles et sur toute sortie où ni
  Jupiter ni Helius n'ont répondu.
- Une mesure de coût ne fait jamais échouer une clôture réelle : toute
  exception de `exit_fee_measurer` est absorbée, `exit_cost` retombe à
  `None` — invariant « la boucle ne meurt jamais ».
