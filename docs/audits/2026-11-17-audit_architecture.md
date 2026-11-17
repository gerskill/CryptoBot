> Rapport BRUT d'un auditeur (sous-agent, lecture seule), 2026-11-17. Les constats n'ont pas tous été re-vérifiés : la synthèse vérifiée est `docs/AUDIT_2026-11-17.md`. Aucun chiffre ici n'est un résultat de performance (journaux absents du dépôt).

# Audit — architecture de mesure et d'apprentissage de CryptobBot (PAPER)

Date : 2026-11-17 · Lecture seule · Sous-agent d'audit

**Fini quand :** chaque constat porte sa sévérité, sa preuve `fichier:ligne`, son
mécanisme, sa conséquence sur « quel bras est rentable » et une correction
minimale ; aucun fichier du dépôt modifié (`git status --short` vide, vérifié en
fin d'audit).

---

## 0. Cadre et limites

- **`data/` est absent de ce poste** (seul `data/skills/` existe) : aucun journal,
  aucune position, aucun shadow log. Toute ampleur mesurée sur les trades du bot
  est marquée « non vérifiable sans data/ ». Les comptages cités viennent des
  historiques d'ajustement suivis par git (`config/params.json`,
  `config/arms/*.json`, dernière synchro b691db4 du 15/11) et du code.
- Les **simulations** de l'annexe utilisent les fonctions du dépôt
  (`src/core/stats.py`, `LearningEngine.simulate_exits`) sur une **loi
  synthétique**. Ce sont des illustrations de mécanisme, **pas des résultats du
  bot**. Le sous-agent **juge** (`.claude/agents/juge.md`) n'a pas été appelé : ce
  sous-agent n'a pas d'outil pour lancer un agent. Si la session parente reprend
  ces chiffres, elle doit les faire juger ou les marquer [À TESTER].
- Déjà connu, **non re-signalé** : `_search_exits` choisit et valide sur les
  mêmes positions ; `_instrumented` non trié ; `walk_forward` d'`audit.py` non trié.
- `Journal.md` (Obsidian) et `HANDOFF.md` **non écrits** : mission en lecture seule,
  à faire par la session parente.
- « Pas de fuite trouvée » n'est pas « le gain est réel ».

---

## 1. Synthèse

| # | Constat | Sévérité | Nature |
|---|---|---|---|
| B1 | Une position sans prix utilisable n'est jamais fermée ni comptée ; le P&L latent n'est jamais compté | **BLOQUANT** | fuite (biais de survie, n°3) |
| B2 | Verdict « IC95 disjoints » : bootstrap dès 3 trades, faux verdicts fréquents, 18 comparaisons | **BLOQUANT** | défaut statistique |
| B3 | Référence mouvante et non contemporaine : témoin qui apprend, historiques entiers, forks « une variable » qui ont divergé | **BLOQUANT** | biais de comparaison |
| B4 | Un journal de bras = des dizaines de stratégies ; `params_version` figé à « 2.0 » | **BLOQUANT** | fuite n°6 |
| I1 | Filtres : ajustement et validation sur les mêmes trades | IMPORTANT | fuite n°1 (hors spec) |
| I2 | P&L en dollars : tailles non comparables entre bras et dans le temps | IMPORTANT | fragilité |
| I3 | Coût : devis manquant = 0, partielles nues, régimes mélangés, rejeu TP sans coût | IMPORTANT | fragilité, biais optimiste |
| I4 | Bras corrélés et interdépendants (même lot, plafond sectoriel par ordre du manifeste) | IMPORTANT | fragilité |
| I5 | Pool : emprunts dominés par `consensus`, doublons de trajectoires, censure | IMPORTANT | fuite n°5 |
| I6 | Rejeu : ordre pic/creux déduit des extrema globaux (« tranchés exactement » est faux) | IMPORTANT | fuite (grandeur de clôture) |
| I7 | Shadow : critère du pic sans stop ni coût, même preuve réutilisée sans fin | IMPORTANT | fragilité |
| I8 | `live_mode_allowed` : témoin seul pour tous les bras, test répété en continu, n = 20 | IMPORTANT | fragilité |
| I9 | Bras conçus après lecture des résultats ; hypothèses non comptées | IMPORTANT | fragilité (comparaisons multiples) |
| I10 | Spec A2 du 2026-11-17 : sept trous | IMPORTANT | — |
| I11 | 364 trades `excluded_from_learning` exclus aussi du capital et des stats | IMPORTANT | biais de sélection |
| M1 | Ordre de `read_positions()` = première jambe, pas `timestamp_exit` ; API un bras non triée | MINEUR | fragilité |
| M2 | Capital reconstruit au redémarrage perd les gains TP1 des positions ouvertes | MINEUR | fragilité |
| M3 | Stop affiché ≠ stop effectif : tous les bras sont à −15 % effectif | MINEUR | lecture trompeuse |
| M4 | Validation des sorties aveugle à `max_hold` et au trailing | MINEUR | fragilité |
| M5 | Agrégat flotte : 19 × 7 000 $, PF 0,0 vs None | MINEUR | lecture trompeuse |
| M6 | Documentation périmée (cadences, « six bras », « tranchés exactement ») | MINEUR | lecture trompeuse |

**Comptage : 4 BLOQUANT, 11 IMPORTANT, 6 MINEUR.**

---

## 2. Constats qui invalident une conclusion de rentabilité (BLOQUANT)

### B1 — Biais de survie : une position sans prix reste ouverte indéfiniment et n'entre dans aucune statistique

**Preuves**
- `src/main.py:1491-1497` : prix inutilisable → `continue` (« position gardée »).
  `evaluate_exits` n'est pas appelé.
- `src/core/positions.py:274-282` : le time stop est **dans** `evaluate_exits`.
  Sans prix, ni stop loss ni time stop : aucune borne de durée.
- `src/main.py:999-1004` : même `/STOP` laisse ces positions ouvertes.
- `src/core/journal.py:303, 329-331` : `read_positions()` ignore toute position
  sans jambe finale. C'est la source de WR, PF, P&L, verdict, apprentissage,
  `live_mode_allowed`, dashboard, API, rapport hebdo.
- `src/core/portfolio.py:951-954, 968` : `equity()` compte les positions
  ouvertes à leur valeur d'**entrée** ; `total_pnl_usd = equity − baseline`.
  Le P&L latent n'est compté nulle part.
- Incident documenté `src/main.py:1600-1602` : « 7 positions bloquées à "prix
  indisponible" depuis leur ouverture … l'une d'elles réellement à -99 % ». Le
  repli Jupiter réduit la fréquence, pas le mécanisme : aucune purge n'existe
  (recherche `zombie|stale|orphelin` dans `src/` : rien sur les positions).
- Second chemin : `src/core/portfolio.py:643-668`. Si l'écriture de la jambe
  finale échoue, la position quitte le portefeuille et le capital encaisse, mais
  le journal n'a pas de jambe finale : les jambes TP1 déjà écrites restent
  orphelines et la position est invisible pour toujours.
- Incohérence associée : les jambes TP1/TP2 d'une position encore ouverte sont
  dans le capital (`portfolio.py:634`) mais pas dans WR/PF/trades.

**Combien de temps une position peut rester ouverte ?** Avec un prix : au plus
`max_hold_time_minutes`, borne 30–1440 (`learning.py:167`), 330 à 660 min
aujourd'hui selon le bras. Sans prix : **sans limite**.

**Mécanisme.** Les tokens qui perdent leur prix sont surtout les rugs, les pools
vidés et les paires migrées, donc majoritairement des perdants. Ils sortent du
dénominateur et du P&L, et occupent un emplacement `max_concurrent_positions`.

**Conséquence.** WR, PF, P&L/trade et verdict sont surestimés, d'autant plus pour
les bras qui achètent des tokens fragiles. Un bras peut paraître rentable parce
que ses pires positions sont « encore ouvertes ». Ampleur : **non vérifiable sans
data/** (compter dans `data/**/positions*.json` les positions dont l'âge dépasse
`max_hold_time_minutes`).

**Correction minimale.**
1. Évaluer le time stop même sans prix : au-delà de `max_hold_time_minutes`
   (plus une marge), clôture forcée `NO_PRICE_TIMEOUT` au dernier prix utilisable
   connu, ou à −100 % si aucun, avec une marque dans le journal.
2. Publier par bras le nombre de positions en retard et un P&L « au pire »
   (positions ouvertes au dernier prix connu, −100 % si inconnu) à côté du réalisé.
3. Test : position sans prix au-delà de `max_hold` → clôturée et comptée.

### B2 — Verdict contre le témoin : bootstrap dès 3 trades, « IC disjoints » qui ne tient pas sa promesse, 18 comparaisons

**Preuves**
- `src/core/stats.py:110` : un intervalle est rendu dès n = 3.
  `stats.py:113-125` : bootstrap percentile simple. L'intervalle reste dans
  [min, max] de l'échantillon : 3 ou 4 pertes donnent toujours un IC entièrement
  négatif.
- `stats.py:149-153` affirme : seuil « d'environ 0,005 par comparaison », « sur
  six bras … faux positif sous 3 % ». Le manifeste compte **19 bras actifs, 18
  comparés au témoin** (`src/main.py:904-906`, `api/server.py:373-376`,
  `scripts/analyse_sorties.py:327`). `scripts/audit.py:124-134` fait les 171
  comparaisons deux à deux, sans correction, dès 3 trades (simple avertissement
  sous 15).
- ADR 011 : un verdict « inférieur » suffit à désactiver un bras. Précédent :
  `narrative` désactivé sur **4 trades** (commit 216a75f, IC [−11,54 .. −7,56]),
  exactement le cas où le percentile ne peut rendre qu'un IC négatif.
- Simulation (annexe A1, loi synthétique identique pour le bras et le témoin,
  n_témoin = 300) : probabilité d'un verdict concluant **à tort** = 31 % (n = 3),
  29 % (n = 4), 21 % (n = 5), 12 % (n = 8), 9 % (n = 15), 4,7 % (n = 30), 1 %
  (n = 60). Presque toujours « inférieur » (23 % à n = 4, 7,7 % à n = 15 avec
  2 000 itérations). Couverture réelle de l'IC95 : 72 % (n = 5), 84 % (n = 15),
  86 % (n = 30), 89 % (n = 60).

**Mécanisme.** Sur une loi asymétrique (beaucoup de petites pertes, rares gros
gains), un petit échantillon manque les gros gains : sa moyenne est basse et son
intervalle trop étroit. Le témoin, long, a un IC étroit. Les deux intervalles se
séparent, et le bras est déclaré « inférieur ».

**Conséquence.** Un bras peut être déclaré inférieur, donc désactivé, par
hasard ; plus rarement supérieur. Avec 18 bras vers 15 trades, au moins un faux
verdict est probable (82 % sous indépendance ; les bras ne sont pas indépendants,
cf. I4, mais l'ordre de grandeur reste « probable »).

**Correction minimale.**
1. Aucun verdict sous 30 trades par bras, via une constante unique partagée par
   `verdict_vs_reference`, `audit.py` et l'API.
2. Tester la **différence** sur la même fenêtre (bootstrap par blocs journaliers
   de la différence des moyennes) au lieu de deux IC disjoints.
3. Correction de Holm sur le nombre de comparaisons réellement faites.
4. Corriger `stats.py:149-153` et l'ADR 011. Test : 4 pertes contre un témoin
   dont l'IC est proche de 0 → « échantillon trop court ».

### B3 — Référence mouvante et non contemporaine

**Preuves**
- `src/main.py:879-907` et `scripts/analyse_sorties.py:307, 327` comparent
  **tout** l'historique du bras à **tout** l'historique du témoin. L'API compare
  sur la même fenêtre (`api/server.py:352-376`), mais `window=all` retombe dans
  le même cas.
- Le témoin n'est pas gelé (`src/core/arm.py:512-525`) : 716 ajustements depuis
  le 2026-07-30, dont 200 depuis le 2026-10-04 et 59 sur `min_liquidity_usd` ou
  `max_age_hours` (historique de `config/params.json`).
- Les bras « fork exact du témoin, une seule variable » ont été copiés à des
  dates différentes d'un témoin qui a continué de bouger. Écart actuel au
  témoin :
  - `tp1_15` et `tp1_20` : 8 paramètres, dont `min_liquidity_usd` 40 000 contre
    60 000, `max_age_hours` 20,5 contre 16, `max_hold_time_minutes` 330 contre
    420, `breakeven_trigger` 100 contre 10 ;
  - `tp1_20_vl2` : 9 ; `anti_rug` : 13 (dont `min_liquidity_usd` 80 000) ;
  - `score_v2` : 3 (`min_liquidity_usd` 65 000, `max_age_hours` 18).
- Les commentaires `src/main.py:1714` et `:1739` (« sous-ensemble exact du
  témoin ») sont faux : `score_v2` accepte des tokens de 16 à 18 h que le témoin
  refuse.
- `sniper_young`, « une seule variable : min_age_hours », référence `sniper`
  (`config/strategies.json:113`) : aujourd'hui `min_age_hours` est identique
  (3,0 et 3,0) et 7 autres paramètres diffèrent (SL −30/−25, tampon 15/10, TP1
  70/90, `min_liquidity_usd` 10 000/40 000, `max_hold` 660/420…). `sniper_ar`,
  « copie COMPLÈTE de sniper » (`strategies.json:70`) : TP1 70/85, `max_hold`
  660/630.
- `config/strategies.json:13` annonce un témoin aux « fichiers data/ historiques
  continus depuis avant le multi-bras », alors que le commit 24741e5 (2026-08-06)
  dit les journaux « archivés localement ». Lequel est vrai : **non vérifiable
  sans data/**.

**Mécanisme.** La comparaison mêle l'effet de la règle testée, la dérive du
témoin, la période de marché, le régime de coût (I3) et le régime de taille (I2).

**Conséquence.** « tp1_20 bat le témoin » ou « anti_rug n'apporte rien » ne
mesure pas la variable annoncée. Aucune paire A/B du manifeste n'est aujourd'hui
un A/B.

**Correction minimale.**
1. Comparer partout sur la fenêtre commune [création du bras, maintenant]
   (`main.py`, `analyse_sorties.py`), et refuser `all` pour un verdict.
2. Pour un test « une variable », comparer à une copie **gelée** du témoin prise
   au moment du fork (bras `role: test`, `learning: false`), pas au témoin
   vivant.
3. Afficher à côté de chaque verdict l'écart de paramètres courant entre le bras
   et sa référence.

### B4 — Un journal de bras mélange des dizaines de stratégies, et `params_version` ne permet pas de les séparer

**Preuves**
- `src/core/portfolio.py:409` : `params_version = str(params.get("version", ""))`.
  `src/core/params.py:42-80` : `set()` n'incrémente jamais `version`. Tous les
  documents valent « 2.0 » (`config/params.json:2` et les 18 `config/arms/*.json`).
  La parade de la fuite n°6 décrite dans `chasse-au-lookahead` est donc
  inopérante.
- Ajustements journalisés : baseline 716, runner 458, sniper 440, consensus 429,
  scalp 424, sniper_ar 394 (hérités de sniper), quality 370, sniper_young 216 ;
  0 pour les 11 bras `learning: false`. Après la décision « apprentissage lent »
  du 2026-10-11, il y en a encore de 25 à 120 par bras (sniper : 120).
- Dérive entre le manifeste et l'état actuel :
  - `sniper`, décrit « 1m30 à 30 min » : `min_age_hours` 0,025 → 3,0,
    `max_age_hours` 0,5 → 21, liquidité 4 000 → 10 000, SL −40 → −30,
    TP1 40 → 70, `max_hold` 30 → 660 ;
  - `quality`, « 4 h à 24 h, sélection stricte » : `min_age_hours` 4 → 0,
    liquidité 40 000 → 5 000, holders 300 → 50, smart money 2 → 0 ;
  - `scalp`, « 30 min à 2 h » : `max_age_hours` 2 → 20, `max_hold` 60 → 450 ;
  - `runner`, « 1 h à 6 h » : `max_age_hours` 6 → 23,5.
- `risk_per_trade` est passé de 0,03 à 0,01 entre le 2026-07-31 et le
  2026-08-07 (témoin), de 0,025 à 0,01 sur les autres bras.

**Mécanisme.** Stats, verdict, apprentissage et dashboards lisent tout le journal
(`read_positions()`) sans notion de régime. Côté apprentissage, les segments de
`refresh_stats` (`learning.py:561-585`) et les positions propres de
`_instrumented` (`learning.py:1338-1346`) rejugent les réglages d'aujourd'hui sur
des trades faits sous d'autres réglages.

**Conséquence.** « sniper est rentable » décrit une moyenne de configurations dont
la plupart n'existent plus ; le nom et la description du bras ne disent pas ce
qu'il trade. Les fenêtres 7 j / 30 j réduisent le mélange sans l'éliminer.

**Correction minimale.**
1. `ParamsStore.set(log=True)` incrémente un `params_revision` (ou un hash des
   sections `filters`, `exit_rules`, `entry_rules`, `scan`, `risk_per_trade`) ;
   `Position.params_version` le porte.
2. Statistiques par bras « depuis la dernière révision », avec le nombre de
   révisions dans la fenêtre affichée.
3. Annoter ou renommer les bras dont la fenêtre a dérivé hors de leur
   description.

---

## 3. Fragilités et fuites de second rang (IMPORTANT)

### I1 — Filtres : l'ajustement et sa validation lisent les mêmes trades (fuite n°1, absente de la spec)

**Preuves.** `learning.py:561-585` calcule les segments sur **toutes** les
positions ; `:662-705` resserre si le WR d'un segment est sous 30 % ou 25 % sur
au moins 10 trades ; `:1057-1064` valide sur les 50 dernières positions **du même
journal** ; `:1027-1047` montre que `simulate_filters` ne peut que retirer des
trades.

**Mécanisme.** On détecte un segment perdant sur l'historique, on monte le seuil
qui l'exclut, puis on « valide » en retirant ces mêmes perdants des 50 derniers
trades : l'amélioration est mécanique. Inversement, un relâchement (shadow ou
inactivité, inclus ligne 707) n'ajoute aucun trade au rejeu : gain nul, donc
annulé dès que la référence est non nulle (`:1075-1084`). Le témoin compte 97
annulations de `filters`. Les messages « filtres validés (P&L/trade a → b) » sont
des chiffres mesurés sur l'échantillon d'apprentissage.

**Conséquence.** « Ce resserrage a amélioré le bras » n'est pas démontré, et ces
validations alimentent la dérive de B4. La spec écarte les filtres au motif
« seuil unique, pas de grille » (`spec:35-36`) ; c'est inexact : 4 segments et 3
seuils sont testés à chaque cadence, plus les relâchements, sur un historique
cumulatif.

**Correction.** Calculer les segments sur les 2/3 anciens et valider sur le 1/3
récent ; ou appliquer, puis juger sur les N trades **suivants** avec retour
arrière. Au minimum, écrire « dans l'échantillon » dans le message.

### I2 — P&L en dollars : tailles non comparables

**Preuves**
- `portfolio.py:348-357` : taille = capital courant × `risk_per_trade` × facteur
  Kelly (1 + WR − 0,40 dès 20 trades si WR > 40 %), plafonnée à 5 %.
  `main.py:2047-2052` lui passe le WR de **tout** l'historique du bras.
- `main.py:1924-1930` : `size_for_cost` réduit la taille selon le carnet.
- `arm.py:339-353` : `capital_pct` n'est pas appliqué en PAPER ; chaque bras
  reçoit `capital_total` = 7 000 $. Avant le 2026-08-06, `capital_total` valait 0,
  d'où 1 000 $ fictifs (`main.py:105, 390-398`, commit 24741e5).
- `risk_per_trade` 0,03 → 0,01 (B4) ; tous les bras sont aujourd'hui au plancher
  0,01.
- `scaled_be` engage 1/3 de la taille tant que l'ajout n'a pas eu lieu
  (`portfolio.py:359-366`).
- Verdict, PF et LIVE sont calculés en `pnl_usd` (`stats.py:134-135`,
  `learning.py:1472-1476`).

**Mécanisme.** La taille varie selon l'époque (≈ 30 $ à 1 000 $ × 3 %, ≈ 70 à
175 $ ensuite) et selon le bras. Le capital composé et le Kelly calculé sur le
vécu amplifient les bras chanceux.

**Conséquence.** Le P&L/trade en dollars et son IC dépendent de l'époque et du
capital autant que de la stratégie ; le PF d'un historique long pondère surtout
les grosses mises. Le WR n'est pas touché ; PF et P&L le sont. Les lignes
antérieures au 2026-08-06 sont-elles encore dans les journaux ? **Non vérifiable
sans data/.**

**Correction.** Comparer en rendement sur mise (`pnl_pct` de `read_positions`,
déjà pondéré par jambe) ou à mise fixe (P&L × 100 / `position_size`) ; pour
`scaled_be`, rapporter à la taille **planifiée**. Désactiver le facteur Kelly
pendant la phase de mesure.

### I3 — Modèle de coût hétérogène

**Preuves**
- Devis de sortie absent → coût 0 et prix nu : `exit_fees.py:109-110`,
  `exit_fees.py:192-193`, `portfolio.py:621-626`.
- Jambes partielles au prix nu tant que le bras a moins de 10 mesures
  (`exit_fees.py:163, 222-224`). L'estimateur est **par bras**
  (`arm.py:378-380`), donc chaque bras neuf ou fork démarre optimiste sur ses
  TP1/TP2.
- `RUG_PULL` : sortie au prix observé après −50 % de liquidité ; sur un pool vidé,
  le devis échoue souvent (coût 0) ou est plafonné à 100 %
  (`positions.py:324-342`).
- Régimes mélangés dans un même journal : pas de coût avant l'ADR 009
  (2026-08-06), partielles nues jusqu'à l'ADR 012 (2026-08-31), aller-retour
  complet sur la vente avant buy-leg-v1, erreur d'échelle corrigée à la lecture
  avant le 2026-10-24 (`journal.py:20, 44-62`), plancher −100 % à la lecture
  (`journal.py:28-41`).
- Rejeu : les jambes TP sont sans coût (`learning.py:1187, 1231-1239`), alors que
  les sorties SL (`slip`, mesuré sur des P&L de journal coût compris) et les
  positions `UNCHANGED` (P&L réalisé) portent le coût. Les réglages qui
  convertissent des positions en sorties TP sont favorisés.

**Conséquence.** Les bras sur tokens fragiles (plus de devis ratés, plus de rugs)
et les bras récents paraissent meilleurs ; les comparaisons entre périodes sont
faussées. `position_stats` publie `exit_cost_coverage_pct` (`stats.py:463, 473`),
mais aucun verdict n'en tient compte.

**Correction.** Devis manquant → médiane mesurée du bras, marquée
`exit_cost_estimated` ; sous 10 mesures, médiane de la flotte. Comparer
seulement des lignes du même régime de coût. Afficher la couverture de coût à
côté du verdict. Dans le rejeu, déduire la médiane de coût sur les jambes TP.

### I4 — Bras corrélés et interdépendants

**Preuves**
- Même lot de candidats pour tous (`main.py:578-626`). Le lot est plafonné à 25,
  avec priorité à la watchlist **union** des bras (`pipeline.py:291, 634-642`).
  L'enveloppe de découverte prend les filtres les plus lâches de tous les bras
  (`pipeline.py:188-211`) : ajouter un bras, ou en laisser un dériver, change ce
  que voient les autres, témoin compris.
- Le plafond sectoriel de flotte est évalué candidat par candidat **dans l'ordre
  du manifeste** (`main.py:505-513, 1038-1052`, `risk.py:83-97`,
  `correlation.py:388-400`) : les premiers bras (baseline, sniper) consomment le
  plafond, les derniers (quality, consensus) sont refusés. Les bras de test sont
  soumis au plafond sans le consommer.
- `consensus` n'entre que si les votants acceptent (`main.py:1764-1788`) ; les
  forks partagent les entrées du témoin.
- `_aggregate_stats` (`main.py:837-862`) compte N fois le même token dans le WR
  et le PF de flotte.
- Le bootstrap suppose des trades i.i.d. (`stats.py:116-118`), alors que les
  trades d'un bras se regroupent par jour et par meta : IC trop étroits.
  `audit.py:217-248` ne mesure qu'un recouvrement de tokens (Jaccard), pas une
  corrélation de P&L.

**Conséquence.** Les 19 expériences ne sont pas indépendantes ; un même pump peut
faire « gagner » plusieurs bras ; la performance d'un bras dépend de la
composition de la flotte, ce qui empêche le témoin de servir de référence stable.
Ampleur **non vérifiable sans data/** (refus « concentration meta » par bras dans
l'entonnoir, recouvrement token × heure).

**Correction.** Bootstrap par blocs journaliers ; WR/PF de flotte dédupliqué par
token ; recouvrement token × fenêtre d'une heure entre bras ; plafond sectoriel
évalué dans un ordre tournant pour supprimer la priorité fixe.

### I5 — Pool de trajectoires : emprunts dominés par `consensus`, doublons, censure (fuite n°5)

**Preuves**
- `arm.py:273-282` concatène les `read_positions()` des autres bras dans l'ordre
  du manifeste (`arm.py:426` : baseline, sniper, sniper_young, scalp, runner,
  quality, consensus).
- `learning.py:1359` garde `empruntes[-window:]`, c'est-à-dire la **queue de la
  concaténation** : les 50 positions empruntées viennent d'abord de `consensus`
  (dernier bras du pool), puis de `quality`. Ce ne sont pas « les plus
  récentes ».
- Déduplication sur `position_id` seulement (`learning.py:1350, 1356`) : un
  token acheté au même moment par sniper et par consensus entre deux fois dans le
  rejeu de sniper.
- Positions propres jamais filtrées par régime (`learning.py:1338-1346`).
- Une position empruntée « UNCHANGED » rend le `pnl_usd` du prêteur, avec sa
  taille, son coût et sa sortie (`learning.py:1244-1248`) ; sa trajectoire est
  censurée par les sorties du prêteur.
- Tous les ajustements `_search_exits` récents portent « rejeu sur 100
  positions » (50 propres + 50 empruntées).

**Conséquence.** Les sorties de chaque bras sont recalibrées surtout sur les
trades de consensus et sur des doublons : les bras convergent (B4) et deviennent
plus corrélés (I4) ; les tokens consensuels sont surpondérés.

**Correction.** Trier le pool par `timestamp_exit` avant la coupe ; dédupliquer
sur (`token_address`, entrée à ±5 min) ; filtrer aussi les positions propres par
régime ; ramener les positions empruntées à une mise fixe.

### I6 — Rejeu : l'ordre pic/creux est déduit des extrema globaux

**Preuves.** `positions.py:390-412` mémorise l'instant du plus haut et du plus bas
**globaux**. `learning.py:1142-1145` en déduit « TP avant SL ». La docstring
`learning.py:1175-1176` dit ces positions « tranchées exactement » ; c'est faux :
l'ordre qui compte est celui du **premier franchissement** de chaque seuil.

**Démonstration (annexe A3).** Trajectoire : −16 % à 2 min, +200 % à 30 min,
−60 % à 200 min. `simulate_exits` avec un stop effectif à −15 % et un TP1 à
+150 % rend « TP1, +51,49 $ ». En réel, le stop sort la position dès la 2e minute.

**Mécanisme.** Le biais est optimiste quand on rejoue un stop plus serré que celui
sous lequel la trajectoire a été enregistrée (trajectoires empruntées à un bras
plus large, anciens trades à SL −40) et que le creux global suit le pic (profil
pump puis dump, courant sur memecoin). Il est pessimiste dans le cas symétrique
côté TP.

**Conséquence.** `exit_grid`, `_search_exits`, `validate_exit_changes`, la spec
A2 et le walk-forward d'`audit.py` héritent du biais ; un hold-out ne le corrige
pas.

**Correction.** Journaliser l'instant du premier franchissement de niveaux fixes
(par ex. −5 à −40 et +15 à +150) dans `update_water_marks`. En attendant, compter
comme « dégradée » toute position où le rejeu voit franchis à la fois le SL et
le TP.

### I7 — Shadow : critère du pic, même preuve réutilisée sans fin

**Preuves**
- `shadow.py:57-60` : `would_have_won` = pic ≥ +100 % sur 4 h, sans stop, sans
  coût, sans ordre.
- `shadow.py:333-359` : tout l'historique est lu, jamais fenêtré ni consommé.
- `learning.py:598-617` : relâchement appelé à chaque trade clos tant que le bras
  a moins de 15 trades ; `:707` : et à chaque cadence.
- Historique du témoin : 111 relâchements de `min_age_hours` justifiés par la
  même phrase (« 25,0 % des rejets 'age_min' auraient fait +100 % ») ; oscillation
  0 → 0,5 → 0 **dans la même minute** (resserrage par segment puis relâchement
  par shadow), répétée jusqu'au 2026-11-11.

**Conséquence.** Le shadow compte des tokens qui ont touché +100 % à un moment,
y compris après un −50 % qui aurait sorti la position ; il pousse les filtres à
se relâcher par crans sur une preuve figée, ce qui alimente B4. Effet sur la
rentabilité : indirect.

**Correction.** Rejouer les rejets avec les règles de sortie du bras (stop
effectif atteint avant +100 % = perdu) ; ne compter que les verdicts postérieurs
au dernier changement du paramètre visé ; au plus un relâchement par famille et
par cadence.

### I8 — `live_mode_allowed` : sens statistique faible

**Preuves.** `learning.py:1456-1506`. Seul le témoin est évalué
(`main.py:337-338`), mais le résultat sert à **tous** les bras
(`main.py:1806-1810, 2094-2098`). Il est recalculé à chaque écriture d'état
(`main.py:778`) et à chaque tableau de bord (`main.py:2363`), sur tout
l'historique, en dollars (I2), avec un bootstrap à n = 20 (couverture ~80 %, B2).
WR > 40 % et PF > 1,5 sont des points, sans intervalle.

**Mécanisme.** Un critère vérifié en continu est un arrêt optionnel. Simulation
(annexe A2), loi d'espérance ≈ +0,4 $/trade (≈ 0,5 % de la mise, absorbé par les
coûts) : 2,2 % de passage pour un test unique à n = 20 ; 9 % si on vérifie après
chaque trade jusqu'à n = 50 ; 16 % jusqu'à n = 200.

**Conséquence.** Le jour où le critère passe ne prouve rien, et il autoriserait
tous les bras sur le vécu d'un seul. Le verrou du propriétaire et l'absence de
module d'exécution rendent le risque actuel nul.

**Correction.** Critère par bras, sur la fenêtre depuis la dernière révision de
paramètres (B4), au moins 100 trades, borne basse d'un IC par blocs supérieure au
coût estimé ; vérification à date fixe, ou test séquentiel avec dépense d'alpha.

### I9 — Bras conçus après lecture des résultats

**Preuves.**
- `config/strategies.json:70` (`sniper_ar`) : « sans eux PF 0.70 → 0.73 », mesuré
  sur l'historique de sniper.
- `:292` (`tp1_20_vl2`) : « Audit 24/10 : vol/liq 2-5 = PF 0.59 ».
- `:206` (`quality`) : « la tranche 2-4h est la SEULE rentable ».
- `:516` (`score_v2`) : seuil = médiane d'entraînement.
- `:113` (`sniper_young`) : créé après un premier essai `runner_young`
  abandonné.
- Commits 216a75f et 1a56f75 (`narrative`) ; « corrections » de `fixes3` et
  `consensus_af` (`:420-474`).
- `copy_smart` : wallets triés sur leur réussite passée, repli sur « les
  meilleurs » quand trop peu passent (`copy_smart.py:101-114`) ; « réussite » =
  pic > 0,01 % (`copy_smart.py:52-54`).

**Mécanisme.** Chaque bras est une hypothèse choisie après avoir vu les données.
Ses trades futurs sont hors échantillon (bon point), mais choisir « le meilleur »
parmi au moins 21 hypothèses (19 actives, plus narrative et runner_young) n'est
corrigé nulle part. Les chiffres de motivation mesurés dans l'échantillon (PF
0,73, PF 0,98) seront rattrapés par la régression vers la moyenne.

**Correction.** Tenir un registre des hypothèses (date, critère de succès, trades
prévus, fenêtre) ; juger un bras sur ses seuls trades postérieurs à sa création ;
appliquer la correction de multiplicité au nombre d'hypothèses du registre, pas
seulement aux bras actifs.

### I10 — Ce que la spec A2 (2026-11-17) laisse passer

Spec : `docs/superpowers/specs/2026-11-17-hors-echantillon-parametres-morts-test-rendu-design.md`.

- **(a) Le couple appliqué n'est pas le couple évalué.** `_limit_step` (±15 pts
  sur TP1, ±2 pts sur SL ; `learning.py:56-58, 394-408`), le plafond du stop
  effectif (`:432-437`) et la règle TP1 < TP2 (`:438-447`) modifient la valeur
  après l'évaluation. Avec un tampon de 10 à 15, les SL −10/−15/−20 de la grille
  sont inapplicables, mais la grille les évalue avec un stop effectif entre 0 et
  +5 %. Historique : `_search_exits` a changé le SL de 0 à 6 fois par bras et
  TP1 de 25 à 52 fois. Le TP1 appliqué a donc été choisi avec un SL jamais
  appliqué. A2.4 rejoue « le couple choisi » : il faut rejouer sur le test les
  valeurs qui **seront** appliquées, et retirer de la grille les SL inapplicables.
- **(b) `validate_exit_changes` reste dans l'échantillon et juge le paquet**
  `_adjust_exits` + `_search_exits` (`learning.py:638-647, 1431-1436`). Un
  changement confirmé hors échantillon peut être annulé dans l'échantillon, et un
  changement de tampon, de SL ou de time stop non prouvé passe avec lui. Ce
  rejeu ne voit ni le time stop ni le trailing (`:1179-1183`) : sur sniper, 41 des
  65 changements de `max_hold_time_minutes` ont été annulés dans la même minute.
- **(c) `measured_breakeven_rest` lit tout le journal du bras**
  (`learning.py:1116`), y compris les jambes du tiers de test (et, dans
  `audit.py:163-170`, celles des plis de test) : le test fuit vers
  l'apprentissage. Il applique aussi le « reste » du bras emprunteur à des
  trajectoires d'autres bras. À estimer sur la tranche d'apprentissage seule.
- **(d) Doublons entre apprentissage et test.** Le même token, avec la même
  entrée, peut être propre d'un côté et emprunté de l'autre (I5) : le test n'est
  pas indépendant. Dédupliquer avant de couper.
- **(e) Seuil relatif à une référence proche de zéro** (point 3, `spec:274-276`).
  Références observées : « −0.2794 → −0.0678 », « −0.1742 → 0.0602 ». 10 % de
  quelques centimes : un gain de bruit confirme. Exiger un gain **apparié** par
  position (mêmes positions, deux règles) dont l'IC bootstrap exclut 0, ou un
  plancher absolu.
- **(f)** Le biais d'ordre des extrema (I6) et les emprunts dominés par consensus
  (I5) ne sont pas corrigés par un découpage chronologique.
- **(g)** « 50 dernières lignes écrites, donc quasi chronologique »
  (`spec:281-284`) est faux pour le pool (I5) et approximatif pour le bras
  (ordre de première jambe, M1). Les filtres restent sans hold-out (I1), et le
  mélange de régimes (B4) n'est pas traité.

### I11 — 364 trades exclus aussi du capital et des statistiques

**Preuves.** `journal.py:308-315, 332-333` ; `docs/ETAT_DU_PROJET.md:736` (364
trades de l'incident du 2026-08-06, marqués à la main ; aucun code ne pose ce
drapeau).

**Mécanisme.** Des pertes produites par les paramètres réellement en vigueur
(stop effectif positif) sont retirées de `read_positions()`, donc du capital, du
WR, du PF et du verdict, pas seulement de l'apprentissage.

**Conséquence.** L'historique « tout » de consensus, quality, scalp et runner est
flatté pour cette journée. Ces lignes sont-elles encore dans les journaux actifs
(commit 24741e5 : « archivés localement ») ? **Non vérifiable sans data/.**

**Correction.** Séparer `excluded_from_learning` d'un `excluded_from_performance`
distinct ; par défaut, compter ces trades dans le P&L avec la mention
« incident ».

---

## 4. MINEUR

- **M1 — Ordre de `read_positions()`.** Les positions sont rangées selon leur
  première jambe écrite (`journal.py:317-324`), pas selon `timestamp_exit`.
  `api/server.py:176-183` ne trie pas le cas « un bras » malgré sa docstring
  (`:157-158`), alors que `window_performance` exige un tri (`stats.py:491`).
  Sont touchés : le drawdown de fenêtre, la série de pertes consécutives
  (`portfolio.py:213-219`) et la fenêtre `[-50:]` (`learning.py:1059`).
  Correction : trier par `timestamp_exit` dans `read_positions()`.
- **M2 — Capital au redémarrage.** `portfolio.py:174-189` : le P&L réalisé
  ignore les jambes TP1 des positions encore ouvertes, et `engaged` ne retire
  que la part restante. Le gain TP1 disparaît du capital jusqu'à la clôture,
  alors qu'il y était avant le redémarrage. La taille des positions suivantes et
  l'equity affichée sont faussées pendant ce temps. Correction : reconstruire le
  capital depuis `read_all()`.
- **M3 — Stop affiché ≠ stop effectif.** Tous les bras sont au stop effectif
  −15 % (SL + tampon : baseline −25 + 10, sniper −30 + 15, scalp −17,2 + 2,2,
  quality −17,6 + 2,6, consensus −21 + 6 ; `learning.py:216`). `arm.summary()`
  (`arm.py:194-207`) et le dashboard affichent `stop_loss_pct`, donc des
  différences de stop qui n'existent pas. Correction : publier
  `effective_stop_loss_pct`.
- **M4 — Validation des sorties aveugle aux paramètres qu'elle ne sait pas
  rejouer** (`learning.py:963-984` contre `:1179-1183`) : 41 des 65 changements
  de `max_hold` annulés sur sniper ; les autres passent sans preuve. Correction :
  juger ces paramètres sur les trades suivants.
- **M5 — Agrégat flotte.** `total_pnl_usd` et `equity` somment 19 portefeuilles
  de 7 000 $ (`main.py:853-855`) alors que `capital_pct` somme à 1. Le profit
  factor vaut 0,0 quand il n'y a aucune perte (`portfolio.py:972`,
  `main.py:862`), contre `None` dans `stats.py:472`.
- **M6 — Documentation périmée.**
  `.claude/skills/apprentissage-garde-fous/SKILL.md:26, 47` (cadences 5/10/20,
  `BACKTEST_WINDOW` 20) contre `learning.py:40-45, 133` (50 et 50) ;
  `stats.py:149-153` (« six bras ») ; `learning.py:1175-1176` (« tranchés
  exactement »).

---

## 5. Corrections, par ordre de priorité

1. **B1** — time stop sans prix et P&L « au pire » publié par bras.
2. **B4** — `params_revision` sur chaque position ; statistiques par régime.
3. **B2 et B3** — pas de verdict sous 30 trades ; test de différence apparié sur
   la même fenêtre, par blocs ; correction de Holm ; copie gelée du témoin comme
   référence des forks.
4. **I2** — comparaisons en rendement sur mise ou à mise fixe.
5. **I10 et I1** — compléter la spec A2 (points a à g) et ajouter un hold-out
   aux filtres.
6. **I3** — devis manquant remplacé par la médiane mesurée, au lieu de 0.
7. **I5 et I6** — pool trié et dédupliqué ; journalisation des premiers
   franchissements.

---

## Annexes

### A0 — Ajustements journalisés par bras (`learning.parameter_adjustment_history`)

| bras | total | annulations (`filters` / `exit_rules`) | depuis le 2026-10-11 | inactivité | shadow | rejeu `_search_exits` |
|---|---|---|---|---|---|---|
| baseline | 716 | 145 | 96 | 40 | 119 | 40 |
| runner | 458 | 130 | 52 | 21 | 23 | 40 |
| sniper | 440 | 100 | 120 | 42 | 1 | 56 |
| consensus | 429 | 88 | 25 | 29 | 10 | 36 |
| scalp | 424 | 97 | 40 | 18 | 77 | 44 |
| sniper_ar (hérité de sniper) | 394 | 84 | 74 | 42 | 1 | 43 |
| quality | 370 | 105 | 35 | 36 | 3 | 36 |
| sniper_young | 216 | 48 | 38 | 27 | 3 | 25 |
| 11 bras `learning: false` | 0 | — | — | — | — | — |

`version` = « 2.0 » dans les 19 documents.

### A1 — Faux verdicts de `verdict_vs_reference` sous l'hypothèse nulle

- Script : `/tmp/claude-0/-home-user-CryptoBot/799e15ad-139d-51e9-9be8-84efb86ce030/scratchpad/sim_verdict.py`
  et `sim_verdict2.py` dans le même dossier.
- Loi synthétique : 75 % de pertes −U(5, 30) %, 25 % de gains log-normaux
  (médiane +20 %, plafond +800 %), mise de 70 $. Bras et témoin tirés de la
  **même** loi ; n_témoin = 300 ; fonctions `bootstrap_mean` et `compare` du
  dépôt.

| n bras | P(verdict concluant à tort) | dont « inférieur » (2 000 itér.) | couverture IC95 du bras |
|---|---|---|---|
| 3 | 0,315 | — | — |
| 4 | 0,287 | 0,233 | — |
| 5 | 0,212 | — | 0,72 |
| 8 | 0,120 | — | — |
| 15 | 0,092 | 0,077 | 0,835 |
| 30 | 0,047 | 0,017 | 0,858 |
| 60 | 0,010 | — | 0,89 |

### A2 — Arrêt optionnel de `live_mode_allowed`

- Script : `/tmp/claude-0/-home-user-CryptoBot/799e15ad-139d-51e9-9be8-84efb86ce030/scratchpad/sim_live.py`.
- Loi synthétique d'espérance ≈ +0,38 $/trade (55 % de pertes −U(5, 25) %, 45 %
  de gains log-normaux).
- Résultats : test unique à n = 20 → 2,2 % ; vérifié après chaque trade → 9 %
  avant n = 50, 12,5 % avant n = 100, 16 % avant n = 200.

### A3 — Ordre des extrema dans `simulate_exits`

- Script : `/tmp/claude-0/-home-user-CryptoBot/799e15ad-139d-51e9-9be8-84efb86ce030/scratchpad/demo_ordre.py`.
- Entrée : `peak_pct` 200 à 30 min, `trough_pct` −60 à 200 min ; règles SL −25 +
  tampon 10 (effectif −15), TP1 150.
- Sortie : `{'TP1': 1}`, +51,49 $. En réel avec ce stop : sortie au stop dès un
  passage sous −15 %.

### A4 — Ce qui reste non vérifiable sans `data/`

- Nombre et âge des positions ouvertes sans prix (B1).
- Présence dans les journaux actifs des lignes antérieures au 2026-08-06 et des
  364 trades exclus (B3, I2, I11).
- Couverture de coût par bras (I3).
- Refus « concentration meta » par bras et recouvrement token × heure (I4).
- Activation effective du facteur Kelly (WR > 40 % sur au moins 20 trades) (I2).
