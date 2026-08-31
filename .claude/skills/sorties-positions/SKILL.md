---
name: sorties-positions
description: Modifier les règles de sortie, le suivi de position ou le coût de sortie de CryptobBot (src/core/positions.py, exit_fees.py, journal.py). Utilise ce skill dès qu'il est question de stop loss, take profit, TP1/TP2/TP3, trailing, time stop, rug pull, breakeven, slippage, frais de sortie, P&L, journal de trades ou d'une position qui « sort au mauvais moment » — y compris pour un simple ajustement de seuil, parce que les règles de sortie sont figées à l'entrée et que le P&L papier est comptabilisé d'une seule façon correcte.
---

# Sorties et positions

## Deux immuabilités

`Position` est frozen : `apply_exit()` et `update_water_marks()` retournent une
**nouvelle** instance. Et surtout : **les règles de sortie sont figées à
l'entrée**. Modifier `params.json` ou `config/arms/<nom>.json` ne change pas
rétroactivement le stop loss d'une position déjà ouverte. C'est volontaire —
sans ça, un ajustement de l'apprentissage réécrirait l'histoire des trades en
cours et rendrait tout backtest faux.

## Priorité fixe, évaluée toutes les 5 s

```
1. RUG PULL     liquidité -50% sur 120s   (RUG_LIQUIDITY_DROP_PCT)
2. STOP LOSS    + tampon de glissement
3. TIME STOP
4. TRAILING
5. TAKE PROFIT  TP1 partiel, TP2 partiel, TP3 total
```

Le rug passe **avant** le stop loss (ADR 004) : il vide la liquidité en
secondes, un stop évalué d'abord sortirait à un prix qui n'existe déjà plus.
Réordonner cette liste sans ADR est un changement de comportement majeur
déguisé en refactor.

## Sortie finale vs partielle — la correction qui a tout changé

Un trade = **une** sortie finale (`is_final_exit: true`). TP1 et TP2 vendent
des fractions et écrivent des lignes de journal intermédiaires. Compter toutes
les lignes **double** le nombre de trades et fausse le win rate : toujours
`TradeJournal.read_final_exits()`, jamais `read_all()` pour compter des trades.
`read_positions()` reconstruit une position complète à partir de ses jambes.

## Coût de sortie (ADR 009, ADR 010)

`measure_exit_cost` combine l'impact de prix Jupiter (deux devis réels, achat
et vente) et le priority fee Helius. Il est déduit du P&L **une seule fois par
position, sur la jambe finale** (`action.is_final`) : c'est le seul point où un
devis unique suffit.

Les jambes partielles ne sont plus au prix nu depuis l'ADR 012, mais leur coût
est **estimé, pas mesuré** : `PartialCostEstimator` prend la médiane des
`exit_cost_pct` réellement mesurés sur le même bras et n'en facture que la
moitié — la part vente, puisque la jambe finale porte déjà l'aller-retour
complet de la position. Sous dix mesures, aucune estimation : prix nu, comme
avant. La ligne de journal porte `exit_cost_estimated`.

Deux règles qui en découlent. **Ne jamais réinjecter une ligne estimée dans le
calcul de l'estimation** — elle convergerait sur elle-même. Et **dire la
rupture** quand on compare deux périodes : les lignes antérieures au
2026-08-31 n'ont pas été réécrites.

## Écarts mesurés à ne pas « corriger »

- **Le stop loss sort sous son seuil** : −27,2 % mesuré pour un SL réglé à
  −25 %. C'est de l'échantillonnage discret. Réduire `monitor_interval_seconds`
  réduit l'écart sans jamais l'annuler ; le tampon
  `exit_rules.stop_loss_slippage_buffer_pct` existe pour l'absorber, et
  `MIN_EFFECTIVE_STOP_LOSS_PCT = -15.0` empêche que tampon + seuil produisent
  un stop absurde.
- **Le breakeven après TP1 a coûté cher** : 3 des 4 gagnants ont rendu leur
  seconde moitié à ~−3 %. Le bras `runner` teste la suppression — avant de
  généraliser, lire son journal.
- **Le take profit est une conséquence, pas un choix** : voir
  `docs/ETAT_DU_PROJET.md` §3.5.

## Vérifier

```bash
python -m unittest tests.test_trading tests.test_exit_fees tests.test_exit_replay \
  tests.test_ladder_breakeven tests.test_journal_positions \
  tests.test_slippage_buffer_recalibration
python -m scripts.analyse_sorties --bras <nom>
```

`analyse_sorties.py` donne perdants, gagnants, atteignabilité des seuils et la
grille comparative — c'est le juge d'un changement de sortie, pas un trade
isolé.
