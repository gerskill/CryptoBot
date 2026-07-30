# 004. Le rug pull est évalué avant toutes les autres sorties

Date : 2026-07-29
Statut : accepté

## Contexte

La spécification d'origine liste 7 règles de sortie « dans l'ordre de
priorité », avec la détection de rug pull en **7ᵉ** position — après le stop
loss, les trois take-profits, le trailing et le time stop.

Un rug pull vide la liquidité en quelques secondes. Évaluer les take-profits
d'abord signifie : constater un prix en hausse, décider de vendre au TP, et
découvrir qu'il n'y a plus de carnet en face.

Le cas pathologique est explicite : un token à +400% dont le dev vient de
retirer la liquidité affiche un prix flatteur et une valeur de sortie nulle.

## Décision

Ordre d'évaluation dans `positions.py::evaluate_exits`, écart assumé vs la
spec :

1. **Rug pull** — chute de liquidité ≥ 50% sur 2 min → sortie totale
2. Stop loss (ou breakeven après TP1)
3. Time stop
4. Trailing stop
5. Take profits (cascade possible sur un même tick)

L'écart est documenté dans la docstring du module, pas seulement ici.

## Conséquences

- La détection dépend de `PriceHistory.liquidity_drop_pct()`, donc de la
  fréquence de monitoring. À 90 s d'échantillonnage sur une fenêtre de 2 min,
  on n'a que 1 à 2 points — inexploitable. C'est ce qui a motivé l'ADR 006.
- Un faux positif (chute de liquidité temporaire due à un retrait légitime)
  sort la position au prix courant. Coût accepté : sortir à tort d'un token
  sain est réparable, rester dans un rug ne l'est pas.
- Le test `test_rug_pull_prioritaire_sur_take_profit` verrouille l'ordre.
