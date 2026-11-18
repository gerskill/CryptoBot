# 020. Positions sans prix fermées à -100 %

Date : 2026-11-18
Statut : accepté

## Contexte

Quand aucune source (DexScreener, Jupiter, Birdeye) ne donnait de prix
exploitable, `_monitor_arm` sautait la position **avant** le time stop et la
détection de rug, et `/STOP` la laissait ouverte. Sans jambe finale, elle
sortait de tous les chiffres (`read_positions()`). Documenté dans le code :
7 positions bloquées ainsi, dont une réellement à -99 %. Les tokens morts sont
justement ceux qui perdent leur prix : les pires pertes étaient invisibles
(audit 2026-11-17, constat B1).

## Décision (Killian, 2026-11-18)

- Prix inutilisable **et** âge ≥ time stop + `ZOMBIE_GRACE_MINUTES` (30) :
  fermeture à -100 % de la part restante, raison
  `ZOMBIE (sans prix depuis le time stop)`, via
  `PaperPortfolio.close_worthless` (pas de devis : token invendable).
  Avec un prix, le time stop l'aurait déjà fermée ; la règle ne dépend que de
  `entry_time` et survit aux redémarrages.
- `/STOP` refait une recherche de prix puis, à défaut, ferme à -100 %
  (`PANIC_STOP (sans prix)`).
- Le plancher de relecture du journal (`correct_legacy_cost`) devient
  -100 % − frais d'entrée quand un frais d'entrée est enregistré : sinon le
  capital reconstruit au redémarrage dépassait le capital réel (900 $ au lieu
  de 897 $ dans le cas testé).

## Conséquences

- Au premier cycle après déploiement, les zombies existants sont fermés :
  baisse brute et voulue des chiffres des bras concernés.
- -100 % est le pire cas : un token revenu plus tard serait compté trop bas.
  Choisi parce qu'il ne flatte jamais un bras.
- `ZOMBIE` est une sortie automatique (`RAISONS_AUTOMATIQUES`).
- Verrouillé par `tests/test_positions_zombies.py`.
