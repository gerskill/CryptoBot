# CryptobBot — état du projet

> Document autoportant. Copiable dans n'importe quelle IA pour reprendre le
> travail sans contexte préalable. Chaque chiffre cité a été **mesuré**, pas
> estimé ; les sources sont indiquées.
>
> Dernière mise à jour : 2026-08-02

---

## 1. Ce que c'est

Bot de trading algorithmique de memecoins Solana, **en mode PAPER uniquement**
(aucune transaction réelle, aucun module d'exécution). Python 3.14, tests
`unittest`, dashboard React/Vite + API FastAPI en lecture seule.

Boucle : découverte → enrichissement → filtres → scoring → analyse technique →
garde économique → position simulée → monitoring 5 s → sortie → apprentissage.

**Le passage en LIVE est doublement verrouillé** : `live_mode_authorized_by_owner`
absent de `params.json` (donc `False`), plus 20 trades / WR > 40 % / PF > 1,5.

---

## 2. Résultats réels

37 positions clôturées.

| mesure | valeur | intervalle de confiance à 95 % |
|---|---|---|
| positions | 37 | — |
| win rate | 10,8 % | **[4,3 – 24,7]** |
| P&L / trade | −4,57 $ | **[−7,63 – −1,44]** |
| P&L affiché | −168,97 $ | — |
| **P&L avec frais réels** | **≈ −196 $** | médiane 3,06 % A/R |
| profit factor | 0,29 | — |

**Deux lectures obligatoires de ces intervalles :**

1. Le win rate va de **4 % à 25 %** — l'échantillon ne distingue pas une
   stratégie catastrophique d'une stratégie moyenne. Toute conclusion tirée
   du point 10,8 % est de la fausse précision.
2. L'intervalle du P&L/trade **ne contient pas zéro**. Les pertes ne sont pas
   du bruit : elles sont statistiquement démontrées.

Les 4 gagnants : WOJAKOS +32,83 $, CALLCAT +12,76 $, MySpace +11,79 $,
PIBBLE +10,94 $. Tous ont touché TP1 entre +100 % et +117 %.

### Distribution des trajectoires (26 positions instrumentées)

```
PIC atteint          CREUX atteint
+10%  : 42%          -10%  : 96%   <- le stop d'origine
+25%  : 31%          -15%  : 65%
+50%  : 23%          -20%  : 54%
+100% : 15%          -25%  : 23%
médiane +4,9%        médiane -20,9%
```

**96 % des trades touchent −10 %.** Le stop d'origine était dans le bruit.

---

## 3. Les cinq découvertes qui ont changé le projet

### 3.1 La comptabilité était fausse

`read_final_exits()` ne gardait que les lignes `is_final_exit`. Un TP1 est une
vente **partielle**, donc une ligne séparée, donc jetée. Résultat : 1 gagnant
affiché au lieu de 4, et 68 $ de profit invisibles pour les statistiques,
l'apprentissage, le backtest, le dashboard et le verrou LIVE.

Corrigé par `TradeJournal.read_positions()` qui regroupe les jambes par
`position_id`. Le P&L en pourcentage est repondéré par la fraction vendue —
+101 % sur la moitié d'une position n'est pas +101 %.

### 3.2 L'apprentissage était un no-op définitif

`exit_rules.stop_loss_pct` valait −10, collé à sa borne basse `PARAM_BOUNDS`.
La seule règle de `_adjust_exits` calcule `stop_loss + 5 = -5`, que
`_bounded_set` reclampe à −10 et qui retourne `None`. **À chaque appel, pour
toujours.** Et le backtest ne rejouait que les filtres d'entrée.

Débloqué par `simulate_exits`, qui rejoue les sorties sur `peak_pct`/`trough_pct`.

### 3.3 Jeune ET liquide n'existe pas

Mesuré sur GMGN `market trending`, chaîne sol :

```
age <= 30m + liq >= 15000  ->  0 token
age <=  1h + liq >= 15000  ->  0 token
age <=  6h + liq >= 25000  ->  1 token
age <= 24h + liq >=  5000  -> 100 (plafond)
```

Chaque stratégie doit choisir un **point** sur cette courbe. Les points sont
mutuellement exclusifs.

### 3.4 Le slippage ne suit pas la liquidité

Devis Jupiter réels, position de 20 $ :

```
TOM         liq 57 000 $   aller-retour   2,32 %
PWEACEJIMO  liq 22 074 $   aller-retour  23,62 %
Willie      liq 13 716 $   aller-retour   2,11 %
Buddy       liq 26 261 $   aller-retour   1,85 %
```

Aucune heuristique ne devine ça — il faut un devis. Distribution sur 16 tokens :
**médiane 3,06 %**, p25 2,25 %, p90 3,74 %.

### 3.5 Le take profit est une conséquence, pas un choix

Win rate nécessaire pour être rentable à 2 % de frais, contre le taux
d'atteinte réel :

| TP1 | WR nécessaire | WR observé | marge |
|---|---|---|---|
| +5 % | **impossible** | — | — |
| +10 % | 57 % | 42 % | **perdant** |
| +25 % | 19 % | 31 % | 1,6× |
| +40 % | 11 % | 23 % | 2,1× |
| +100 % | 5 % | 15 % | **3,0×** |

« Plein de petits gains » est mathématiquement mort à ces frais. Et
contre-intuitivement, **plus le TP est haut, plus la marge est confortable** :
les frais sont fixes, le WR exigé s'effondre plus vite que le taux d'atteinte.

Réserve : la colonne « observé » vient de 26 trades, la ligne +100 % de 4.

---

## 4. Architecture

### 4.1 Multi-stratégies (« bras »)

**1 collecte partagée → N évaluations en CPU pur.** Ajouter un bras coûte zéro
requête. C'est la propriété qui rend l'expérience possible à budget constant.

7 bras, chacun avec ses filtres, ses sorties, son portefeuille, son journal,
son shadow log, son `LearningEngine` et ses bornes.

Fenêtres **élargies le 2026-08-01** après mesure de l'entonnoir : chaque bras
avait un motif de rejet dominant unique, et trois d'entre eux ne retenaient
strictement rien.

| bras | âge | liq min | SL | TP1 | hold |
|---|---|---|---|---|---|
| baseline | 1,5-6h | 25 K | −10 % | +100 % | 4h |
| sniper | 1m30-**1,5h** | 4 K | −40 % | +40 % | 30 min |
| scalp | 30m-**4h** | 5 K | −25 % | +25 % | 1h |
| runner | 1-**12h** | **10 K** | −35 % | +150 % | 6h |
| quality | 4-**48h** | **20 K** | −25 % | +50 % | 4h |
| narrative | 6h-∞ | **40 K** | −30 % | +80 % | 24h |
| consensus | 1m30-24h | 4 K | −25 % | +50 % | 3h |

Effet mesuré sur l'entonnoir (candidats retenus / présentés) :

```
            avant        après
baseline     0/50      168/2300   + 1 entrée
sniper       0/50       37/2300
scalp        4/50       68/2300
runner       2/50      220/2300   + 1 entrée
consensus   24/50     1047/2300   + 2 confluences
```

`quality` a demandé un second passage : son empilement de filtres coupait
95 % de ce qui restait (`MC ≥ 300 K` faisait 20 → 4, `top10 < 15 %` faisait
3 → 1). Ramené à 150 K / 300 swaps / 20 %, il voit 6 tokens au lieu d'un.

**En PAPER chaque bras a 1000 $ indépendants**, `capital_pct` n'est pas
appliqué : découper rétroactivement faisait démarrer `baseline` à −16,46 $
(mise 150 $, journal −166 $), et un bras à 5 % prendrait des positions dix fois
plus petites, affichant un P&L moindre pour une raison d'allocation.

`baseline` est la référence pour `verdict_vs_reference` — sa série de trades
est continue depuis avant le multi-bras. **Il n'est plus gelé depuis le
2026-08-03** : il apprend comme les six autres (filtres, sorties, tampon de
glissement, relâchement sur inactivité), décision du propriétaire — l'objectif
est qu'il devienne lui-même une bonne stratégie, candidate au passage en réel,
plutôt qu'un point de comparaison figé.

### 4.2 Les six portes d'entrée

```
1. filtres du bras       (12 à 37 seuils selon le bras)
2. alpha absolu >= seuil (65 à 75)
3. confluence >= 2       (bras consensus uniquement)
4. can_open              cooldown, place, doublon
5. analyse technique     pas de dump, tendance, expansion de volume
6. garde économique      honeypot -> taille -> plancher de TP
```

**Invariant central : une donnée absente ne rejette JAMAIS.** `None` passe.

### 4.3 Sorties, priorité fixe, évaluées toutes les 5 s

```
1. RUG PULL     liquidité -50% sur 120s
2. STOP LOSS    (+ tampon de glissement)
3. TIME STOP
4. TRAILING
5. TAKE PROFIT  TP1 partiel, TP2 partiel, TP3 total
```

Le rug passe en premier : il vide la liquidité en secondes.

Après TP1 le stop passe à breakeven — **c'est ce qui a coûté cher**, 3 des
4 gagnants ont rendu leur seconde moitié à ~−3 %. `runner` teste la suppression.

---

## 5. Sources de données

| source | rôle | coût | limite |
|---|---|---|---|
| **GMGN** `market trending` | découverte principale, filtrée **serveur** : âge, liq, swaps, MC, bundler, insider, top10. Rend 30 champs en 1 appel | gratuit (CLI) | ~60/min estimé |
| **Jupiter** | prix par lot (50 mints/appel), devis, coût A/R, **détection honeypot** | gratuit | **1 req/s** |
| DexScreener | découverte secondaire, `pair_address` pour le monitoring | gratuit | 270/min effectifs |
| RugCheck | sécurité | gratuit | 60/min |
| Birdeye | holders exacts, OHLCV | gratuit | **1 req/s ← goulot** |
| Helius | repli holders | gratuit | 600/min |
| Twitter | social | gratuit | **402 — quota épuisé, module coupé** |

**Le prochain mur est Birdeye** : 23 requêtes sur un cycle observé, à 1 req/s
= 23 s d'un cycle de 90 s. Le palier Lite à 39 $/mois passe à 15 req/s.

⚠️ Le flux DexScreener `/token-profiles` est de la **promotion payante** :
liquidité médiane 1 634 $, 1 token sur 38 passait les filtres.

---

## 6. Sécurité

- `.env` gitignoré, **jamais suivi par git, absent de l'historique**
- Aucune clé en dur, aucune loggée
- `src/apis/gmgn.py` : liste blanche `ALLOWED_COMMANDS`, `swap`/`order`/
  `cooking` absents, exception levée **avant** le sous-processus
- `src/apis/jupiter.py` : liste blanche `ALLOWED_PATHS`, `/swap/v2/execute` et
  `/swap/v2/build` absents. Un devis n'engage rien
- Le CLI `jup` (installé) **peut** exécuter des swaps — outil manuel, non câblé
- Blacklist honeypot à sens unique : aucun bras ne peut débannir

---

## 6 bis. Bugs trouvés à l'audit, et corrigés

Cinq défauts réels, tous découverts en lisant les LOGS et les mesures — aucun
n'était visible dans la structure du code.

| bug | conséquence | correctif |
|---|---|---|
| **Clé Helius en clair dans les logs** | `requests` met l'URL complète — clé en query string — dans chaque message d'exception. Observé 4 fois. | `_safe_error()` masque `api-key=…`. **La clé exposée doit être régénérée.** |
| Limiteur Helius à 600/min | Rafales → 429. Aggravé depuis la mort de Birdeye : tous les holders retombent sur Helius. | 10/s, fenêtre d'une seconde |
| Birdeye : 400 non géré | 489 requêtes brûlées sur un quota mort, 3 essais + backoff chacune, ~23 s par cycle | détection du 400 « compute unit » → coupure |
| `Interval.conclusive` à 30 points | déclarait « concluant » un IC de 20,9 points couvrant catastrophe et succès | seuil à 12 points |
| 425 lignes de code mort | `scoreboard.py` et `signals.py` testés, zéro appelant | tous deux câblés (justesse des bras, quorum du consensus) |

## 6 ter. Audit du 2026-08-02 — le contrepoids était mort

Quatre défauts, tous de la même famille que ceux ci-dessus : du code câblé,
testé, et jamais atteint. Réunis, ils désactivaient **entièrement**
`_relax_from_shadow` — le seul mécanisme qui relâche un filtre trop strict,
donc la seule sortie de la boucle « peu de trades → filtres resserrés → moins
de flux → encore moins de trades ».

| défaut | conséquence | correctif |
|---|---|---|
| **Six bras sur sept sans shadow** | `bootstrap_arms` donne un `ShadowTracker` à chaque bras et le passe à son `LearningEngine`, mais `_track_rejections` n'alimentait que le témoin. 399 rejets jugés pour `baseline`, **zéro** pour les six autres — leur fichier n'existait pas | `_track_rejections` lit l'évaluation de chaque bras ; le prix est rafraîchi une fois sur l'union des adresses en attente |
| **Relâchement derrière la porte des 15 trades** | `run()` retournait « (en attente) 0/15 » avant d'atteindre `_relax_from_shadow`. Un bras dont les filtres coupent tout ne trade pas, donc n'atteint jamais 15, donc ne peut jamais découvrir que ses filtres coupent trop | `_relax_from_shadow` passe avant la porte. Sans risque : il ne fait que relâcher, et `_starving` n'interdit que le resserrage |
| **Âge et volume dans « autre »** | `reason_family()` n'avait pas de branche pour eux : 175 rejets d'âge et 42 de volume, **54 % du total**, rangés dans une famille qu'aucun paramètre ne dessert. Le motif dominant était invisible | familles `age_max`, `age_min` (corrections opposées) et `volume`, et `missed_rate_by_family` recalcule la famille depuis le motif pour rattraper l'historique |
| **`funnel_log.jsonl` non borné** | 11 Mo, 61 922 lignes, aucune rotation. `recent_flow()` le parsait EN ENTIER pour n'en garder que 20 lignes — à chaque `_bounded_set`, pour chacun des 7 bras | `read_funnel(tail=N)`, rotation à 8 Mo, une génération conservée et relue pour ne pas devenir aveugle après une bascule |

Cinquième correctif, de nature différente : **le veto économique portait sur
un point tiré de 4 observations.** `TAUX_ATTEINTE_MESURES` stockait des taux
nus ; « 15 % atteignent +100 % » vient de 4 succès sur 26 positions, IC95
6–34 %. `evaluate()` s'en servait pour refuser des entrées. Désormais le
plancher affiché reste celui du point, mais le **refus** se calcule à la borne
haute : une entrée n'est rejetée que si elle reste sous le plancher même dans
l'hypothèse la plus favorable compatible avec les données. Miroir exact de
`live_mode_allowed`, qui n'autorise que si la borne défavorable est bonne.
Effet mesuré : `scalp` et `runner` n'étaient plus jamais admis à 3,06 % de
frais, ils le redeviennent ; le veto mord toujours à 7,9 % et à 23,62 %.

## 7. État opérationnel

*Mis à jour le 2026-08-02.*

- **450 tests verts** (`python3 -m unittest discover -s tests`)
- Boucle en cours, cycle 90 s, monitoring 5 s

### Le panneau affichait le témoin et le présentait comme le bot

Corrigé le 2026-08-02. `stats` est le portefeuille du bras témoin, qui est
**gelé** : il montrait « 4 gagnants sur 39 », PF 0,28 et drawdown 17,4 %
figés depuis 16 h, pendant que les six autres bras accumulaient 27 gagnants
sur 93. Les gains étaient bien écrits dans les journaux — c'est la LECTURE qui
regardait le mauvais portefeuille.

`_aggregate_stats` rend désormais `wins`, `closed_trades`, `win_rate`,
`profit_factor` et `worst_arm_drawdown_pct`, **recalculés sur les journaux et
non moyennés depuis les bras** : une moyenne de taux pondérerait les 2 trades
de `quality` comme les 39 du témoin. Le drawdown ne s'additionne pas entre
portefeuilles — on rend le pire, et le libellé le dit.

```
FLOTTE (7 bras) — capital 6848.67 $ | P&L -151.33 $
Win rate : 29.0% (27/93) | Profit factor : 0.68
témoin (gelé) : 825.93 $ | -174.07 $ | WR 10.3% | PF 0.28 | 39 trades
```

### Les bras ont commencé à trader

Contredit la version précédente de cette section. Mesuré par
`python3 -m scripts.analyse_sorties` :

| bras | trades | WR | $/trade | PF | pic méd | creux méd |
|---|---|---|---|---|---|---|
| baseline | 39 | 10,3 % | −4,46 | 0,28 | +5,6 % | −17,2 % |
| sniper | 25 | 36,0 % | −1,91 | 0,59 | +24,1 % | −28,8 % |
| scalp | 5 | 40,0 % | −1,80 | 0,63 | +20,2 % | −27,5 % |
| runner | 5 | 40,0 % | **+6,72** | 2,26 | +71,6 % | −35,2 % |
| quality | 2 | 50,0 % | +4,51 | 2,02 | +59,1 % | −19,7 % |
| narrative | 0 | — | — | — | — | — |
| consensus | 6 | 50,0 % | +2,20 | 1,63 | +45,3 % | −17,6 % |

**Aucune de ces lignes ne conclut, sauf peut-être `sniper`.** À 2, 5 et 6
trades, ces chiffres sont du bruit : `runner` à +6,72 $/trade sur 5 trades ne
distingue pas une bonne stratégie d'une bonne semaine. `sniper` est le seul
au-dessus de 15 trades, et son $/trade reste négatif.

Ce que la table dit tout de même : les fenêtres élargies le 2026-08-01
produisent des pics médians de 2 à 12 fois ceux du témoin. C'est une piste à
mesurer, pas un résultat.

### Entonnoir mesuré (50 candidats par bras)

### Entonnoir mesuré (50 candidats par bras)

```
     bras     filtres  seuil_alpha  confluence
 baseline      0/50            —           —
   sniper      0/50            —           —
    scalp      4/50          2/4           —
   runner      2/50          0/2           —
  quality      0/50            —           —
narrative      2/50          0/2           —
consensus     24/50        10/24         0/6
```

**Le blocage est aux filtres, pas ailleurs.** Aucun candidat n'atteint jamais
la technique ni la garde économique. Motif dominant de `scalp` : 19 rejets sur
« âge > 2h ».

`consensus` affiche 0/6 : il exige ≥2 bras d'accord, mais trois bras acceptent
zéro candidat. Il est en avance sur ses dépendances.

### Mais l'entonnoir seul désigne le mauvais coupable

« Motif dominant : l'âge » invite à élargir la fenêtre d'âge. Le shadow dit
l'inverse. `python3 -m scripts.analyse_shadow --bras baseline`, 399 rejets
jugés, seuil +100 % :

```
famille        n    ≥+100%     IC95      pic max
liquidity    176     13,6%    [9–19]      +3578%
age_max      168      0,6%    [0–3]        +154%
volume        42      2,4%    [0–12]       +170%
```

**Le plafond d'âge est justifié : ce qu'il écarte ne monte pas.** Sur 168
tokens rejetés pour « trop vieux », un seul a franchi +100 %. C'est le
**plancher de liquidité** qui coûte : 24 des 27 gros manqués viennent de là,
dont CATE à +3578 %, JORDAN à +1677 %, KEK à +1345 %.

Réserve qui interdit d'en conclure « baisser la liquidité » : ce sont
exactement les carnets où le slippage est le pire, et un shadow n'a ni
slippage ni impact de marché. La suite est un devis Jupiter sur ces tokens,
pas un changement de seuil.

Comparaison qui situe l'enjeu : le win rate réalisé du témoin est 10,3 %,
tandis que 13,6 % de ce qu'il a rejeté sur la liquidité a franchi +100 %.

---

## 7 bis. Notifications — ce qui perce le silence

Corrigé le 2026-08-02, sur signalement : une position à +100 % visible au
dashboard, rien sur Telegram. **Quatre défauts distincts**, pas un.

1. **Aucune notification de gain LATENT.** `send_entry` et `send_exit`
   couvrent les deux extrémités du trade. Entre les deux, rien : `Meowt`
   (runner) est montée à +83,4 % sans qu'un seul message parte, parce qu'elle
   était encore ouverte.
2. **`notify=none` taisait aussi les gros gains.** La politique « seul le
   témoin parle en direct » a été écrite quand seul le témoin tradait. Depuis,
   il n'a rien pris en 5 h pendant que les six autres bras produisaient 27
   gagnants sur 93 : la règle revenait à taire exactement ce qu'on voulait voir.
3. **Le premier digest arrivait 4 h après le démarrage.** `_last_summary` était
   initialisé à `time.time()`, donc chaque redémarrage repoussait l'échéance.
   Avec plusieurs relances dans la journée, il n'est jamais parti.
4. **La troncature était silencieuse.** `messages[-5:]` faisait passer un
   digest amputé pour un digest complet.

**Ce qui parle maintenant :**

| événement | comportement |
|---|---|
| gain latent franchissant +50 / +100 / +150 % | alerte immédiate, une fois par position, **quelle que soit la politique** |
| sortie ≥ +50 % | perce `notify=none` |
| sortie perdante, sortie < +50 % | digest, comme avant |
| premier digest | 15 min après le démarrage, puis toutes les 4 h |

Les seuils viennent des taux d'atteinte mesurés sur 26 positions
instrumentées : +50 % est franchi par 23 % des trades, +100 % par 15 %,
+150 % par 4 %. Le pic médian étant **+4,9 %**, un palier plus bas sonnerait à
chaque trade.

**Les pertes restent muettes, et c'est délibéré.** 96 % des trades touchent
−10 % : les alerter serait un bruit permanent, et un canal qu'on finit par
ignorer ne notifie plus rien du tout.

L'état des paliers vit dans la boucle, pas dans `Position` — celle-ci est
immuable et sérialisée sur disque, y ajouter un champ obligerait à migrer les
positions déjà ouvertes. Le coût assumé : un redémarrage réannonce une fois un
palier déjà franchi.

---

## 7 ter. Agents de mesure (`src/agents/`)

Ajoutés le 2026-08-02. **Ils calculent et journalisent ; aucun n'écrit un
paramètre ni ne refuse une entrée.**

La distinction qui organise ce paquet : un agent qui APPREND réclame un
échantillon, et le dépôt en compte 93. Greffer une douzaine d'apprenants
là-dessus produirait douze surapprentissages parallèles. Ceux-ci produisent
d'abord la donnée que la couche d'apprentissage lira ensuite ; l'ordre inverse
donnerait des lecteurs de fichiers vides.

| agent | ce qu'il mesure | quand | journal |
|---|---|---|---|
| `counterfactual_timing` | prix à −1, −2, −3 cycles avant l'entrée | ouverture | `counterfactual_log.jsonl` |
| `dev_history` | score créateur 0-100 sur 6 signaux déjà collectés | ouverture | `dev_history_log.jsonl` |
| `volatility_agent` | écart-type des rendements log, base horaire | clôture | `volatility_log.jsonl` |
| `microstructure_agent` | dérive liquidité/prix, devis A/R, profondeur | clôture | `microstructure_log.jsonl` |
| `rsi_agent` | RSI de Wilder sur bougies reconstruites | clôture | `rsi_log.jsonl` |

### Deux points de mesure, et pourquoi

Les cinq agents tournaient d'abord tous à l'ouverture. Mesuré sur les deux
premières entrées réelles du 2026-08-02 :

```
rsi          samples 1 et 3    (il en faut 15)
volatility   samples 1 et 5    (il en faut 8)
```

**Le bot entre vite après la découverte**, donc `PriceHistory` est presque vide
au moment d'ouvrir. Trois des cinq agents mesuraient une ACCUMULATION avec un
ou deux points.

Le partage suit ce que chaque agent a réellement besoin de voir :

- **à l'ouverture** — ce qui n'existe qu'à cet instant. Les snapshots d'AVANT
  l'entrée disparaissent au cycle suivant, et le candidat enrichi aussi ;
- **à la clôture finale** — ce qui s'accumule. Le monitoring tourne à 5 s : sur
  une détention de 30 min, la volatilité passe de 1 à ~360 rendements. Une
  ligne par position, pas une par tick — mesurer à chaque tour écrirait des
  centaines de lignes redondantes sauf la dernière.

Les lignes de clôture portent `position_id`, `arm`, `pnl_pct`, `peak_pct` et
`duration_min` : **un indicateur qu'on ne peut pas relier à un résultat ne
s'évalue jamais.**

Le RSI reste le plus exigeant et le restera : les bougies font 180 s, donc ses
15 clôtures demandent 45 minutes de détention — plus long que la durée de vie
de plusieurs bras. Il journalise « inconnu » sur les positions courtes, ce qui
est la réponse honnête plutôt qu'un RSI calculé sur trois points.

### Première lecture réelle

Le contrefactuel a produit du signal dès les deux premières entrées :

```
GOONER   -90s : +6,33 %
Slop     -270s : +4,98 %   -180s : +0,91 %   -90s : +5,19 %
```

Trois valeurs sur quatre au-dessus du seuil de franchissement (3,06 %). Sur
deux trades ça ne conclut rien — mais c'est la grandeur que cet agent existe
pour capter, et le mécanisme fonctionne.

`dev_history` sort une couverture de **14 %** : un seul de ses six signaux est
renseigné, parce que ces tokens viennent de DexScreener et non de GMGN.
`MIN_COVERAGE = 0.5` l'empêche donc de rejeter quoi que ce soit — le garde-fou
joue son rôle, mais l'agent restera inerte tant que l'enrichissement GMGN ne
couvrira pas les tokens réellement achetés.

**Quatre décisions de conception qui viennent d'une mesure, pas d'une
intuition :**

1. **Il n'y a pas de bid-ask sur Solana.** Les memecoins se traitent sur des
   AMM, et `Snapshot` ne porte que `(ts, price, volume_5m, liquidity)`. Le
   spread est remplacé par `round_trip_cost_pct` de Jupiter — un devis réel —
   et la profondeur par la liquidité du pool. Fabriquer un spread depuis ces
   champs aurait produit un nombre plausible et faux.
2. **Les décalages du contrefactuel sont des multiples du cycle de scan.** Les
   −30 s / −10 s / +10 s du réflexe initial sont **plus fins que
   l'échantillonnage** : avant l'ouverture, le seul historique vient du scan à
   90 s. Vérifié de bout en bout — les trois décalages sortaient `null`, et un
   journal de `null` se lit « pas d'avantage de timing » au lieu de « pas de
   données ».
3. **`PriceHistory` vit en mémoire, jamais sur disque.** Les snapshots
   d'avant-entrée n'existent que pendant le cycle qui ouvre la position : c'est
   la seule fenêtre où le contrefactuel peut reconstituer quoi que ce soit,
   d'où l'appel dans `_open_position`. **Conséquence : ces mesures ne peuvent
   PAS être rejouées sur les 93 trades déjà clôturés.** Elles partent de zéro,
   vers l'avant.
4. **Le seuil de `dev_history` est calé sur l'arithmétique des poids.** 45 est
   la seule valeur qu'aucun signal isolé n'atteint et que deux signaux forts
   concordants dépassent (creator_sold + serial_launcher = 46,4 %). Les trois
   signaux faibles cumulés font 35,7 % et ne rejettent pas : ils ont déjà leurs
   filtres au manifeste, les compter deux fois serait un doublon silencieux.

Invariant commun, hérité du pipeline : **une donnée absente ne rejette jamais
et ne s'invente pas.** Chaque agent journalise aussi ses lectures inconnues —
sans quoi on ne saurait plus distinguer « jamais en surachat » de « jamais
assez de bougies pour le savoir ».

---

## 8. Fichiers qui comptent

```
src/main.py              boucle, orchestration des 7 bras         1252 l
src/agents/              5 agents de mesure, aucun n'apprend
src/pipeline.py          collect() partagé / evaluate() par bras
src/core/arm.py          StrategyArm, manifeste, ArmNotifier
src/core/learning.py     ajustements, simulate_exits, bornes/bras
src/core/journal.py      read_positions() <- la correction clé
src/core/economics.py    plancher de TP, sizing par le coût
src/core/funnel.py       entonnoir de décision, entrée ET sortie
src/core/signals.py      vote avec abstention, quorum sur présents
src/core/scoreboard.py   justesse par agent
src/core/wallets.py      registre de wallets (observation seule)
src/apis/jupiter.py      prix par lot, devis, honeypot
config/strategies.json   manifeste des 7 bras
config/arms/<nom>.json   document params par bras (propriété du bras)

scripts/analyse_sorties.py   perdants, gagnants, atteignabilité, grille
scripts/analyse_rejets.py    entonnoir : où meurent les candidats
scripts/analyse_shadow.py    ce que les filtres ont coûté, IC95 par famille
scripts/export_vault.py      journal -> notes Obsidian reliées
```

---

## 9. Garde-fous contre le surapprentissage

| constante | valeur | rôle |
|---|---|---|
| `MIN_SEGMENT_SAMPLE` | 10 | pas d'ajustement sans 10 trades dans le segment |
| `MIN_FLOW_TO_TIGHTEN` | 2 | **casse la boucle** : interdit de resserrer un filtre quand le flux est déjà famélique. Relâcher reste toujours permis |
| `MIN_TRADES_PER_ARM` | 15 | plancher par stratégie — le multi-bras divise l'échantillon par 7 |
| `MIN_TRADES_FOR_WEIGHTS` | 50 | avant de pondérer un agent |
| `MIN_SIGNALS_PER_AGENT` | 20 | avant de juger un agent |
| `EXIT_BACKTEST_MIN_COVERAGE` | 15 | sous ce seuil, **aucun verdict** — ne pas savoir ≠ annuler |
| `PARAM_BOUNDS` | par bras | bornes dures, sinon les règles ne font que resserrer |
| `INACTIVITY_CYCLES` | 300 | au-delà, un bras qui n'entre plus voit son seuil dominant desserré. Calé sur la mesure : les bras qui entrent le font tous les 36 à 155 cycles |
| `_relax_set` | — | **un relâchement qui, une fois clampé, resserrerait est refusé.** Les bornes sont globales, la config d'un bras ne l'est pas |
| `compare` | IC95 disjoints | aucun bras déclaré meilleur que le témoin sans séparation franche. Sept stratégies comparées produisent un gagnant par hasard |

### Les trois relâchements, et sur quelle preuve

Ils sont indépendants et ne se remplacent pas :

| mécanisme | preuve exigée | débloque |
|---|---|---|
| `_adjust_filters` | 15 trades du bras **et** 10 dans le segment | un bras qui trade déjà et perd sur un segment |
| `_relax_from_shadow` | 15 rejets jugés dans une famille, >25 % montés à +100 % | un bras dont les rejets gagnaient |
| `_relax_from_inactivity` | 300 cycles évalués sans **aucune** entrée | un bras qui ne joue pas du tout |

Le troisième existe parce que les deux premiers sont inatteignables pour un
bras qui n'entre jamais : pas de trades, et pas assez de rejets jugés par
famille. `narrative` était dans ce cas — 0 entrée sur 927 cycles.

**Précision a posteriori, 2026-08-03 : `frozen` n'a jamais couvert les trois.**
Il ne gate que `_relax_from_inactivity` — `_adjust_filters` et
`_relax_from_shadow` ont toujours tourné sur le témoin comme sur les autres
bras, y compris avant cette date. Le témoin bénéficie désormais des trois,
`frozen=False` comme tout le monde : décision explicite du propriétaire pour
qu'il devienne lui-même une bonne stratégie plutôt qu'un point de comparaison
figé.

### Mise en commun des trajectoires

`simulate_exits` ne lit que `peak_pct` et `trough_pct` — **une trajectoire est
une trajectoire**, elle ne dépend pas du bras qui l'a achetée. `_instrumented`
emprunte donc aux autres journaux, filtré par `_in_window` sur l'âge et la
liquidité à l'entrée :

```
bras       propres  + pool  total   rejeu possible ? (seuil 15)
scalp            6      20     26   OUI  (était NON)
runner           6      20     26   OUI  (était NON)
consensus        6      20     26   OUI  (était NON)
quality          2       8     10   NON — sa fenêtre exclut les autres
narrative        0       0      0   NON
```

Le filtre de fenêtre n'est pas une précaution de style : un token de `sniper`
(âge 0-1 h, liquidité 4 K) n'a pas la forme d'un token de `quality` (âge
4-48 h, liquidité 20 K). Sans lui on remplacerait un manque de données par un
biais — et le manque, lui, se voit.

**Les filtres d'ENTRÉE ne se mettent jamais en commun** : chaque bras a ses
seuils, c'est toute sa raison d'être.

---

## 10. Limites connues, à dire avant d'agir

1. **Le rejeu des sorties ne connaît pas l'ordre pic/creux** sur les lignes
   antérieures à `minutes_to_trough`. 3 des 26 positions sont ambiguës — et ce
   sont 3 des 4 gagnantes. Politique pessimiste : −2,76 $/trade. Optimiste :
   −1,14 $. **Le choix de politique détermine la réponse.** Les nouveaux trades
   portent les deux dates et sont tranchés exactement.
2. **Élargir le stop est structurellement sous-évalué** par le rejeu : le cas
   « aucun seuil franchi » retombe sur un P&L produit par le stop historique.
3. **Le trailing n'est pas rejouable** — il dépend du plus-haut à chaque tick.
4. **26 positions, 4 gagnants.** Toute conclusion est dominée par 4 observations.
5. **Le social est mort** (402 Twitter) — les poids sont redistribués, mais les
   bras ne sont donc pas comparables sur cet axe.
6. **Les bras ne sont pas isolés sur l'axe social** : la couverture de l'un
   dépend de ce que les autres ont demandé (budget partagé).
7. **Les six bras neufs repartent de zéro sur le shadow.** Leur log vient
   d'être branché (§6 ter) : il leur faut `SHADOW_MIN_SAMPLE` = 15 rejets
   jugés par famille, à 4 h d'observation chacun, avant que `_relax_from_shadow`
   puisse rendre quoi que ce soit. Les 399 rejets du témoin ne leur servent
   pas — un bras rejette sur SES seuils.
8. **`ShadowTracker._tracked` n'est pas persisté.** Les suivis en cours vivent
   en mémoire : un redémarrage perd tout ce qui n'a pas encore atteint ses 4 h.
   Sur une boucle relancée souvent, le shadow se remplit plus lentement que le
   nombre de rejets ne le laisse croire.
9. **Le multi-bras coûte moins cher que je ne l'ai d'abord écrit.** Chaque bras
   se débloque sur SES 15 trades, indépendamment des autres — il n'y a aucun
   total de 105 à atteindre. Mesuré au 2026-08-02 : `baseline` (39 trades) et
   `sniper` (26) ont franchi toutes leurs cadences et apprennent déjà, et la
   cadence d'arrivée est de 8 à 32 trades/jour/bras. Le coût réel n'est pas
   l'attente, c'est la **comparaison multiple** — traitée par
   `verdict_vs_reference`.
10. **Aucun bras n'est distinguable du témoin.** IC95 du P&L/trade, 2026-08-02 :
    `sniper` [−4,74 .. +1,40], `runner` [−5,61 .. +25,00], `consensus`
    [−5,42 .. +11,37]. Le classement par $/trade est du bruit tant que cette
    colonne dit « indistinguable ».

---

## 11. Décisions ouvertes

| sujet | état |
|---|---|
| élargir `scalp` de 2 h à 4 h | 19 rejets d'âge le suggéraient — **le shadow dit non** : 0,6 % [0–3] des rejets `age_max` franchissent +100 %. L'entonnoir désignait le mauvais coupable |
| baisser le plancher de liquidité | 13,6 % [9–19] des rejets `liquidity` franchissent +100 %, dont un à +3578 %. **Ne rien changer avant un devis Jupiter** sur ces carnets : c'est là que le slippage est le pire, et le shadow ne le mesure pas |
| réduire le nombre de bras | **écartée.** Chaque bras se débloque sur SES 15 trades, pas sur un total : `baseline` (39) et `sniper` (26) apprennent déjà, et la cadence est de 8 à 32 trades/jour/bras. Le temps n'était pas la contrainte |
| `quality` : `max_age_hours` hors borne | config 48 h, borne globale (2, 24). Tout relâchement est refusé par `_relax_set` — **décision de propriétaire** : élargir la borne dans `bounds` du manifeste, ou ramener la config sous 24 h |
| `narrative` et `consensus` bloqués au seuil alpha | et non sur un filtre. `_relax_from_inactivity` va descendre 70 → 67,5 et 65 → 62,5, plancher à 55. À surveiller : c'est le relâchement le plus coûteux de la table |
| Birdeye Lite 39 $/mois | seul abonnement qui débloque quoi que ce soit |
| suivi de wallets étape 2 | registre en place, 7 wallets, avances de +0,4 à +3,6 min — **trop faibles pour du copy-trading** si ça se confirme |
| découpage de `main.py` | proposé, refusé pour l'instant : **1084 lignes** (+202 depuis le refus), règles encore mouvantes |
| consensus pondéré | prématuré, zéro trade — `MIN_TRADES_FOR_WEIGHTS` l'interdit |

---

## 12. Principes de travail sur ce dépôt

- **Mesurer avant d'affirmer.** Chaque seuil de ce document vient d'une commande
  qu'on peut relancer.
- **Une donnée absente ne rejette jamais.** Vrai du pipeline, des agents, du
  rejeu.
- **Dire l'attente.** « rien ajusté » et « pas assez de données pour ajuster »
  sont deux états différents.
- **Les commentaires expliquent le POURQUOI**, souvent en citant le bug évité.
- Docstrings et tests en français, noms de tests décrivant le bug verrouillé.
