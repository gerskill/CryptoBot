# 003. Séparer le score d'entrée du score de classement

Date : 2026-07-29
Statut : accepté

## Contexte

Chaque sous-score mélangeait 60% d'échelle absolue et 40% de rang dans le lot
courant. Le score composite servait à la fois à **trier** les candidats et à
**décider** de l'entrée (seuil à 75).

Défaut structurel : sur un lot médiocre, le meilleur candidat décroche 100/100
sur tous les composants relatifs et franchit le seuil **mécaniquement**. Une
nuit creuse aurait produit des entrées sur les moins mauvais déchets
disponibles, sans qu'aucun d'eux ne vaille quoi que ce soit dans l'absolu.

Vérifié en live : KATE sortait à 62.9 en classement mais 75.8 en absolu ;
Faucate à 68.2 / 76.1. Les deux nombres divergent de plus de 10 points.

## Décision

Deux scores distincts sur `Candidate` :

- `alpha_score_absolute` — seuils de saturation fixes uniquement.
  **Seul autorisé à décider d'une entrée.**
- `alpha_score` — 60% absolu + 40% rang. **Sert au tri.**

`main.py::_try_entries` compare `alpha_score_absolute` au seuil. Le message de
log affiche les deux, pour qu'un écart anormal saute aux yeux.

La sûreté RugCheck n'est jamais normalisée par lot : un token sûr l'est dans
l'absolu, pas relativement à ses voisins de scan.

## Conséquences

- Un candidat peut franchir la porte tout en étant 3ᵉ du classement. C'est le
  comportement voulu.
- Le test `test_score_absolu_stable_quel_que_soit_le_lot` verrouille la
  propriété : ajouter des concurrents ne doit pas changer le score absolu.
- Le seuil de 75 devient interprétable dans le temps — un score de 80
  aujourd'hui vaut un score de 80 la semaine prochaine.
