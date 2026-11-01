# Recalibration du score d'entrée — 2026-10-10

Analyse hors ligne uniquement : le bot, le code et la config de production n'ont **pas** été modifiés.
Méthode : docs/PROMPT_QUANT_CRYPTOBBOT.md (questions isolées, split temporel, calibration, portes chiffrées).
Scripts et sortie brute : `proposals/score_v2/analysis/analysis.tgz` (results.txt).

## 1. Échantillon
- 4 338 lignes de journal (PAPER), 11 bras → **3 384 positions clôturées** (sorties partielles agrégées par `position_id`).
- Plusieurs bras prennent le même token : regroupement token + entrée à moins de 60 min → **1 515 entrées uniques** (position la plus ancienne retenue).
- Split temporel : **train** 06.08 → 24.09 18:47 (909 entrées), **test** 24.09 18:47 → 10.10 10:47 (606 entrées). Heures de Zurich.
- Cibles : (a) `hit10` = a touché +10 % (≈ 50 % des entrées), (b) `win` = PnL net > 0 (≈ 20–21 %).

## 2. Le score actuel
`src/core/scoring.py`, poids `params.json > scoring_weights`, renormalisés sur les composantes présentes :
liquidité 0,20 · volume/momentum 0,25 (= 50 % ratio volume 1h/liquidité, 30 % variation 5 min, 20 % pression acheteuse) ·
social 0,20 · smart money 0,12 · rugcheck 0,15 · fiabilité wallets 0,08. La porte d'entrée est le score **absolu** ≥ 55 (baseline ; 55–70 selon les bras).

Calibration (entrées uniques) :

| Score absolu | n | hit10 | WR | PnL moyen |
|---|---|---|---|---|
| 55–60 | 36 | 47 % | 28 % | −6,4 % |
| 60–65 | 45 | 53 % | 27 % | −3,8 % |
| 65–70 | 185 | 51 % | 23 % | −12,5 % |
| 70–75 | 464 | 55 % | 24 % | −9,2 % |
| 75–80 | 236 | 50 % | 18 % | −16,0 % |
| 80–85 | 136 | 51 % | 19 % | −12,3 % |
| 85–90 | 193 | 45 % | 20 % | −12,5 % |
| 90+ | 101 | 50 % | 15 % | −20,7 % |

- AUC test du score absolu : **0,41** pour `win`, 0,46 pour `hit10` → **anti-prédictif** (plus haut = pire).
- Lu comme une probabilité (score/100), Brier `win` 0,48 contre 0,17 pour le simple taux de base : ce n'est pas une probabilité.

**Pourquoi il est anti-prédictif.** Il récompense ce qui vient de pomper :
- variation 5 min : AUC test `win` 0,39 (donc 0,61 dans l'autre sens) ; > +40 % → WR 15–16 %, < 0 → WR 26–29 % ;
- ratio volume/liquidité : AUC test 0,45 (0,56 inversé) ;
- le score monte avec ces deux variables (corrélation de rang +0,24 et +0,28).
Les autres composantes n'aident pas : rugcheck est à 99 presque partout (AUC 0,49), la liquidité est neutre en train (0,50) et faiblement positive en test (0,57).
Social, smart money et wallets **ne sont pas journalisés par trade** (`social_score` toujours vide) : impossible de les tester.

## 3. Ce qui prédit, ou pas (AUC train → test, entrées uniques)
| Variable connue à l'entrée | hit10 | win |
|---|---|---|
| variation 5 min (inversée) | 0,53 → 0,55 | 0,55 → **0,61** |
| volume/liquidité (inversé) | 0,47 → 0,53 | 0,52 → 0,56 |
| liquidité | 0,51 → 0,55 | 0,50 → 0,57 |
| holders | 0,53 → 0,54 | 0,49 → 0,56 |
| âge | 0,45 → 0,54 | 0,51 → 0,56 |
| volume 1h | 0,53 → 0,50 | 0,48 → 0,48 |
| rugcheck | 0,50 → 0,50 | 0,50 → 0,49 |
| heure | 0,51 → 0,48 | 0,50 → 0,43 |

- **`hit10` : rien ne le prédit** (max 0,55 en test ; régression 5 variables : 0,595 train → 0,535 test). Cela confirme Killingbot.
- **`win` : un seul signal stable, « ne pas acheter le pump »** (momentum 5 min et volume/liquidité). Il est faible.
- **Piège évité, fuite de données.** `microstructure_log` (profondeur, dérive prix/liquidité, impulse) et `exit_cost_pct` sont écrits **à la sortie** (médiane 18 min après l'entrée). Leur AUC de 0,73–0,84 est du look-ahead : ces variables sont exclues. Attention, le filtre « frais de sortie » de la simulation Killingbot (PF 1,53) repose peut-être sur ce champ.
- **Seule vraie mesure de coût à l'entrée** (porte `economie` de l'entonnoir, depuis le 05.10) : 138 entrées uniques, AUC `win` 0,63. Aller-retour ≤ 2,6 % → PnL moyen −6 à −7 %, au-delà de 2,6 % → −21 à −27 %. C'est prometteur, mais trop court pour un split train/test.

## 4. Score v2 proposé
Régression logistique à 2 variables, poids explicites, ajustée sur le train seulement :

```
z = −1,2065 − 0,004636 × clip(variation_5m %, −50, 100) − 0,115594 × log10(max(volume_1h/liquidité, 0,01))
p = 1 / (1 + e^−z)        entrée si p ≥ 0,205 (médiane du train)
```
Lecture : avec volume = liquidité, on refuse au-delà de +32 % sur 5 min. Avec volume = 10× la liquidité, on refuse au-delà de +7 %.

Hors échantillon (test) :
- AUC `win` **0,62** (IC 95 % bootstrap 0,57–0,68) ; train 0,545 ; `hit10` 0,545.
- Brier 0,1656 contre 0,1677 (taux de base), 0,1633 après isotonic. Les probabilités vont seulement de 0,14 à 0,27 : le classement est utile, mais **la calibration est insuffisante pour la taille**.
- Fiabilité test par quintile : prédit 0,18 / 0,21 / 0,22 / 0,22 / 0,24 → observé 0,15 / 0,16 / 0,17 / 0,26 / 0,34.

PF simulé sur les positions réelles de la période test, en ne gardant que celles au-dessus du seuil :

| Période test | Tout | p ≥ 0,205 (« top 50 ») | p ≥ 0,215 (« top 30 ») | Refusées |
|---|---|---|---|---|
| baseline | 348 pos · PF 0,77 · −937 $ | 227 · PF **0,98** · −49 $ (t −0,33) | 170 · PF 0,97 · −59 $ | 121 · PF 0,48 · −888 $ |
| tous bras | 1 636 · PF 0,62 · −6 675 $ | 1 225 · PF 0,70 · −3 763 $ | 900 · PF 0,69 · −2 882 $ | 411 · PF 0,44 · −2 911 $ |

- Stabilité baseline : 1re moitié du test PF 0,81 → 0,90 ; 2e moitié 0,72 → 1,06. Sur le train : 0,32 → 0,45.
- Les seuils viennent du train : en test, la période est moins « pump », donc 74 % des entrées passent au lieu de 50 %.
- Règle encore plus simple, « variation 5 min < 0 » : baseline test PF 0,91 (120 pos).

## 5. Limites
- Le signal réduit les pertes, il **ne crée pas d'edge** : aucune portée ne passe les portes (PF > 1,3, t > 2). Le meilleur résultat (PF 0,98 baseline) est indiscernable de 1.
- AUC train (0,545) plus faible que test (0,62) : signal instable dans le temps, régime plus calme en test.
- Pas de données smart money, social ou carnet à l'entrée dans les journaux. Le PnL dépend des sorties de chaque bras, d'où le bruit.
- Le PnL des bras est rejoué tel qu'il a été obtenu, sans re-simuler les positions que le filtre aurait libérées.

## 6. Recommandation
1. **Ne pas** remplacer le score de baseline et **ne pas** utiliser de score pour la taille (Kelly) : la calibration ne le permet pas.
2. **OK pour un bras de test PAPER `score_v2`**, prêt mais non déployé : `proposals/score_v2/`. C'est un fork exact du témoin + un filtre anti-pump, avec role test, learning false et capital 0. Coût API nul. Verdict après ≥ 200 trades, soit environ 2 semaines au rythme du témoin. Objectif réaliste : PF nettement meilleur que baseline, pas PF > 1 seul.
3. Journaliser le **coût aller-retour à l'entrée** (`round_trip_pct` de la porte `economie`) et les sous-scores (`sub_scores`, smart money, buy/sell 5m) dans `trades_log.jsonl`. C'est le signal le plus prometteur (AUC 0,63 sur 5 jours), et il manque de l'historique pour le valider.
4. Plus tard seulement, si `score_v2` et `scaled_be` tiennent chacun leurs promesses : tester leur combinaison.
