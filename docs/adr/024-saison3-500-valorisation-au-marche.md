# 024 — Bras de prédiction : saison 3 à 500 $, valorisation au marché, issues corrigées

Date : 2026-11-22 (installé sur accord de Killian, 13:51 heure de Zurich).

## Contexte
Saison 2 (46 $ par bras, ADR 018) : mises ≈ 22 % du compte, deux bras à sec
(pm_temoin, pm_pile_ou_face), trois bras qui n'ont jamais pu parier (favoris :
le minimum d'Hyperliquid coûtait plus que le plafond). Le tableau de bord
comptait les paris ouverts au prix payé et affichait un drawdown en % du
capital de départ (jusqu'à 168 %). Six paris étaient « comptés perdus par prudence ».

## Décision
- **Saison 3** : 500 $ fictifs par bras, mise visée 1 % (relevée au minimum de
  10 $ ≈ 2 %), plafond 10 % par pari (choix de Killian, 2026-11-22 ; 5 % proposé au départ). La saison 2 est archivée intacte dans
  `data/prediction/archive/saison2_46usd` (`scripts/nouvelle_saison_prediction.py`) ;
  ses paris ouverts y finissent (`saisons_a_terminer`), sans toucher la saison 3.
- **Valorisation au marché** (`paper.py::mark_bet`, `marks.json`) : un pari
  ouvert vaut sa revente au carnet acheteur, frais de vente 0,90 % déduits ;
  milieu si aucun acheteur (signalé), coût si aucune donnée. Le latent reste
  séparé du réalisé. Aucune règle d'entrée ou de sortie ne lit cette valeur.
- **Drawdown lisible** (`paper.py::equity_drawdown`) : baisse depuis le plus
  haut du compte, latent compris, en % du plus haut (≤ 100 %).
- **Issues corrigées** (seules les certaines appliquées à l'installation, choix de Killian) (`scripts/corrige_paris_prudence.py`) : seules les
  issues certaines sont appliquées par défaut ; les probables demandent
  `--inclure-probables`. Copie du journal gardée.

## Conséquences
- Avec la règle de taille `min_cote` (la plus sévère, non tranchée), un favori
  n'entre que jusqu'à ≈ 0,83 et `pm_quasi_certain` (0,90–0,97) reste refusé (il faudrait un plafond ≥ 18 % à 0,90).
  Plusieurs sources tierces disent `notionnel` (parts × prix ≥ 10 $) : si c'est
  confirmé, `regle_taille_min: "notionnel"` laisse entrer tous les favoris à ≈ 2 %.
- Une lecture de carnet de plus par pièce ouverte et par cycle, plafonnée par
  `valorisation_carnets_max` (60).
- 500 $ par bras n'est plus la taille du vrai compte Liquid (≈ 49,56 $) : le
  papier n'est plus « comme le réel » pour la taille, seulement pour les règles.

## Complément (2026-11-22 14:15)

Limites d'engagement total (25 %) et des paris de plus de 24 h (10 %) : voir ADR 025.
