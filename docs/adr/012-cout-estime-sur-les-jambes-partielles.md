# 012. Coût estimé sur les jambes partielles

Date : 2026-08-31
Statut : accepté

## Contexte

L'ADR 009 ne déduit le coût de sortie qu'une fois par position, sur la jambe
finale, parce qu'un devis Jupiter coûte une requête et que Jupiter plafonne à
1 req/s. La conséquence était écrite dans `CONTEXT.md` depuis le 2026-08-06 :

> Le P&L papier reste partiellement optimiste. Les jambes partielles (TP1,
> TP2) restent au prix nu.

Ce n'est pas un détail comptable. Un gagnant qui sort en TP1 + TP2 + TP3 paie
une vente sur trois. Or `live_mode_allowed()` décide du passage en réel sur le
win rate et le profit factor calculés à partir de ces lignes : le chiffre qui
autorise à risquer de l'argent réel est biaisé, et il l'est du côté flatteur.

Ordre de grandeur mesuré : l'aller-retour coûte 3,06 % en médiane sur 20 K de
liquidité. Deux ventes non facturées sur un trade à trois jambes, c'est
plusieurs points de P&L rendus invisibles par position.

## Décision

Les jambes partielles se voient déduire un coût **estimé**, jamais mesuré en
direct :

1. **Pas de devis par jambe.** L'ADR 009 tient : interroger Jupiter à chaque
   TP1 multiplierait les requêtes sur l'API la plus contrainte du dépôt.
2. **Source de l'estimation : les mesures passées du bras lui-même.** Médiane
   des `exit_cost_pct` réellement mesurés sur ses jambes finales. Par bras, et
   pas globalement : les fenêtres de liquidité des sept bras sont
   volontairement disjointes, la médiane de `sniper` (4 K) n'a rien à voir
   avec celle de `narrative` (40 K).
3. **La moitié de cette médiane.** `round_trip_cost_pct` mesure un aller-retour,
   achat plus vente. La jambe finale porte volontairement le round-trip complet
   puisque l'entrée n'est facturée nulle part ailleurs ; facturer un round-trip
   entier à chaque jambe partielle facturerait l'achat deux ou trois fois. À
   défaut d'un devis unidirectionnel — `/swap` reste hors de `ALLOWED_PATHS` —
   la moitié est le partage le moins faux.
4. **Plancher de dix mesures**, le même que `MIN_SEGMENT_SAMPLE`. En dessous,
   aucune estimation : la jambe reste au prix nu, exactement comme avant. « Pas
   assez de données pour estimer » n'est pas « coût nul ».
5. **La ligne de journal le déclare** : `exit_cost_estimated: true`. Les
   estimations sont exclues du calcul de l'estimation suivante, sinon elle
   converge sur elle-même.

## Conséquences

**Ce que ça coûte.** Les P&L des trades à sorties multiples baissent, y compris
rétroactivement dans les comparaisons entre une période d'avant et d'après —
les lignes anciennes n'ont pas été réécrites, elles restent au prix nu. Toute
analyse qui compare deux périodes doit filtrer sur `exit_cost_estimated` ou
accepter cette rupture, et la dire.

**Ce que ça empêche.** Un bras ne peut plus atteindre `live_mode_allowed()`
grâce à des ventes partielles gratuites. C'est le but.

**Ce qu'il faudra revoir.** Le partage 50/50 entre achat et vente est une
hypothèse, pas une mesure. Le jour où un devis unidirectionnel devient
accessible, `SELL_LEG_SHARE` disparaît au profit d'une mesure réelle. Le
plancher de dix reste à confirmer quand les bras auront un historique plus
épais : dix coûts mesurés sur un bras dont la liquidité a changé de régime
décrivent un marché qui n'existe plus.

**Ce qui n'a pas changé.** Sans estimateur injecté, `PaperPortfolio` se comporte
exactement comme avant. Aucune migration de données, aucune réécriture du
journal.
