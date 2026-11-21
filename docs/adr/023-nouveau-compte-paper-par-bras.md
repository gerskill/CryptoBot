# 023. Nouveau compte paper par bras, historique conservé

Date : 2026-11-21
Statut : accepté (Killian)

## Contexte

Chaque bras traînait tout son passé : `baseline` à -4 343 $ sur 7 000 $, plus forte
baisse 62 %, sur 1 517 positions prises sous des réglages et des bugs aujourd'hui
corrigés (signaux GMGN jetés, insiders jamais lus, zombies, entrées perdues M2).
Les chiffres affichés ne disaient plus rien de la version actuelle.

## Décision

- Un **compte paper** (`data/compte.json`, écrit par `python -m scripts.nouveau_compte
  --capital 100 --oui`) fixe une date de début et un solde **identique pour chaque
  bras : 100 $**. Il prime sur `capital_total` de `params.json`, qui n'est pas modifié.
- Solde, P&L, taux de réussite, drawdown, fenêtres de l'API, du dashboard et de
  l'app ne comptent que les positions **ouvertes depuis** le début du compte.
- **L'historique est gardé** : les journaux ne bougent pas, l'apprentissage les lit
  en entier (choix de Killian).
- Une position ouverte avant le compte finit sa vie dans l'ancien : sa sortie est
  journalisée, elle ne crédite ni ne débite le nouveau solde.
- La taille des positions suit d'elle-même (capital × `risk_per_trade`, plafond 5 %) :
  ~3 $ par position. Les coûts fixes pèsent alors plus en %, comme en réel.

## Conséquences

- Tous les bras repartent à « échantillon trop court » (B2 : 30 positions par côté)
  et le Kelly ne joue qu'après 20 nouveaux trades : quelques jours sans verdict.
- Le fichier est dans `data/` : ni versionné ni réécrit par l'apprentissage.
  L'effacer revient à l'ancien comportement.
- Tests : `tests/test_compte_paper.py`.
