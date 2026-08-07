# 011. Un bras n'est désactivé que sur un verdict statistiquement concluant, jamais sur une sous-performance apparente

Date : 2026-08-03
Statut : accepté

## Contexte

Sept bras tournent en parallèle, chacun avec son propre P&L. Regarder sept
séries et retirer la moins bonne produit un gagnant par hasard — mesuré le
2026-08-02 : `runner` affichait +6,72 $/trade sur cinq trades, un chiffre trop
instable pour distinguer une bonne stratégie d'une bonne semaine.

Le 2026-08-03, `verdict_vs_reference` (`src/core/stats.py`) a pu trancher sur
`narrative` : IC95 du P&L/trade **entièrement négatif**, `[-11,54 .. -7,56]`
contre le témoin, sur 4 trades WR 0 % PF 0,00, -8,94 $/trade. C'est le seul
bras sur lequel les données autorisaient une décision — les six autres
recouvrent l'intervalle du témoin (indistinguables), y compris `runner`
malgré son chiffre le plus haut.

## Décision

Un bras n'est désactivé que si `verdict_vs_reference` rend un verdict
**concluant et défavorable** — IC95 disjoint de celui du témoin, entièrement
du mauvais côté — pas sur une sous-performance simple, un P&L négatif brut,
ou un rang bas dans le classement. `compare()` exige des IC95 disjoints
(seuil ~0,005 par comparaison) ; sur six bras comparés au témoin, le risque
d'au moins un faux positif reste sous 3 % sans machinerie de correction
supplémentaire.

La désactivation se fait via `config/strategies.json` : `enabled: false` sur
l'entrée du bras, plus un champ `disabled_reason` qui documente le verdict
chiffré qui a motivé la décision (commit `216a75f`, sur `narrative`).
`load_manifest()` + `bootstrap_arms()` filtrent les entrées désactivées
(`e.get("enabled", True)`) : le bras n'est pas instancié, ne trade plus, mais
son fichier de paramètres et son journal restent sur disque — rien n'est
supprimé.

`capital_pct` des bras restants est redistribué pour que la somme reste 1,0 :
`bootstrap_arms()` refuse de démarrer sinon (`ManifestError` — « du capital
inventé fausserait toute comparaison entre bras »).

Avant qu'un verdict soit possible, un bras qui n'entre jamais peut être
débloqué par `_relax_from_inactivity` (300 cycles sans aucune entrée desserre
son seuil dominant) — c'est ce mécanisme qui a débloqué `narrative` la nuit
précédant sa désactivation : il a pris 4 trades, qui ont tous perdu, ce qui a
produit la mesure permettant de trancher. La désactivation n'intervient
qu'après que le bras a eu sa chance de produire un échantillon.

## Conséquences

- Six bras sur sept restent actifs bien qu'aucun ne soit statistiquement
  meilleur que le témoin — l'absence de preuve de supériorité n'est pas une
  preuve d'infériorité, et seule cette dernière justifie l'arrêt.
- Le seul autre mécanisme qui retire du capital à un bras est la désactivation
  manuelle par ce chemin : il n'existe pas de désactivation automatique
  déclenchée par le code lui-même. Le verdict est calculé automatiquement,
  la décision d'appliquer `enabled: false` est humaine.
- Rien dans le dépôt ne réactive un bras désactivé automatiquement même si un
  regain de performance survenait a posteriori sur son échantillon existant
  (il n'en produit plus, étant arrêté) — la réactivation serait, comme la
  désactivation, une décision de propriétaire.
- `MIN_TRADES_FOR_WEIGHTS` (50) et `MIN_SIGNALS_PER_AGENT` (20) protègent des
  décisions analogues sur la pondération des agents de score, avec le même
  principe : pas d'action tant que l'échantillon ne permet pas de trancher.
