# Détecteur « insider front-run » — observation seule (2026-10-18)

## Pourquoi
Bubblemaps a montré que des wallets liés à certains callers X achètent ~20 s avant le call puis
revendent ~4 min après. Killian a validé (18/10) de **mesurer** ce schéma sur nos entrées,
**sans changer aucune décision**. Activation éventuelle dans la porte `anti_rug` : décision de
Killian, après évaluation aux bilans du 24/10 et du 7/11.

## Ce qui a été construit
- `src/core/insider.py` — calcul pur (aucun appel réseau) : `insider_metrics()`, `detect_spike()`,
  `funder_of()`, `swaps()`, `first_activity()`.
- `src/agents/onchain_observer.py` — après le calcul on-chain existant, méthode `_insider()` :
  réutilise les données déjà téléchargées (transactions de lancement, 15 dernières minutes,
  top 20 porteurs, signatures des 12 wallets vérifiés « frais ») et ajoute **au plus 6
  `getTransaction`** (1 crédit chacun, lecture seule, ajouté à la liste blanche) pour trouver
  la source de financement des wallets frais.
- Champs `insider_*` présents dans `OnchainObserver.snapshot()` (null si pas prêt), donc copiés
  dans `entry_context` → `trades_log.jsonl` à chaque entrée, avec le même anti look-ahead
  (`computed_at <= décision`).

## Méthode
Wallets examinés : top 10 porteurs + 10 premiers acheteurs (max 12, mêmes que la vérif « frais »),
plus le créateur s'il a financé l'un d'eux. Liens (union-find) :
1. même source de financement (1er transfert SOL entrant) ;
2. financé directement par le créateur ;
3. deux wallets frais créés à < 5 min l'un de l'autre ;
4. achat dans le même slot au lancement (bundle).

Pic d'achats : minute avec ≥ 5 achats et ≥ 3 × la médiane des autres minutes (fenêtre 15 min
récente, sinon fenêtre de lancement ; ≥ 3 min couvertes). « Avant le pic » = 5 min avant.
Vente précoce : revente < 10 min après le 1er achat observé.

## Champs
| Champ | Sens |
|---|---|
| `insider_status` | ok / no_wallets / budget_partial / error (null = pas calculé) |
| `insider_credits_used` | crédits Helius de la partie insider |
| `insider_wallets_checked`, `insider_funders_found` | couverture |
| `insider_links_funder/creator/created/slot` | nombre de liens par type |
| `insider_cluster_size` | taille de la plus grosse grappe (0 = aucun lien) |
| `insider_linked_wallets`, `insider_clusters_n` | wallets liés au total, nb de grappes |
| `insider_pct_supply` | % de l'offre détenu par les wallets liés (**borne basse**, top 20 seulement) |
| `insider_spike_detected/time/buys` | pic détecté, minute (epoch), nb d'achats |
| `insider_bought_before_spike`, `insider_pre_spike_buyers`, `insider_spike_lead_sec` | achats liés dans les 5 min avant le pic |
| `insider_buyers_before_entry_15m` | wallets liés ayant acheté dans les 15 min avant le calcul (donc avant l'entrée) |
| `insider_early_sellers`, `insider_sellers_15m`, `insider_selling` | reventes rapides / récentes |
| `insider_flag` | grappe ≥ 3 ET (offre ≥ 5 % OU achat avant pic OU revente) |

## Garanties
- Observation seule : aucun bras, porte, apprentissage ou paramètre ne lit `insider_*`.
- Isolé : un dépassement de budget ou une panne dans la partie insider **ne change jamais**
  `onchain_status` (donc ni la porte `anti_rug`), seulement `insider_status`.
- `params.json`, `.env`, `live_allowed`, DD : non touchés. PAPER seulement.

## Budget Helius
Avant : ~65 crédits par token (médiane 65, max 66 sur 1 572 entrées journalisées), plafond
20 000/jour (≈ 300 tokens/jour ; plafond atteint une seule fois, le 12/10). Après : +0 à 6 crédits
(seulement s'il y a des wallets frais) → ≤ ~71 (≈ +9 %), soit ≈ 280 tokens/jour au plafond.
Le plafond journalier reste une limite dure.

## Limites connues
- Faux positifs : deux wallets financés par le même hot wallet d'exchange sont « liés »
  (`KNOWN_HUB_FUNDERS` vide faute de liste fiable).
- Fenêtre récente tronquée à 100 transactions sur les tokens très actifs → pic parfois non détecté.
- Seuls 12 wallets examinés ; les acheteurs du pic hors top 10 / premiers acheteurs ne sont pas
  vérifiés.

## Évaluation prévue
Aux bilans du 24/10 (point d'étape) et du 7/11 : comparer PF / taux de gain / part de RUG_PULL
des trades `insider_flag = true` vs `false` (et par composante), trades post-correctif uniquement.

## Tests
`tests/test_insider_obs.py` (12 tests). Suite complète : 950 passed (938 avant).
Sauvegarde : `backups/pre-insider-obs-2026-10-18/`.
