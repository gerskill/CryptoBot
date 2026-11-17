---
name: rentabilite-annuelle
description: Dire si un bras de CryptobBot est rentable « sur l'année » — rendement annualisé, objectif de +X %/an (120 %, 100 %, doubler), « lesquels garder », « lesquels supprimer », projection d'un P&L de quelques jours sur un an, comparaison des bras par rendement. Utilise ce skill dès qu'un pourcentage annuel, un seuil de rentabilité par an ou une suppression de bras fondée sur le rendement est en jeu — surtout quand on te demande d'être « décisif » ou « sans réserves », parce que c'est exactement la pression sous laquelle un rendement de 9 jours devient un « passe » annuel.
---

# Rentabilité annuelle d'un bras

**Principe.** « Ce bras fait-il +120 %/an ? » est une affirmation sur l'avenir.
Sur les échantillons de ce dépôt (quelques dizaines de trades, quelques jours
ou semaines par bras), la réponse honnête est le plus souvent **« indéterminé »**
— et c'est **la** réponse décisive : elle dit quoi faire (continuer à mesurer)
et quoi ne pas faire (supprimer, miser).

## Règles qui ne se négocient pas

1. **Base = 7 000 $ par bras** (`capital_total` de `config/params.json`) : en
   PAPER chaque bras reçoit la mise entière, `capital_pct` n'est pas appliqué
   (`src/core/arm.py`). Jamais le capital engagé (~1 % par position, 2 à 3
   positions : rapporter le P&L à ~140 $ multiplie le rendement par ~50).
   Le défaut `PAPER_DEFAULT_CAPITAL = 1000` ne sert que si `capital_total` vaut 0.
2. **Pas d'annualisation sous 30 jours calendaires ET 30 positions.** En
   dessous, la ligne du bras dit « non annualisable » — pas de pourcentage
   annuel, même « à titre indicatif », même suivi d'une réserve.
3. **La période est calendaire** : du début de la fenêtre à aujourd'hui, jours
   sans trade compris. Jamais « du premier au dernier trade ».
4. **Le verdict vient d'un intervalle, pas d'un point.** Trois états seulement :
   - `≥ 120 %/an établi` : la borne BASSE de l'intervalle annualisé ≥ 120 % ;
   - `< 120 %/an établi` : la borne HAUTE < 120 % ;
   - `indéterminé` : sinon.
5. **Plusieurs bras jugés ensemble = un seul gagnant ne prouve rien.** Dire
   combien de bras ont été regardés. Avec 19 bras, un « ≥ 120 % établi » isolé
   exige que sa borne basse tienne aussi sans le meilleur jour du bras.
6. **Supprimer ou désactiver un bras sur son rendement : jamais.** ADR 011 :
   seulement sur verdict **concluant et défavorable** contre le témoin
   (`verdict_vs_reference` : IC95 **disjoints** de ceux du témoin, `compare()`).
   L'ADR ne fixe **aucun nombre de trades** : ne pas en inventer un, citer la
   disjonction. Puis `enabled: false` avec `disabled_reason`,
   jamais une suppression. Le témoin (`baseline`) ne se retire pas : toutes
   les comparaisons s'y réfèrent.
7. **Le juge avant tout chiffre annoncé** (`.claude/agents/juge.md`) : drawdown
   max, moyenne des 5 % pires positions, coûts avec stress d'au moins 1 % de
   slippage. Sans verdict VALIDÉ, chaque chiffre porte `[À TESTER]`.

## Calcul

```python
from src.core.stats import window_performance, bootstrap_mean
# positions = journal.read_positions() triées par timestamp_exit
perf = window_performance(positions, 7000.0, since=debut_fenetre)
# P&L par JOUR CALENDAIRE (0 pour un jour sans clôture), puis :
ic = bootstrap_mean(pnl_par_jour)            # Interval(value, low, high, n)
annuel = lambda x: 100 * x * 365 / 7000.0    # simple, en %/an
# verdict sur annuel(ic.low), annuel(ic.high) — règle 4
```

Le composé `(E1/E0)^(365/D) − 1` peut s'afficher À CÔTÉ du simple quand
D ≥ 180 jours ; avant, il amplifie le bruit (9 jours à +5,9 % donnent +900 %).

## À vérifier avant de publier une ligne

| piège | vérification |
|---|---|
| compteurs `learning` des fichiers de bras (`total_trades`…) | ce ne sont pas des performances ; `sniper_ar` porte 1 425 trades **hérités** de `sniper` (créé par copie le 2026-11-02) |
| paramètres qui ont bougé dans la fenêtre | compter les entrées de `parameter_adjustment_history` dans la fenêtre : si > 0, le journal mélange plusieurs stratégies — le dire dans la ligne |
| positions encore ouvertes | `window_performance` ne les compte pas : donner leur nombre et le pire cas au stop |
| garde-fous coupés en PAPER | `cooldown_hours = 0`, `max_drawdown_stop_pct = 0` : le drawdown papier n'est pas celui d'un bot réel protégé |
| P&L | `read_positions()` (TP1/TP2 recollés), jamais `read_final_exits()` pour sommer |

## Forme de la réponse

Un tableau, une ligne par bras, ces colonnes dans cet ordre :
`bras | fenêtre (jours) | positions | P&L $ | rendement fenêtre % (sur 7 000 $) | annualisé simple [IC95] ou « non annualisable » | ajustements dans la fenêtre | verdict (règle 4)`.
Puis une ligne « Bras jugés : N ». Puis « Suppression : aucune — ADR 011 » sauf
verdict concluant et défavorable contre le témoin. Puis le verdict du juge.

## Excuses entendues, et la réalité

| excuse | réalité |
|---|---|
| « Il veut que je sois décisif, sans réserves » | « indéterminé, mesurer jusqu'au JJ/MM » EST décisif. Un « PASSE » sur 9 jours est une erreur décisive. |
| « Je donne l'annualisé et je préviens à la fin » | La réserve en bas de page n'annule pas le « PASSE » en haut. Sous 30 jours : pas de pourcentage annuel du tout. |
| « +5,9 % en 9 jours, c'est énorme » | Une position fait ~70 $ (1 % du capital) : une seule à +200 % rapporte 140 $, soit 2 % de 7 000 $. Deux ou trois tirages chanceux font le « +5,9 % ». |
| « Ce bras perd, je le supprime » | ADR 011. Et un bras désactivé reste sur disque. |
| « Le fichier dit 1 425 trades » | Compteur d'apprentissage, éventuellement hérité. Seul le journal fait foi. |
| « Rapporté au capital engagé c'est le vrai rendement » | Le capital tourne : même 140 $ servent des dizaines de fois. La base est 7 000 $. |

## Signaux d'arrêt

Tu es sur le point d'écrire un pourcentage annuel sur moins de 30 jours ; le
mot « supprimer » à côté d'un rendement ; un chiffre sans `[À TESTER]` ni
verdict du juge ; « le meilleur des 19 » sans dire qu'on en a regardé 19.
→ Reprendre les règles 2, 6, 7, 5.
