> Rapport BRUT d'un auditeur (sous-agent, lecture seule), 2026-11-17. Les constats n'ont pas tous été re-vérifiés : la synthèse vérifiée est `docs/AUDIT_2026-11-17.md`. Aucun chiffre ici n'est un résultat de performance (journaux absents du dépôt).

# Audit « le nom dit une chose, le code en fait une autre » — CryptoBot

Périmètre : lecture seule du dépôt (aucun fichier modifié). Seule exécution : `python -m unittest discover -s tests` (1242 OK, `git status` propre) et deux petits `python -I` de simulation hors dépôt. Tous les numéros de ligne sont ceux de l'arbre au 2026-11-17.
Déjà connus, non re-rapportés : onglet Backtester, `min_liquidity_lock_days`, tests style pytest, paragraphe « P&L papier optimiste », `read_final_exits()`.

Bilan : 5 HAUT, 14 MOYEN, 8 BAS (27 constats).

---------------------------------------------------------------------------
## HAUT
---------------------------------------------------------------------------

### H1. `verdict_vs_reference` rend « supérieur / inférieur » dès 3 trades (faux positifs mesurés : 42 % à n=4, 10 % à n=10)
**Sévérité : HAUT**
- **Ce que disent les docs :** `api/server.py:335` « à 7 jours [le verdict] vaut presque toujours « échantillon trop court », et c'est la bonne réponse » ; `src/core/stats.py:151` et ADR 011 (l.26) « sur six bras comparés au témoin, le risque d'au moins un faux positif reste sous 3 % » ; `config/strategies.json` (score_v2) « Verdict vs baseline après >= 200 trades » ; ADR 011 présente comme « verdict possible » un IC95 calculé sur **4 trades** (`narrative`).
- **Ce que fait le code :** `bootstrap_mean` ne refuse que `len(data) < 3` (`src/core/stats.py:110`) ; `verdict_vs_reference` ne rend « échantillon trop court » que si l'un des deux IC est `None` (donc < 3 trades). `compare()` (l.238) conclut si les IC ne se chevauchent pas. Le bootstrap percentile sur 3-5 valeurs asymétriques donne un IC absurdement étroit. `Interval.conclusive` (`stats.py:57`, `width < 12.0`) est un seuil en « points de pourcentage » appliqué à un IC de **dollars/trade** : un IC de 3 trades de largeur 2,5 $ est marqué `conclusive: True` (exposé dans `vs_baseline.interval` de state.json et `/api/arms/performance`). Le commentaire `telegram_reporter.py:294` sait que ce seuil ne vaut pas pour des $ — mais l'API et le state continuent de l'exposer. Il y a maintenant 18 bras comparés au témoin, pas six (0,5 %/comparaison → ~9 % de faux positif global, avant même le défaut de n).
- **Preuve exécutée :** `verdict_vs_reference([-1,0.5,-2], [-5,-4,-6])` → `'supérieur'`, `conclusive: True` des deux côtés. Simulation (2000 tirages, même loi asymétrique 80 % × -5 $ / 20 % × +20 $ des deux côtés) : bras à n=4 contre témoin à n=40 → verdict « supérieur/inférieur » dans **42 %** des cas ; n=10 contre 40 → **10 %**.
- **Conséquence :** la décision d'activer/désactiver/promouvoir un bras (ADR 011, « seul verdict concluant justifie l'arrêt ») s'appuie sur un verdict qui conclut presque au hasard sur les petits échantillons ; la colonne « vs témoin » du dashboard (`ArmPerformance.tsx:136`) l'affiche telle quelle.
- **Correction minimale :** plancher de taille dans `verdict_vs_reference` (ex. `min(len) >= 30`, cohérent avec « 30 paris » côté Liquid) → sinon « échantillon trop court » ; corriger la phrase « six bras / 3 % » ; ne pas émettre `conclusive` sur un IC en $ (ou le recalculer en fonction de la largeur relative).

### H2. Paramètres de protection déclarés mais jamais lus : `filters.max_tax_buy`, `filters.max_tax_sell`, `entry_rules.slippage_max_pct`, `entry_rules.entry_timing`, `entry_rules.position_size_formula` (le vrai plafond de coût est `entry_rules.max_round_trip_pct`, non déclaré)
**Sévérité : HAUT** (affirmation de sécurité)
- **Ce que disent les noms :** `config/params.json:40-41` `max_tax_buy: 5`, `max_tax_sell: 5` (« taxe achat/vente ≤ 5 % »), `:47` `slippage_max_pct: 15`, `:48` `entry_timing: "breakout_5m"`, `:50` `position_size_formula: "kelly_criterion_adjusted"`. Recopiés dans les 18 `config/arms/*.json` et rendus modifiables/lisibles dans l'onglet Config (`ParamSection`).
- **Ce que fait le code :** `grep -rw` sur `src api scripts dashboard/src` : **0 lecture** pour `max_tax_buy`, `max_tax_sell`, `slippage_max_pct`, `entry_timing`, `position_size_formula` (seule trace de « tax » : `src/core/gmgn_observe.py:147`, observation journalisée, aucune décision). Le plafond de coût réellement appliqué est `arm.params.get("entry_rules.max_round_trip_pct", 6.0)` (`src/main.py:1923`) : clé **absente de tous les fichiers de config** (défaut codé en dur 6,0 ; `economics.MAX_ACCEPTABLE_ROUND_TRIP_PCT = 8.0` n'est que le défaut de la fonction). La taille est `capital × risk_per_trade × (1 + wr − 0,40)` (`portfolio.py:354-356`), sans lien avec `position_size_formula`.
- **Conséquence :** relever « slippage_max_pct » de 15 pour laisser entrer plus de tokens ne change rien ; croire à un filtre de taxe ≤ 5 % (honeypot à taxe) alors qu'aucun n'existe ; deux nombres différents (15 déclaré, 6 réel) pour le même plafond.
- **Correction minimale :** retirer les 5 clés des fichiers (ou les brancher avec test) ; déclarer `entry_rules.max_round_trip_pct` dans params.json pour que la valeur réelle soit visible/éditable ; documenter dans l'ADR du A3 de la feuille de route (déjà ouvert pour `min_liquidity_lock_days`) que la classe est plus large.

### H3. Les chiffres « FLOTTE » du bandeau (equity, P&L, win rate, profit factor, nombre de trades) agrègent 19 bras dont 12 clones de test du témoin, chacun avec une mise virtuelle de 7 000 $
**Sévérité : HAUT**
- **Ce que disent les noms :** `src/main.py:812` « Vue d'ensemble des **7** stratégies » ; `Shell.tsx:84-92` chiffre héroïque « equity » = `agg.equity`, commentaire « les **six** portefeuilles totalisaient 6 926 $ » ; `TheBrain.tsx` cellules « win rate / profit factor / trades — clôturés, tous bras » ; banner console `main.py:2370` « FLOTTE (N bras) — **capital** X $ ».
- **Ce que fait le code :** `_aggregate_stats` (`main.py:811-870`) somme `equity` de **tous** `self.arms` (19 : `config/strategies.json`), chaque bras démarrant avec `capital_total` = 7 000 $ (`arm.py attach_portfolios` : « chaque bras a une mise indépendante et identique »). 12 de ces bras sont `role: test` et des **clones/forks du témoin** (`tp1_15/20/30`, `tp1_20_vl2` = « clone EXACT », `anti_rug` = « fork EXACT », `fixes3`, `consensus_af`, `score_v2`, `sniper_ar`, `mature_liquid`, `scaled_be`, `copy_smart`) : mêmes tokens, souvent mêmes entrées. win rate et PF « flotte » comptent donc le même événement de marché plusieurs fois (pseudo-réplication), et le chiffre « capital/equity » est une somme de comptes fictifs (`docs/ETAT_DU_PROJET.md` §1.1 : « equity 38 390 $ » pour 7 bras × 7 000 − 10 609 $). `equity()` est de plus « à la valeur d'entrée » (`portfolio.py` `equity()`), pas au marché.
- **Conséquence :** le chiffre le plus visible de l'écran n'est ni un capital, ni une performance de stratégie ; un bon bras de test ou un clone perdant déplace le « win rate tous bras » et le « profit factor » qui sert d'indicateur de santé du projet. Le pourcentage de rendement du header (`Shell.tsx` `pnlPct`) a pour dénominateur 19 × 7 000.
- **Correction minimale :** n'agréger que les bras `voter`/`consensus` (ou le seul témoin) pour win rate/PF/equity du bandeau, et libeller le reste (« somme de N comptes fictifs indépendants ») ; corriger « 7 stratégies » / « six portefeuilles » dans docstring et commentaires.

### H4. `would_have_won` / « Ghost mode » : le libellé dit « aurait gagné / TP1 », le code mesure « le pic a touché +100 % à un moment », et cela pilote le relâchement automatique des filtres
**Sévérité : HAUT**
- **Ce que disent les noms :** `src/core/shadow.py:59` « Aurait atteint **TP1 (+100 %)** », champ journal `would_have_won`, `TheBrain.tsx:198` « des rejets **seraient partis** à +100 % », `api/server.py get_shadow` `missed_rate`.
- **Ce que fait le code :** `peak_gain_pct >= 100` constant (`shadow.py:60`), indépendant du `take_profit_1` du bras (150 pour le témoin, 40 pour sniper, 15-30 pour les tp1_*), et sans règle de stop : un token qui plonge à -60 % puis touche +100 % dans les 4 h est compté « gagnant ». C'est un pic non réalisable (aucun SL, aucun coût, aucun ordre d'exécution). `_relax_from_shadow` (`learning.py:722`, seuil `MISSED_RATE_RELAX_THRESHOLD = 25`) relâche automatiquement le filtre de la famille dès que 25 % des rejets franchissent ce pic.
- **Conséquence :** les filtres se desserrent sur un critère que le bot ne pourrait pas encaisser, et l'étiquette « TP1 » est fausse pour tous les bras sauf un hypothétique TP1=100. README/CONTEXT disent « trades fictifs, pour arbitrer des seuils » mais ne disent pas que le critère est le pic brut.
- **Correction minimale :** renommer (`peak_reached_100`), corriger la docstring, et soit paramétrer le seuil par bras (TP1 du bras), soit exiger « sans toucher le SL effectif avant le pic » avant d'autoriser le relâchement.

### H5. Le stop « -25 % » affiché n'est pas le stop appliqué (-15 %), et « BREAKEVEN_STOP » vaut +10 % : trois affichages différents du même stop + une note de config contredite par sa propre valeur
**Sévérité : HAUT**
- **Ce que disent les noms :** `exit_rules.stop_loss_pct: -25` ; Telegram à l'entrée `notify/telegram.py:63` et `agents/telegram_reporter.py:147` « SL : {position.stop_loss_pct} » ; panneau STRATÉGIES `TheArms.tsx:150` « stop loss {arm.stop_loss_pct}% » (= `exits.get("stop_loss_pct")`, `arm.py summary`) ; `BREAKEVEN_STOP`.
- **Ce que fait le code :** `Position.effective_stop_loss_pct = base + stop_loss_slippage_buffer_pct` (`positions.py:~176-184`), et `evaluate_exits` déclenche sur ce seuil (`positions.py:~268`). Avec les valeurs de params.json (SL -25, tampon **+10,0**, l.70) le stop réel est **-15 %**. Après TP1 ou `breakeven_trigger`, `base = 0` → le « breakeven » est posé à **+10 %** (sauf `breakeven_stop_exact`, réservé aux bras tp1_*), mais l'étiquette de sortie reste `BREAKEVEN_STOP` (`positions.py:~269`). `ActivePositions.tsx` affiche, lui, le seuil effectif (commentaire l.63) → un même trade montre -25 sur Telegram/panneau Stratégies et -15 sur la carte de position. Par ailleurs `params.json:71` `_note_sl_buffer` affirme « **0 VOLONTAIREMENT** » alors que la valeur juste au-dessus est 10,0 (et l'historique d'apprentissage montre 716 entrées dont des « tampon augmenté … » qui font en réalité **baisser** la valeur de 15 → 10 le 2026-08-17 — le garde-fou croisé `MIN_EFFECTIVE_STOP_LOSS_PCT` a écrasé la proposition, mais la raison enregistrée décrit la proposition, pas le résultat). `economics.py:163` garde `rest_pct = -2,9` « mesuré via le stop breakeven » alors que ce stop vaut +10 % pour le témoin.
- **Conséquence :** lecture erronée du risque par trade (Telegram) ; analyses « BREAKEVEN_STOP » qui supposent une sortie ≈ 0 alors qu'elle vaut +tampon ; note de config contradictoire qui pousse à « corriger » une valeur voulue.
- **Correction minimale :** afficher `effective_stop_loss_pct` partout (Telegram, `summary()`), renommer l'étiquette en `BREAKEVEN_STOP` seulement si `breakeven_stop_exact`, sinon `PROFIT_LOCK_STOP`, remplacer `_note_sl_buffer` par l'état réel, journaliser la valeur appliquée (et non proposée) dans `parameter_adjustment_history.reason`.

---------------------------------------------------------------------------
## MOYEN
---------------------------------------------------------------------------

### M1. « Kelly » et `risk_per_trade` ne sont ni Kelly ni un risque par trade
**Sévérité : MOYEN**
- **Dit :** README l.48, l.70 « sizing Kelly » ; `portfolio.py:349` « Kelly ajusté » ; `params.json:50` `kelly_criterion_adjusted` ; `risk_per_trade: 0.01`.
- **Fait :** `position_size = capital × risk_per_trade × (1 + (wr − 0,40))` plafonné à 5 % (`portfolio.py:354-357`) ; `win_rate` = part de trades `pnl_usd > 0` du bras (`main.py:2049-2052`). Aucun ratio gain/perte, aucune fraction f* = p − q/b. `risk_per_trade` est la **taille de position en % du capital** (1 %), pas la perte au stop (1 % × 15-25 % ≈ 0,15-0,25 % du capital). Le plafond de 5 % ne peut jamais mordre (max ≈ 1,6 %). Sous 20 trades ou WR ≤ 40 % (cas réel : WR 18 %) le facteur est neutre.
- **Conséquence :** une personne qui règle `risk_per_trade` en pensant « 1 % de risque » sous-estime/surestime d'un facteur 4 à 7 la perte réelle ; `learning._adjust_risk` (`learning.py:988-1010`) « ajuste le risque » alors qu'il ajuste la taille.
- **Correction :** renommer (`position_pct`) ou documenter ; retirer « Kelly » du README/params.

### M2. Le seuil d'entrée « sur le score absolu » est appliqué par un `break` sur une liste triée par le score de classement
**Sévérité : MOYEN**
- **Dit :** `main.py:1705-1708` commentaire « Le seuil porte sur le score ABSOLU » ; scoring.py/CONTEXT : `alpha_score_absolute` autorise, `alpha_score` classe.
- **Fait :** `scoring.py:202` `scored.sort(key=alpha_score)` (60 % absolu + 40 % rang) puis `_try_entries` fait `if candidate.alpha_score_absolute < threshold: break` (`main.py:1708`). Le tri n'est pas monotone en absolu : un candidat A (absolu 60, bon rang) précède un candidat B (absolu 70, mauvais rang) ; avec seuil 65 le `break` sur A **n'évalue jamais B**. (La ligne `main.py:694/890` compte « qualifiés » avec un filtre, pas un `break` → le compteur « qualifiés » du dashboard peut dépasser ce qui est réellement tenté.)
- **Conséquence :** des entrées éligibles perdues silencieusement, en partie responsable des « bras inactifs » que le relâchement d'inactivité tente ensuite de débloquer en baissant le seuil.
- **Correction :** `continue` à la place de `break` (comme pour le filtre score v2 juste dessous, dont le commentaire dit précisément pourquoi).

### M3. `liquidity_absolute` : la docstring donne de fausses valeurs, et le composant est quasi saturé après filtre
**Sévérité : MOYEN**
- **Dit :** `scoring.py:51` « Échelle log : 15K → ~40, 50K → ~70, 150K+ → 100 ».
- **Fait :** `100·log10(L)/log10(150000)` = **80,7** à 15K, **90,8** à 50K, **92,3** à 60K (le filtre `min_liquidity_usd` du témoin). (calcul vérifié.)
- **Conséquence :** avec le plancher de liquidité à 60K le composant pèse 20 % du score en valant 92-100 pour tout token retenu : il ne départage rien et gonfle `alpha_score_absolute` de ~18 points, donc le seuil d'entrée est plus facile à franchir que ne le suggère la docstring.
- **Correction :** corriger la docstring ; envisager une saturation plus haute ou une échelle relative au plancher.

### M4. « Profit factor » : quatre conventions pour « aucune perte », dont une qui s'affiche comme « aucun gain »
**Sévérité : MOYEN**
- **Fait :** `portfolio.py:972` et `main.py:862` → `0.0` ; `stats.py:472` → `None` (avec docstring l.455 « 0 dirait l'inverse de la réalité ») ; `weeklyStats.ts` → `Infinity` ; `learning.py:998` (`_adjust_risk`) → `0.0` mais `learning.py:1476` (`live_mode_allowed`) → `inf`. `TheBrain.tsx:129-134` affiche « aucun gain à ce jour » dès que `profit_factor == 0`, y compris le cas « que des gains ».
- **Conséquence :** pour un bras à gains uniquement (début de vie), la carte du bras (`stats.profit_factor`, `TheArms`) et le bandeau indiquent PF 0 / « aucun gain » ; `_adjust_risk` traite « zéro perte » comme PF 0 (jamais d'augmentation du risque même avec 100 % de gains).
- **Correction :** une seule fonction (celle de `stats.position_stats`, `None` si pas de perte) utilisée par portfolio, aggregate, learning et le dashboard.

### M5. « Drawdown » : au moins quatre définitions ; le « drawdown max » qui sert de coupe-circuit est un maximum historique qui ne redescend jamais, avec un pic non restauré au redémarrage
**Sévérité : MOYEN**
- **Fait :** `portfolio.py:240-243` rejoue la courbe de P&L **réalisé** depuis le journal (pic = max cumulé) ; mais le suivi en direct (`portfolio.py:~936-941`, `_finalize`) utilise `equity()` (valeur d'entrée des positions ouvertes) et `peak_capital`, initialisé à `max(baseline, capital_courant+engagé)` (`:189`) — **le pic historique n'est pas restauré** : après un redémarrage, le drawdown à venir est mesuré par rapport à un pic plus bas. `stats.window_performance` (réalisé, % du pic), `prediction/paper.py:_drawdown_pct` (perte pic→creux en **$ divisée par le capital initial**, pas par le pic), `drawdown.ts` (réalisé, % du pic). `drawdown_breached` (`portfolio.py:322-326`) compare `max_drawdown_pct` (maximum de tous les temps) à `max_drawdown_stop_pct` : une fois franchi, plus aucune entrée tant que le plafond n'est pas relevé, même après récupération (documenté « ne s'efface pas », mais le message dit « drawdown X % », pas « max historique X % »). Le bandeau `TheBrain` affiche « pire bras, depuis son sommet » = ce maximum historique.
- **Conséquence :** deux « drawdown max » du même bras peuvent différer entre l'API (réalisé) et le bandeau (mixte) ; le pourcentage Liquid n'est pas comparable à celui des memecoins.
- **Correction :** restaurer `peak_capital` depuis le journal, nommer « drawdown max historique », unifier sur `stats.py`.

### M6. `capital_pct`, sa validation « sinon le bot refuse de démarrer » et `notify` : des champs de manifeste sans effet, protégés par une erreur de démarrage
**Sévérité : MOYEN**
- **Dit :** `strategies.json:4` « capital_pct doit sommer à 1.0, sinon le bot refuse de démarrer : du capital inventé rendrait toute comparaison entre bras fausse » ; `arm.py bootstrap_arms` lève `ManifestError` ; 12 bras de test portent `capital_pct: 0.0`.
- **Fait :** `arm.py attach_portfolios` : « EN PAPER, … `capital_pct` n'est PAS appliqué » (chaque bras reçoit `capital_total` = 7 000 $, y compris ceux à `capital_pct: 0.0`). `notify` : `arm.py:91` « ne décide PLUS rien » mais `bootstrap_arms` lève `ManifestError` si la valeur est hors `NOTIFY_POLICIES`. `ETAT_DU_PROJET.md:205` dit « 1000 $ » (la valeur est 7 000 $ dans params.json).
- **Conséquence :** la contrainte de somme protège contre une comparaison que le champ ne peut pas fausser ; elle empêche en revanche d'activer un bras sans rééquilibrer des parts inutiles. Un lecteur croit que 12 bras « n'ont pas de capital ».
- **Correction :** documenter explicitement dans le manifeste que `capital_pct` n'est qu'un plan LIVE ; ne valider la somme qu'en mode LIVE.

### M7. Garde économique : le « win rate » utilisé est tantôt un taux d'atteinte du TP1 (a priori), tantôt le win rate P&L > 0 du bras
**Sévérité : MOYEN**
- **Fait :** `economics.win_rate_for` (`economics.py:103`) renvoie d'abord `arm_win_rate` (part de trades `pnl_usd > 0`, `main.py:1958`) dès 10 trades, sinon l'a priori « 15 % atteignent +100 % » (pics ≥ niveau). Or `evaluate`/`minimum_viable_tp` modélisent `P(atteindre TP1)`. Un trade peut être « gagnant » sans toucher TP1 (stop-profit à +10 % du tampon, voir H5) → le veto est calculé sur une probabilité qui n'est pas celle du modèle. `rest_pct = -2,9` (`economics.py:163`) est un a priori de 2026-08 (« mesuré via le stop breakeven ») alors que le stop breakeven vaut +tampon.
- **Conséquence :** veto/non-veto d'entrées sur une grandeur de nature différente de celle documentée (« 4 succès sur 26 »).
- **Correction :** utiliser le taux d'atteinte TP1 mesuré du bras (`peak_pct ≥ tp1` dans le journal), ou renommer la variable.

### M8. README / docstring de portefeuille annoncent des garde-fous « appliqués » que params.json désactive
**Sévérité : MOYEN** (affirmation de sécurité ; LIVE reste protégé par `_live_strict`)
- **Dit :** README l.70 « Garde-fous appliqués : … pause de 2 h après 3 pertes consécutives » ; `portfolio.py:6` « pause forcée de 2h après 3 pertes consécutives ».
- **Fait :** `config/params.json` `risk_rules.cooldown_hours: 0`, `max_drawdown_stop_pct: 0` (`_note` : « PAPER LEARNING MODE… AVANT tout passage LIVE : remettre 2 et 25 »). Le coupe-circuit de drawdown n'est mentionné ni dans README ni CONTEXT.
- **Conséquence :** en PAPER, aucune pause ni aucun coupe-circuit ; les statistiques de pertes consécutives (portfolio, `consecutive_losses` Telegram) n'ont aucun effet.
- **Correction :** une ligne dans README/CONTEXT « désactivés en PAPER (params.json), exigés en LIVE par `risk.py:_live_strict` ».

### M9. `live_mode_allowed` : évalué sur le seul témoin, et listé comme 3 critères dans toute la doc alors que le code en exige 5
**Sévérité : MOYEN**
- **Dit :** README l.74, CONTEXT, AGENTS, `main.py:6-8` : « 20 trades, WR > 40 %, PF > 1.5 ».
- **Fait :** `learning.py:1456-1506` ajoute (a) le verrou `live_mode_authorized_by_owner` (absent de params.json → toujours bloqué), (b) borne basse de l'IC95 du P&L/trade > 0. Et `self.learning` du bandeau et de `pre_trade_check` est celui du **témoin** (`main.py:~337`, `live_allowed` l.778, 1806, 2094) : le résultat du témoin déverrouillerait LIVE pour tous les bras. Le panneau « Passage en LIVE : autorisé/bloqué » ne dit pas de quel bras il parle.
- **Conséquence :** documentation sous-estime la rigueur (bon sens), mais le périmètre « un bras suffit » n'est écrit nulle part.
- **Correction :** documenter les 5 conditions et le périmètre baseline ; idéalement évaluer par bras.

### M10. Commandes Telegram : `/STATUS` ne décrit que le témoin sous le titre « RÉSUMÉ », `/STOP` peut laisser des positions ouvertes sans le dire
**Sévérité : MOYEN**
- **Fait :** `/STATUS` → `send_summary(self.portfolio.stats())` (`main.py:976`) = bras `baseline` seul ; « Capital » = `stats["capital"]` = **cash libre** (hors positions ouvertes) tandis que « P&L total » vient d'`equity()`. Même défaut que celui déjà corrigé côté dashboard (commentaire `TheBrain.tsx:104`). `/STOP` (`_panic_exit`, `main.py:978-1013`) envoie « fermeture de toutes les positions » puis, si le prix est introuvable, ne fait qu'un `print` « position NON fermée » (aucun message Telegram) et passe `self.paused = True` quand même ; l'utilisateur croit tout fermé.
- **Conséquence :** décision de laisser tourner/éteindre basée sur un résumé partiel ; position non fermée non signalée sur le canal où la commande a été donnée.
- **Correction :** `/STATUS` sur `_aggregate_stats()`, et message Telegram final listant les positions non fermées.

### M11. CONTEXT « Invariant 6 : aucun ajustement sans 10 échantillons dans le segment » est faux pour les relâchements
**Sévérité : MOYEN**
- **Fait :** `learning.py:610` applique `_relax_from_shadow()` avant même d'avoir `MIN_TRADES_PER_ARM` (15) trades ; `_relax_from_inactivity` (`:736`) ajuste un filtre ou le seuil alpha (jusqu'à 55) sans aucun échantillon de trades. `_adjust_exits/_adjust_risk` ne contrôlent que la taille globale (cadence), pas un segment. La docstring de tête (`learning.py:9`) répète la promesse.
- **Conséquence :** la garantie « jamais d'apprentissage sur du bruit » ne couvre que `_adjust_filters`.
- **Correction :** reformuler l'invariant (« resserrer ≥ 10 par segment ; relâcher sur preuve shadow/inactivité »).

### M12. Documentation d'état périmée (chiffres et mécanismes) en plusieurs endroits
**Sévérité : MOYEN**
- README : « boucle 90 s » (l.35, l.78, l.153) et « monitoring … toutes les 20 s » (l.153) vs `params.json` `interval_seconds: 45`, `monitor_interval_seconds: 5` ; « Flux d'un cycle » décrit `/token-profiles` + `/token-boosts` alors que la découverte est GMGN (`_note_gmgn` de params.json) ; « Non implémenté : … test honeypot » (l.258) alors que `jupiter.sellable` (`src/apis/jupiter.py:294`) + `main.py:1912` blacklistent déjà les honeypots ; l.42 « Twitter quota 60/15min » vs tableau l.~100 « 300 / 450 par 15 min ».
- `docs/ETAT_DU_PROJET.md` (« document autoportant », mis à jour 2026-09-02) : « 7 bras », « 1000 $ indépendants » (l.205), « API FastAPI en lecture seule » (l.15, alors que `POST /api/prediction/requests`, `api/server.py:550`, écrit).
- `api/server.py:8` « aucun endpoint ne déclenche de trade » (vrai) mais l.1 « lecture seule » (faux : une route d'écriture, ADR 017).
- `AGENTS.md:11` « les 791 tests » (1242 + 56 via le pont).
- Docstrings « sept stratégies / six bras » dans `main.py:812`, `main.py _fleet_exposure`, `arm.py`, `TheBrain.tsx` (19 bras actifs).
- **Conséquence :** un lecteur (humain ou IA, car ETAT se veut « copiable dans n'importe quelle IA ») reprend des chiffres de 2026-08/09 comme état courant.
- **Correction :** une passe de mise à jour ou une bannière « historique, voir CONTEXT » sur ETAT.

### M13. Code mort que la documentation présente comme actif
**Sévérité : MOYEN**
- `DevHistoryAgent.rejection_reason` (`src/agents/dev_history.py:200`) : jamais appelé ; seul `observe` (post-entrée, `main.py:2272`) tourne. La docstring décrit une table « REJETTE / passe » et `DEFAULT_REJECT_ABOVE = 45` comme une règle ; l'agent ne rejette rien.
- `Pipeline._enrich_social` (`src/pipeline.py:877`) : « conservé pour les appelants existants et les tests » — aucun appelant, aucun test.
- Parmi les fonctions publiques jamais référencées (ni src, ni tests) : `Journal.last_trade`, `scoreboard.build_from_logs`, `shadow.resume_summary`, `funnel.sampling_note`, `cache.watched_since/age_seconds`, `twitter.acceleration/author_diversity`, `dexscreener.search_pairs/get_token_data`, `birdeye.get_new_listings`.
- **Conséquence :** on croit qu'un filtre « créateur suspect » protège alors qu'il n'est que journalisé.
- **Correction :** brancher ou supprimer ; au minimum corriger la docstring de `dev_history`.

### M14. « BACKTEST … validées » dans les logs d'apprentissage = rejeu dans l'échantillon, sans le dire dans le message
**Sévérité : MOYEN**
- **Fait :** `learning.py:628-631` imprime « 🧠 BACKTEST | sorties validées (P&L/trade a → b) » ; `validate_exit_changes` rejoue sur les mêmes trades que ceux qui ont motivé le changement (README « Non implémenté » le reconnaît, pas les logs/Telegram `send_learning`). Le mot « validées » est lu comme une validation hors échantillon.
- **Conséquence :** un ajustement affiché comme validé est un résultat d'ajustement sur les données d'entraînement (cf. `chasse-au-lookahead`).
- **Correction :** « rejeu dans l'échantillon » dans le message ; A2 de la feuille de route.

---------------------------------------------------------------------------
## BAS
---------------------------------------------------------------------------

### B1. `scripts/README.md` : 15 scripts non documentés, et `mesure_glissement.py` affiche un mauvais nom de fichier
- Absents du README : `apply_chainstack_basic_auth.py`, `copy_fitness_9.py`, `copy_fitness_all.py`, `copy_robust_v2.py`, `export_vault_prediction.py`, `feature_coverage.py`, `gmgn_observe.py`, `helius_budget.py`, `prediction_demo.py`, `reconstruct_pnl.py`, `run_prediction.py`, `run_shortlist.py`, `run_ui.py`, `shadow_nobe.py`, `backup_daily.sh` (+ `scripts/launchd/`).
- `scripts/mesure_glissement.py:2,24-25` imprime « Ce script est remplacé par scripts_analyse_sorties.py … python3 scripts_analyse_sorties.py » : ce fichier n'existe pas (c'est `scripts/analyse_sorties.py`, `python -m scripts.analyse_sorties`). Il exécute quand même `main()` après le message.
- Les options documentées des scripts listés (`--heures`, `--bras`, `--sections`, `--seuil`, `--vault`, `--jours`, `--diff`, `--perf`, `--sans-envoi`) existent bien (vérifié par `add_argument`).

### B2. Noms de métriques Prometheus
`cryptobbot_arm_trades_total` est typée `gauge` (`metrics.py:99`) alors que `_total` est la convention des compteurs ; `cryptobbot_cycle_total` est typée `counter` mais c'est un numéro de cycle qui repart de zéro au redémarrage (`rate()` fausse).

### B3. Noms des paramètres de sortie
- `trailing_stop_distance_pct` (80) = recul en **points de P&L** depuis le pic (`positions.py:286-287` : `high_water_pct - pnl`), pas un % du prix ; le commentaire « actif seulement après TP2 ou +200 % » ignore `trailing_stop_activation` par bras (80 pour sniper).
- `partial_sell_tp1_pct` est une fraction de la position **initiale** (0-1), `partial_sell_tp2_pct` une fraction du **reste après TP1** (`positions.py:317`) ; les deux portent `_pct` mais valent 0,5.

### B4. « social_score »
`candidate_social_score` (`onchain_observer.py:213`) est un score de **présence de liens** (site/X/Telegram…), redéfini le 2026-10-10 (« remplace l'ancien social_score = mentions X ») ; les lignes de journal plus anciennes portent l'ancienne signification. `learning.py:581` `win_rate_by_social_score` segmente en réalité `entry_social_mentions_1h` ; `params.json learning.win_rate_by_social_score = {}`.

### B5. Rapport hebdo du dashboard : la courbe d'une semaine passée part de la mise initiale
`WeeklyReport.tsx` `equitySeries = [startingEquity, …]` avec `startingEquity = agg.equity − agg.total_pnl_usd` (mise de départ de toute l'histoire) alors que `stats.window_performance` démarre la fenêtre sur l'équité réelle au début de la fenêtre (docstring l.~486). Même chose pour le pourcentage hebdo (dénominateur fleet 19 × 7 000).

### B6. Commentaires « le témoin est GELÉ »
`TheBrain.tsx:104` « le bras témoin, qui est GELÉ » alors que `arm.py:525` `frozen=False` et la décision du 2026-08-03 (aucun bras gelé).

### B7. Description du bras `score_v2`
`config/strategies.json` : « NON déployé tant que Killian n'a pas validé » alors que `enabled: true` (le bras trade) ; même description : « Verdict vs baseline après ≥ 200 trades » — pas imposé par `verdict_vs_reference` (voir H1).

### B8. `pipeline.py:338` mature : `min_age_hours = float(filters.get("min_age_hours") or 24)`
Une valeur 0 est traitée comme absente (→ 24 h). Intentionnel pour le bras « mature » mais un `min_age_hours: 0` explicite est silencieusement remplacé.

---------------------------------------------------------------------------
## Contrôles faits sans trouvaille
- Tous les champs de `config/arms/*.json` / overrides de `strategies.json` de la famille `filters.*` GMGN (`max_bundler_rate`, `min_swaps`, `reject_wash_trading`, …) sont lus (pipeline.py ~977-1080, gmgn.py). Champs de manifeste `social_wishlist`, `discovery`, `min_confluence`, `bounds`, `learning`, `role`, `enabled` : lus. Clés de `config/prediction_arms.json` : toutes lues (runner/paper/overlay/learning).
- `api_budgets` 0 = illimité : confirmé par `load_budgets` (`budget.py` : `if limit and limit > 0`).
- `win_rate` : même définition partout (`pnl_usd > 0`) ; tests sans assertion : 4, tous des tests « ne lève pas » (nommage exact).
- `/RESUME`, `/STOP`, `/STATUS` existent bien ; panic button présent (voir M10 pour la réserve).
