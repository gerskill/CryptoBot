# 010. Le slippage se mesure par deux devis Jupiter réels, jamais par une formule

Date : 2026-08-06
Statut : accepté

## Contexte

L'ADR 009 déduit un coût de sortie réel du P&L, mais ce coût doit venir de
quelque part. Sur des memecoins à faible liquidité, l'impact de prix varie
énormément d'un token à l'autre à taille égale — mesuré sur 16 tokens à 20 $
de position :

```
TOM         liq 57 000 $   aller-retour   2,32 %
PWEACEJIMO  liq 22 074 $   aller-retour  23,62 %
Willie      liq 13 716 $   aller-retour   2,11 %
Buddy       liq 26 261 $   aller-retour   1,85 %
```

Deux tokens à liquidité comparable (PWEACEJIMO et Willie) donnent des coûts
séparés d'un facteur 10. Aucune heuristique fondée sur la liquidité affichée
ne peut deviner ça — il faut un devis.

## Décision

`JupiterAPI.round_trip_cost_pct()` (`src/apis/jupiter.py`) mesure le slippage
par **deux devis réels, aucune transaction envoyée** :

1. Un devis ACHAT : `SOL -> token`, pour le montant en lamports équivalent à
   `size_usd`.
2. Si le premier devis répond, un devis VENTE : `token -> SOL`, pour
   `buy.out_amount` (le montant de tokens réellement obtenus au premier
   devis, pas une approximation).

Le coût de chaque jambe est `abs(price_impact_pct)` — le champ d'impact de
prix retourné par Jupiter lui-même sur ce devis, pas une distance calculée
localement entre un prix avant/après. Le coût aller-retour est la somme des
deux : `buy.total_cost_pct + sell.total_cost_pct`.

Si l'un des deux devis échoue (pas de route, token illiquide, timeout), la
fonction retourne `None` plutôt que d'extrapoler depuis le devis disponible.

Le même mécanisme sert à la détection de honeypot (`sellable()`) : si le
devis de vente n'a aucune route ou coûte un multiple du devis d'achat, le
token est un piège — c'est un sous-produit du même appel, pas un test séparé.

## Conséquences

- Le slippage mesuré dépend de l'état du carnet **au moment de la mesure**,
  pas d'un chiffre figé. Un même token peut coûter 2 % un instant et 20 % le
  suivant si la liquidité bouge.
- Deux appels Jupiter par mesure de coût de sortie (ADR 009), sur un budget
  de 1 req/s — un des goulots d'étranglement documentés du pipeline.
- La distribution mesurée sur 16 tokens (médiane 3,06 %, p25 2,25 %,
  p90 3,74 %) sert de repère pour le veto économique et le dimensionnement de
  position, mais chaque décision individuelle repose sur un devis frais, pas
  sur cette médiane.
- `round_trip_cost_pct` reste utilisé à l'entrée (filtre économique,
  dimensionnement) et à la sortie (ADR 009) — même fonction, mêmes deux
  devis, deux points d'appel différents du cycle de vie d'une position.
