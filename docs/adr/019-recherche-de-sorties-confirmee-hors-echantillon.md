# 019. Recherche de sorties confirmée hors échantillon

Date : 2026-11-18
Statut : accepté

## Contexte

`_search_exits` essayait 36 couples (stop, TP1) sur jusqu'à 100 positions
instrumentées, gardait le meilleur et le validait **sur ces mêmes positions** :
un choix qui ne pouvait pas échouer et ne prédisait rien (fuite n°1 du skill
`chasse-au-lookahead`). Mesure dans l'historique des ajustements
(`config/arms/*.json`, 2026-11-17) : ces recherches ont porté sur 40 positions
en médiane (15 à 100) et chaque bras qui apprend en a appliqué 25 à 56.
L'audit d'architecture (`docs/audits/2026-11-17-audit_architecture.md`, I10)
a trouvé cinq fuites de plus : couple appliqué ≠ couple évalué (pas maximal),
validation dans l'échantillon capable d'annuler le choix, reste au breakeven
mesuré sur tout le journal, doublons entre bras des deux côtés de la coupe,
seuil relatif à une référence de quelques centimes.

## Décision

- Les positions sont dédoublonnées (même token à ±300 s = même événement,
  la copie propre au bras gardée), triées par clôture, coupées 2/3 – 1/3
  (`chronological_split`, `HOLDOUT_TRAIN_FRACTION`).
- Le couple est choisi sur les 2/3 anciens, mesures bornées à la fin de
  cette tranche (`until`).
- Il n'est appliqué que si, sur le tiers récent, les **valeurs réellement
  appliquées** (`_planned_value`) changent le P&L d'au moins
  `EXIT_BACKTEST_MIN_CHANGED` (5) positions et que la borne basse de
  l'intervalle `bootstrap_mean` des écarts appariés est > 0.
- `run()` valide `_adjust_exits` dans l'échantillon **avant** la recherche,
  qui ne peut donc plus être défaite.
- Les filtres ne sont pas verrouillés : leur message porte une mesure
  « tiers récent », qui n'est **pas** une preuve hors échantillon
  (`_adjust_filters` décide sur toutes les positions).

Six issues distinctes dans `last_exit_search` : pas assez de données,
dégradé, rien à ajuster, indéterminé, rejeté hors échantillon, confirmé.

## Conséquences

- Les sorties bougeront moins souvent. Surveiller la part d'« indéterminé »
  par bras sur le Mac : si elle domine, agrandir la fenêtre de test, ne pas
  baisser le seuil de 5.
- Restent hors périmètre : ordre pic/creux deviné (I6), emprunts dominés par
  `consensus` (I5), verrou des filtres (I1), mélange de régimes (B4) ;
  `scripts/audit.py` mesure encore le reste au breakeven sur tout le journal.
- Verrouillé par `tests/test_hors_echantillon.py`, dont deux tests qui
  échouent si l'on retire `until=` ou si l'on inverse l'ordre de `run()`.
