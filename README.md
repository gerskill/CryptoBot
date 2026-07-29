# MemeCoin Alpha Loop V2

Bot de scan de meme coins Solana/EVM. **Mode PAPER : aucune transaction n'est émise.**
Ce dépôt couvre la chaîne découverte → audit → scoring → analyse technique → signal.

## Installation

```bash
pip install -r requirements.txt
cp .env.example .env   # renseigner HELIUS_API_KEY quand disponible
python -m src.main
```

Tests :

```bash
python -m unittest discover -s tests -v
```

## Architecture

```
config/params.json        règles dynamiques (filtres, poids, exits) — source de vérité
src/main.py               boucle 90s, monitoring, dashboard, panic button
src/pipeline.py           découverte → enrichissement → filtres sécurité → scoring
src/settings.py           chemins + clés API (lues dans .env, jamais en dur)
src/apis/dexscreener.py   découverte + données marché (sans clé)
src/apis/rugcheck.py      audit sécurité (sans clé)
src/apis/birdeye.py       holders exacts, OHLCV, new listings (1 req/s)
src/apis/helius.py        holders + dev wallet (repli de Birdeye)
src/apis/twitter.py       mentions sociales (quota 60/15min)
src/notify/telegram.py    alertes + commandes /STOP /RESUME /STATUS
src/core/ratelimit.py     fenêtre glissante thread-safe
src/core/cache.py         cache 30 min + watchlist + blacklist (persistés)
src/core/scoring.py       score alpha 0-100
src/core/positions.py     règles de sortie SL/TP/trailing/time/rug
src/core/portfolio.py     portefeuille papier, sizing Kelly, cooldown
src/core/journal.py       trades_log.jsonl append-only
src/core/learning.py      étape 6 : stats, segments, ajustement borné des paramètres
src/core/shadow.py        suivi des rejets — corrige le biais du survivant
src/core/params.py        lecture/écriture params.json + historique d'ajustements
src/analysis/technical.py bougies reconstruites, HH/HL, volume
```

## Boucle papier complète

Un cycle enchaîne : commandes Telegram → **monitoring des positions ouvertes** →
scan → scoring → analyse technique → ouverture. Le monitoring passe avant le scan :
une position en cours prime sur la recherche d'une nouvelle entrée.

Sorties évaluées dans cet ordre — **écart assumé vs la spec**, qui place le rug pull
en 7ᵉ : rug pull → stop loss (ou breakeven après TP1) → time stop → trailing → take
profits. Un rug vide la liquidité en secondes ; l'évaluer après les TP garantirait de
sortir sur un carnet déjà vide.

Garde-fous appliqués : max 5 % du capital par position quoi que dise le Kelly, Kelly
inactif sous 20 trades, pause de 2 h après 3 pertes consécutives, un seul trade par
token à la fois, `/STOP` ferme tout et met en veille.

Passage en LIVE verrouillé par `LearningEngine.live_mode_allowed()` : 20 trades,
win rate > 40 %, profit factor > 1.5. Le module d'exécution n'existe pas — le verrou
n'est donc pas contournable par erreur.

## Flux d'un cycle (90 s)

1. `/token-profiles/latest/v1` + `/token-boosts/latest/v1` → adresses candidates
2. Filtre **cache / blacklist / watchlist** *avant* tout appel de données
3. `/latest/dex/tokens/{a1,...,a30}` → **30 tokens par requête**
4. Filtres marché : liquidité, volume 1h, fenêtre d'âge
5. Enrichissement parallèle (5 threads) : RugCheck → Helius
6. Filtres sécurité : score, holders, concentration, dev wallet, autorités
7. Score alpha 0-100 pondéré par `scoring_weights`
8. Score ≥ `alpha_score_entry_threshold` → analyse technique → signal `data/signals.jsonl`

## Rate limiting

Un limiteur par famille d'endpoints, marge de sécurité 10 %. Quotas Twitter et
Birdeye **mesurés** sur les en-têtes `x-rate-limit-limit`, pas supposés :

| API | Quota réel | Limiteur |
|-----|-----------|----------|
| DexScreener profiles / boosts | 60/min | 54/min |
| DexScreener tokens / pairs | 300/min | 270/min |
| RugCheck | ~60/min (non documenté) | 54/min |
| Helius | 10/s (gratuit) | 540/min |
| Birdeye | ~1/s (429 sur 2 appels enchaînés) | 1/s |
| Twitter `counts/recent` | **300 / 15 min** | 270 / 15 min |
| Twitter `search/recent` | **450 / 15 min** | 405 / 15 min |

Le batch 30-adresses fait tomber un scan de 37 tokens à **4 requêtes** au lieu de 39.

## Social : deux endpoints, deux rôles

`counts/recent` donne le **volume exact** (`meta.total_tweet_count`, aucun plafond) plus
une courbe minute par minute d'où sort la vélocité. `search/recent` plafonne à 100
résultats — inutilisable pour compter, mais seule source des auteurs et de l'engagement.
Il sert donc d'**échantillon de qualité**, jamais de compteur.

Un token = 2 requêtes. À 8 tokens par cycle de 90 s : 160 req / 15 min sur 750
disponibles, soit **21 % du budget**. `max_lookups_per_cycle` plafonne si le scan
remonte 30 candidats d'un coup.

Le score social pondère volume (40 %), diversité d'auteurs (35 %) et engagement (25 %),
puis applique une pénalité anti-spam sur la part d'auteurs distincts et un facteur
d'accélération. 200 tweets de 3 comptes est une ferme à bots, pas de l'engouement.

## Deux scores, deux usages

`alpha_score_absolute` (seuils fixes) **autorise l'entrée**. `alpha_score` (60 % absolu
+ 40 % rang dans le lot) **classe** les candidats.

Sans cette séparation, le meilleur candidat d'un lot médiocre décroche 100/100 sur les
composants relatifs et franchit le seuil mécaniquement — une nuit creuse produirait des
entrées sur les moins mauvais déchets disponibles.

## Auto-amélioration : les deux défauts corrigés

**Biais du survivant.** Un bot qui n'apprend que de ses trades ne peut jamais découvrir
qu'un filtre est trop strict — les candidats rejetés ne produisent aucune donnée. Le seul
signal disponible est « mes trades perdent → resserrer ».

`src/core/shadow.py` enregistre chaque rejet avec son prix, puis revient voir ce que le
token est devenu (fenêtre de 4 h). Si plus de 25 % des rejets d'une famille de motif
seraient montés à +100 %, le filtre correspondant est relâché. Les rejets pour risque
critique ou autorité active ne sont jamais suivis : on ne relâche pas un filtre honeypot,
quel que soit le manque à gagner.

Ce sont des trades **fictifs** : pas de slippage, et une entrée réelle aurait bougé le
prix. Ils servent à arbitrer des seuils, pas à mesurer une performance.

**Cliquet monotone.** Toutes les règles d'ajustement de la spec ne font que resserrer
(`min_age +0.5`, `max_age -0.5`, `min_liquidity +5000`). Sans contre-poids, le bot
converge vers « plus aucun candidat ne passe » et s'arrête de trader définitivement.
Corrigé par `PARAM_BOUNDS` (bornes dures sur chaque paramètre ajustable) plus les
relâchements issus du shadow tracking.

## Deux cadences

Scan toutes les 90 s, **monitoring des positions toutes les 20 s**
(`scan.monitor_interval_seconds`). La découverte n'a rien à gagner à tourner plus vite
que le rafraîchissement DexScreener (~30-60 s), mais un stop loss à -25 % échantillonné
toutes les 90 s sort très loin sous son seuil : un memecoin perd 40 % en 30 secondes.
La détection de rug a une fenêtre de 2 min — à 90 s d'échantillonnage, ça fait 1 à 2
points, inexploitable. Coût du monitoring rapide : 1 requête par position et par tick,
sur un quota de 300/min.

## Cache et watchlist

- **Cache 30 min** (`data/token_cache.json`) : un token déjà scanné n'est pas ré-interrogé.
- **Watchlist** : un token *qualifié* échappe au cache et est rafraîchi à chaque cycle.
  Sans ça, l'historique de prix ne se remplit jamais et l'analyse technique reste bloquée
  en `pending` — c'est le piège principal de la combinaison cache + analyse technique.
- **Audit sécurité** mémorisé 15 min : le prix se rafraîchit toutes les 90 s, pas l'audit.
- **Blacklist** persistée : 7 j sur risque critique, 48 h prévu pour un échec honeypot.

## Points à connaître

**Score RugCheck inversé.** RugCheck renvoie un score de *risque* (BONK = 7). `params.json`
utilise `min_rugcheck_score: 70` au sens « plus haut = plus sûr ». Conversion appliquée :
`sûreté = 100 − risque`. BONK → 93.

**RugCheck ne discrimine pas les tokens très jeunes.** Mesuré en live : les 3 candidats
qualifiés d'un scan sortaient tous à `safety 99` avec une liste de risques vide. Sur un token
de moins d'une heure, ce score est quasi constant — il sert de **garde-fou** (il rejette les
mints/freeze actifs et les rugs) et non de signal de classement. Son poids de 15 % dans le
score alpha n'apporte donc presque aucune séparation entre candidats jeunes ; la vraie
discrimination vient de `top_holder_pct` et des holders (Helius).

**Bougies : Birdeye en source primaire.** `/defi/ohlcv` rend 60 bougies 1 m pour une heure
de recul, disponibles immédiatement. La reconstruction depuis les snapshots reste en repli
quand Birdeye est indisponible — elle impose alors ~9 min de chauffe avant le premier
verdict (buckets alignés sur l'horloge) et produit des bougies à 2 points où `high ≈ low`.

**Le critère de structure de la spec ne se déclenche jamais.** Mesuré sur 43 memecoins
Solana réels, bougies 1 m :

| Critère | Seul | Croisé avec l'expansion de volume |
|---|---|---|
| HH + HL sur 3 bougies (`strict`, spec littérale) | 2.3 % | **0 %** |
| Closes croissants ×3 | 13.6 % | 0 % |
| Tendance nette `close[-1] > close[0]` (`balanced`) | 18.6 % | **4.7 %** |

Exiger 3 higher highs ET 3 higher lows consécutifs sur des bougies 1 m est un événement à
2.3 %, qui tombe à 0 % une fois croisé avec le volume. Un bot dans ce mode ne prend aucun
trade — donc ne produit aucun point d'apprentissage, et l'étape 6 ne démarre jamais.
D'où `entry_rules.structure_mode` avec **`balanced` par défaut** : ~1 entrée toutes les
22 évaluations, soit de quoi accumuler les 20 trades papier de validation.

**Volume : expansion, pas croissance stricte.** Exiger 3 hausses consécutives sur une série
bruitée ne se produit quasiment jamais (0 passage sur 12 évaluations en live). Le test
compare la moyenne des 3 dernières bougies à celle des 3 précédentes.

**Poids redistribués.** Sans clé Twitter/GMGN, les composants social et smart money sont
exclus du score et leur poids est réparti sur les autres. Le score reste sur 100 —
`sub_scores._weights_used` indique la fraction de poids réellement couverte (0.60 aujourd'hui).

**Filtres non bloquants sur donnée absente.** Un filtre dont la donnée manque (holders sans
clé Helius) ne rejette pas le candidat. Sans Helius, `min_holders` n'est donc pas appliqué.

**`min_liquidity_lock_days` non vérifiable.** RugCheck expose `lpLockedPct`, pas une durée
de lock. Le filtre en jours nécessite une autre source.

**P&L papier optimiste.** Les sorties sont simulées au prix observé, sans slippage ni
frais. Sur un memecoin à 20K de liquidité, l'aller-retour coûte plusieurs pour cent en
réel. Un win rate papier de 45 % ne garantit donc pas un win rate réel de 45 %.

## Non implémenté

Exécution des trades (Jupiter/1inch), wallets, test honeypot, smart money (GMGN n'a pas
d'API publique — `min_smart_money_buys_30min` reste inactif), journal Airtable/Notion,
backtest de validation (étape 6.4).
