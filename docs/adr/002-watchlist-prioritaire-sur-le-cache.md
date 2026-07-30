# 002. La watchlist court-circuite le TTL du cache

Date : 2026-07-29
Statut : accepté

## Contexte

Le cache de 30 minutes évite de ré-interroger un token déjà scanné — il fait
tomber un cycle de 39 requêtes à 4.

Observé en live au cycle 2 : les 4 candidats qualifiés du cycle 1 étaient
sautés par le cache. Conséquence en chaîne :

1. aucun nouveau snapshot de prix n'était enregistré pour eux
2. l'historique ne se remplissait donc jamais
3. l'analyse technique restait bloquée en `PENDING` indéfiniment
4. **aucune entrée n'était possible, jamais**

Deux exigences se contredisaient : « ne pas re-scanner » et « suivre les
candidats qualifiés ».

## Décision

Introduction d'une **watchlist** dans `TokenCache`. Un token qualifié est
placé sous surveillance ; `is_fresh()` retourne toujours `False` pour lui,
quel que soit le TTL.

Le scan reçoit `always_include=watched` : les tokens surveillés sont
ré-interrogés même s'ils ont quitté `/token-profiles/latest` — ce qui arrive
en quelques minutes.

Un token surveillé sort de la watchlist s'il est rejeté, blacklisté, ou après
`MAX_WATCH_HOURS` (6 h, au-delà il quitte la fenêtre d'âge de toute façon).

L'audit sécurité, lui, reste caché 15 minutes : le prix bouge toutes les 90 s,
pas le rapport RugCheck.

## Conséquences

- Le gain du cache est préservé : mesuré à 33 sautés / 3 interrogés au cycle 3.
- La watchlist est persistée dans `token_cache.json` : un redémarrage ne perd
  pas le suivi.
- Un blacklistage retire d'office de la watchlist.
