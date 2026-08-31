---
name: chasse-au-lookahead
description: Traquer le regard en avant, le biais de survie et la fuite d'information dans tout ce qui rejoue l'historique de CryptobBot — simulate_exits, exit_grid, simulate_filters, walk_forward, _instrumented, les scripts d'analyse. Utilise ce skill dès qu'un backtest, un rejeu, une simulation ou une recalibration annonce un gain, dès qu'un seuil est choisi sur des données passées, et systématiquement avant d'accepter une amélioration mesurée sur l'historique — un backtest qui s'améliore est plus souvent une fuite d'information qu'un progrès, et c'est la seule classe de bug que les garde-fous chiffrés du dépôt n'attrapent pas.
---

# Chasse au lookahead

Les garde-fous du dépôt protègent contre le **manque d'échantillon** :
`MIN_SEGMENT_SAMPLE`, `MIN_TRADES_PER_ARM`, `PARAM_BOUNDS`, IC95 disjoints.
Aucun ne protège contre une **fuite d'information** : un backtest qui connaît
l'avenir a l'air excellent sur un gros échantillon comme sur un petit. C'est
l'angle mort structurel de `learning.py`.

La question à poser à chaque rejeu : **au moment que je simule, cette
information existait-elle ?**

## Les six fuites, par ordre de fréquence dans ce dépôt

**1. Le seuil réglé et jugé sur les mêmes trades.** `simulate_exits` et
`exit_grid` cherchent le meilleur couple (stop, take profit) *sur les données
qu'ils évaluent*. Trouver le meilleur réglage a posteriori est trivial et ne
prédit rien. Le dépôt le sait — c'est exactement ce que `walk_forward` et
`overfit_gap` existent pour mesurer. La faute n'est pas de simuler, c'est de
**conclure sans avoir regardé l'écart apprentissage/test**.

**2. Une grandeur d'après l'entrée utilisée comme si elle était connue à
l'entrée.** `peak_pct`, `trough_pct`, `minutes_to_peak`, `final_leg_pnl_pct`
n'existent qu'une fois la position fermée. Les lire pour décider d'une entrée,
d'un filtre ou d'un score est le lookahead pur. Ils sont légitimes pour juger
une **règle de sortie** — c'est leur raison d'être — jamais pour juger une
règle d'**entrée**.

**3. Le biais de survie.** `read_positions()` ignore les positions sans jambe
finale — donc **les positions encore ouvertes**. Un rejeu qui ne compte que les
trades clos surreprésente ceux qui se sont résolus vite. Vérifier ce que ça
change quand des positions traînent : un bras qui garde ses perdants ouverts
paraît meilleur qu'il n'est.

**4. Les lignes exclues qui reviennent.** `excluded_from_learning` et
`exit_cost_estimated` marquent des lignes qui ne doivent pas nourrir certains
calculs. Une estimation réinjectée dans le calcul de l'estimation suivante
converge sur elle-même (ADR 012). Vérifier que chaque agrégat filtre ce qu'il
doit filtrer, et que le filtre n'est pas seulement documenté.

**5. Le pool de trajectoires.** `_instrumented` emprunte des trajectoires aux
autres bras, filtrées par `_in_window`. Deux choses à vérifier à chaque
modification : que le filtre de fenêtre est toujours appliqué aux emprunts
(sans lui, un bras est jugé sur des trajectoires qu'il n'aurait jamais
achetées — un biais remplace un manque, et le biais ne se voit pas), et que
les emprunts n'incluent pas des positions postérieures à la période simulée.

**6. Les paramètres d'aujourd'hui appliqués à hier.** `params_version` existe
sur chaque ligne de journal pour ça. Rejouer un trade de la semaine dernière
avec les seuils actuels mesure une stratégie qui n'a jamais tourné.

## Procédure de revue

1. **Lister les entrées du calcul.** Pour chaque champ lu, dire à quel instant
   il devient connu : à la découverte, à l'entrée, pendant la vie de la
   position, à la clôture. Tout champ « à la clôture » utilisé dans une
   décision d'entrée est un défaut bloquant.
2. **Vérifier l'ordre chronologique.** Les tranches de `walk_forward` doivent
   être ordonnées par `timestamp_exit`. Une liste triée par P&L, par bras, ou
   simplement non triée casse la validation sans rien signaler.
3. **Chercher le filtre manquant.** `excluded_from_learning`,
   `exit_cost_estimated`, `_in_window`, `is_final_exit` : lequel manque ?
4. **Exiger l'écart apprentissage/test.** Un gain annoncé sans `overfit_gap`
   n'est pas un gain, c'est une mesure sur ce qu'on a déjà vu.
5. **Chercher le seuil qui a été essayé plusieurs fois.** Tester six valeurs de
   stop loss (`SL_GRID`) et garder la meilleure, c'est six chances de tomber
   sur du bruit. Le dépôt compense par des bornes dures et des cadences —
   vérifier qu'elles s'appliquent bien au chemin modifié.

## Comment le dire

Séparer ce qui **fuit** de ce qui est **fragile**. Une fuite invalide le
chiffre : le dire sans ménagement, avec le champ fautif et l'instant où il
devient connu. Une fragilité (petit échantillon, seuil essayé plusieurs fois)
n'invalide pas, elle exige une réserve écrite à côté du chiffre.

Et rappeler l'état que ce dépôt distingue partout : « pas de fuite trouvée »
n'est pas « le gain est réel ». Une revue qui ne trouve rien ne prouve rien
sur la taille de l'échantillon.

## Vérifier

```bash
python -m unittest tests.test_calibration tests.test_exit_replay tests.test_garde_fous
python -m scripts.audit          # IC95, walk-forward, ce qu'on peut conclure
```
