# Observation on-chain & sociale — 2026-10-10

OBSERVATION SEULEMENT : aucun seuil, aucun bras, aucune décision d'entrée/sortie
ne lit ces champs. Ils sont écrits dans `entry_context` (position) puis recopiés
dans `trades_log.jsonl` (setdefault, ne remplace aucune colonne existante).

## Fichiers
- `src/agents/onchain_observer.py` — observateur Helius (fil d'arrière-plan) + `social_links()`.
- `src/main.py` — `_request_onchain_observation()` après le scan ; fusion dans `entry_context` à l'ouverture.
- `src/apis/gmgn.py` — respect du ban 429 (échéance lue dans le message), repli exponentiel, cache négatif 60 s, espacement 0,3 s.
- `scripts/feature_coverage.py` — taux de remplissage par champ.
- `tests/test_onchain_observer.py` — 19 tests.

## Garde-fous
- Méthodes RPC en liste blanche, lecture seule : getTransactionsForAddress, getSignaturesForAddress,
  getAccountInfo, getTokenLargestAccounts, getMultipleAccounts.
- Timeouts 6 s (RPC) / 4 s (RDAP). Limiteur partagé avec HeliusAPI (10 req/s).
- 3 nouveaux tokens max par cycle, file de 12, cache 15 min par token.
- Budget 20 000 crédits/jour (~65 crédits/token mesurés → ~300 tokens/jour, ~600 k crédits/mois).
- Anti look-ahead : `snapshot(token, decision_ts)` ne renvoie un résultat que si
  `computed_at <= decision_ts` ; sinon tous les champs à null, `onchain_status = pending|missing`.
- Toute panne (réseau, HTTP, JSON, budget) → null, jamais d'exception dans la boucle.

## Champs
onchain_* : status, version, computed_at, age_at_decision_sec, compute_sec, credits_used,
mint/freeze_authority_revoked, supply, top1_pct, top10_pct (top 20 comptes agrégés par
propriétaire — INCLUT pool/courbe de liquidité), creator, creator_pct_top20,
creator_bought_at_launch, dev_out_transfers, dev_sold (transferts sortants du mint par le
créateur), launch_slot/time, token_age_min, early_tx_sampled (100 premières tx),
first_buyers_n/in_top20/top20_pct, launch_slot_buyers, max_buyers_same_slot,
bundled_slots (≥3 acheteurs distincts dans un slot), bundle_buy_pct (achats bruts dans ces
slots, % de supply), fresh_checked/top10/first_buyers (wallet < 25 signatures et première
activité < 72 h), tx_count_5m/5_15m/truncated, unique_buyers_5m/15m (si fenêtre complète),
buyers_per_min_recent/5_15m, buyers_trend.

social_* : links_source, links_count, has_website/x/telegram/discord, x_handle,
x_is_community, website_domain, dex_boosts_active, dex_profile,
website_domain_age_days + website_rdap_status (RDAP public, gratuit).

## Limites connues
- Un token vu pour la première fois et acheté dans le même cycle a `onchain_status=pending`
  (non bloquant par construction).
- top10_pct inclut le pool / la bonding curve (non étiquetés par Helius).
- Sur les tokens très actifs, 100 tx complètes ≈ quelques secondes : unique_buyers_* null,
  taux par minute estimés sur l'échantillon.
- « Smart money » : Helius ne fournit pas d'étiquettes ; seule GMGN le fait.
- `bundle_buy_pct` = achats BRUTS dans les slots à ≥3 acheteurs : inclut MEV/sandwich, peut
  dépasser 100 % (mesuré 131 % sur REELS). Indicateur plus propre : `launch_slot_buyers`.
- Tokens trending : ≥1000 tx/15 min → `tx_count_truncated=true`, `tx_count_5_15m` et
  `unique_buyers_*` null (jamais de faux zéro) ; utiliser `buyers_per_min_*`.

## Mesures au déploiement (15:42 CEST)
- Durée de cycle : avant 19,8 s moy / 16,2 méd (13 cycles) ; après 10,8 s moy / 10,8 méd (13 cycles).
  La baisse vient surtout de GMGN (plus d'appels kline en échec pendant le ban).
- 12 entrées en 11 min (2 tokens) : onchain_status ok 12/12, remplissage onchain_* 92 %, social_* 95 %.
- Coût mesuré : 65 crédits/token, 3,8–6,0 s de calcul en arrière-plan.
