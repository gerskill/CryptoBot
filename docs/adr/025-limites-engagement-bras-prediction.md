# ADR 025 — Limites d'engagement des bras de prédiction (25 % total, 10 % au-delà de 24 h)

- Date : 2026-11-22
- Statut : accepté (Killian, 22/11/2026 14:12 et 14:15, heure de Zurich)
- Complète : ADR 024 (saison 3 à 500 $, valorisation au marché)

## Contexte

Au premier cycle de la saison 3, rien ne limitait le **total** misé par un bras :
90 paris ouverts d'un coup, `pm_temoin` à 497 $ engagés sur 500 $. Killian a aussi
demandé de tenir compte de la **durée** des paris : un pari de plusieurs jours
immobilise le compte bien plus longtemps qu'un pari d'une heure.

## Décision

Engagé = mises + frais d'achat des paris **ouverts** du bras.

Base (la plus prudente) = minimum de :
1. capital de départ de la saison (`capital_fictif_usd`, 500 $) ;
2. capital + résultat réglé ;
3. équité au marché du cycle précédent (`marks.json`), si connue.

Les gains ne remontent donc jamais la base au-dessus de 500 $ ; les pertes, réglées
ou latentes, la baissent.

- `engagement_total_max_pct` = 0,25 : engagé ≤ 25 % de la base (125 $ à 500 $).
  Refus : `engagement_total_max`.
- `engagement_long_max_pct` = 0,10, `engagement_long_heures` = 24 : l'engagé sur les
  paris dont le règlement tombe plus de 24 h après l'entrée ≤ 10 % de la base (50 $).
  Refus : `engagement_long_max`. Les paris de 24 h ou moins ne comptent que dans
  les 25 %. **Échéance inconnue = long** (prudent), pour le marché au moment de
  l'entrée comme pour un pari déjà ouvert sans `expiry` / `entry_time`.
- Contrôle en deux temps : avant la lecture du carnet (avec la mise visée, évite des
  lectures inutiles) puis avec le coût réel après taille minimale.
- Les paris déjà ouverts au-delà ne sont **jamais fermés** : ils vont à leur
  règlement, le bras reprend une fois repassé sous la limite.
- Rien d'autre ne change : plafond 10 % par pari, taille minimale Hyperliquid,
  50 % par domaine.

## Conséquences

- `/api/prediction/summary` : chaque bras a un bloc `engagement` (base, engagé,
  limite, disponible, dépassé, et les mêmes champs `_long`).
- Tableau de bord : ligne « engagé X / 125 $ · > 24 h Y / 50 $ », en rouge avec
  « en attente » quand le bras est bloqué.
- Les bras qui ont tout engagé au cycle 1 de la saison 3 restent à l'arrêt tant que
  leurs paris ne sont pas réglés : c'est voulu.
- Sans ces réglages dans la config, le comportement est celui d'avant (tests).
