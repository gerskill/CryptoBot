---
name: pipeline-scan
description: Modifier la découverte, l'enrichissement, les filtres ou les motifs de rejet de CryptobBot (src/pipeline.py, src/apis/). Utilise ce skill dès qu'il est question de scan, de collecte de tokens, de GMGN/DexScreener/Birdeye/Helius/RugCheck/Jupiter, d'un candidat rejeté à tort, d'un nouveau filtre, d'un champ à enrichir, du budget d'appels API ou de l'entonnoir de décision — y compris quand la demande ressemble à un simple « ajoute un filtre » ou « pourquoi ce token ne passe pas ».
---

# Pipeline de scan

`ScanPipeline` fait **une collecte partagée** puis **N évaluations en CPU
pur**, une par bras. C'est la propriété qui rend sept stratégies possibles à
budget d'API constant : ajouter un bras coûte zéro requête. Toute modification
qui remet une requête réseau dans `evaluate()` détruit cette propriété.

- `collect(envelope)` → `CollectedBatch` : découverte + enrichissement, partagé.
- `evaluate(...)` → `ArmEvaluation` : filtres et score, par bras, sans réseau.
- `discovery_envelope(filter_sets)` : enveloppe **la plus lâche** de tous les
  bras. Un filtre serveur trop serré ici prive tous les bras d'un token.

## Les six portes d'entrée, dans l'ordre

```
1. filtres du bras        12 à 37 seuils selon le bras
2. alpha absolu >= seuil  65 à 75
3. confluence >= 2        bras `consensus` uniquement
4. can_open               cooldown, place, doublon
5. analyse technique      pas de dump, tendance, expansion de volume
6. garde économique       honeypot -> taille -> plancher de TP
```

## La règle qui casse le plus souvent

**Une donnée absente ne rejette JAMAIS.** Un filtre dont la donnée manque
(holders sans clé Birdeye, `smart_money_buys_30m` toujours `None`) laisse
passer le candidat. Écrire `if c.holders < seuil` sur un `None` lève, et
`if not c.holders` rejette un `0` légitime **et** un `None` : les deux sont des
bugs. Le motif correct est explicite :

```python
if c.holders is not None and c.holders < f["min_holders"]:
    return "holders"
```

## Motifs de rejet

`_rejection_reason` retourne une **chaîne de famille** (`liquidity`, `holders`,
`concentration`, `rugcheck`, `social`, `authority`…). Ces familles sont lues
par `shadow.reason_family` et par l'apprentissage pour savoir quel paramètre
relâcher : inventer un motif hors vocabulaire le rend invisible au relâchement.
Un nouveau motif se déclare dans les deux endroits, avec un test.

`rugcheck` et `authority` ne sont **jamais** relâchées, quelle que soit la
mesure — ce sont des gardes de sécurité, pas des seuils de performance.

## Sources et leurs limites réelles

| source | rôle | limite |
|---|---|---|
| GMGN `market trending` | découverte principale, filtrage **serveur**, 30 champs en 1 appel | ~60/min |
| Jupiter | prix par lot (50 mints), devis, coût A/R, honeypot | **1 req/s** |
| DexScreener | découverte secondaire, `pair_address` | 270/min |
| RugCheck | sécurité | 60/min |
| Birdeye | holders exacts, OHLCV | **1 req/s — le goulot** |
| Helius | repli holders (pagination coupée : borne inférieure) | 600/min |
| Twitter | social | quota épuisé, module coupé |

Le prochain mur est Birdeye. Avant d'ajouter un appel par candidat, compter
son coût sur un cycle de 90 s et passer par `src/core/budget.py` et
`src/core/ratelimit.py` — jamais un `requests.get` nu.

⚠️ Le flux DexScreener `/token-profiles` est de la **promotion payante** :
liquidité médiane 1 634 $, 1 token sur 38 passait les filtres. Ne pas le
reprendre comme source de découverte.

## Pièges mesurés

- **`holders_is_exact = False`** signifie « au moins N », pas « N ». Ne jamais
  comparer strictement une borne inférieure sans vérifier ce drapeau.
- **Jeune ET liquide n'existe pas** : âge ≤ 1 h + liq ≥ 15 K = 0 token mesuré.
  Chaque bras choisit un point sur cette courbe. Un bras dont la fenêtre est
  vide ne trade pas — ce n'est pas un bug.
- **`Candidate` est frozen** : tout enrichissement retourne une copie via
  `with_fields()`. Une mutation en place ne compilera pas, ou pire, sera
  contournée par un `object.__setattr__` — à refuser en revue.

## Vérifier une modification

```bash
python -m unittest tests.test_funnel tests.test_pipeline_split tests.test_garde_fous
python -m scripts.analyse_rejets --heures 24        # où meurent les candidats
```

Un changement de filtre se juge sur l'entonnoir mesuré, pas sur l'intuition :
avant/après en candidats retenus par bras.
