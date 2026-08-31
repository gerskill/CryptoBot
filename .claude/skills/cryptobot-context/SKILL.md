---
name: cryptobot-context
description: Point d'entrée obligatoire avant toute intervention sur CryptobBot (MemeCoin Alpha Loop) — vocabulaire métier, invariants non négociables, mode PAPER, et aiguillage vers le bon skill spécialisé. Utilise ce skill dès que la demande touche ce dépôt : scan, scoring, bras, sorties, apprentissage, agents, API, dashboard, tests, scripts d'analyse, ADR — même si l'utilisateur ne demande explicitement aucun contexte, et même pour une modification qui paraît triviale. Un agent qui invente son propre vocabulaire ou ignore un invariant casse silencieusement la comparabilité des sept bras.
---

# CryptobBot — contexte et aiguillage

Bot de scan et de trading **papier** de meme coins Solana, avec boucle
d'auto-amélioration. Sept stratégies (« bras ») partagent une collecte et
s'évaluent indépendamment.

## Avant d'écrire une ligne

1. Lire `CONTEXT.md` (racine) — vocabulaire, invariants, pièges connus.
2. Chercher l'ADR concerné dans `docs/adr/`. Beaucoup de choix de ce dépôt
   sont contre-intuitifs et **mesurés** : sans leur justification, ils
   ressemblent à des bugs, et « corriger » l'un d'eux annule une mesure.
3. Lire `docs/ETAT_DU_PROJET.md` pour l'état opérationnel et les chiffres
   réels avant d'avancer un ordre de grandeur.

## Vocabulaire à ne jamais tordre

| terme | sens exact |
|---|---|
| candidat | token sorti du scan, `Candidate` frozen — n'engage aucun capital |
| position | trade papier, **règles de sortie figées à l'entrée** |
| `alpha_score_absolute` | seuils fixes, comparable entre scans — **autorise l'entrée** |
| `alpha_score` | 60 % absolu + 40 % rang du lot — **trie seulement** (ADR 003) |
| `rugcheck_score` | score de **sûreté** (haut = sûr), inversé à l'ingestion (ADR 001) |
| shadow trade | rejet suivi 4 h, sert à arbitrer des **seuils**, jamais à mesurer une perf (ADR 007) |
| famille de rejet | regroupement des motifs ; `rugcheck` et `authority` jamais relâchées |
| sortie finale | `is_final_exit: true` — utiliser `read_final_exits()`, sinon les trades sont comptés double |

## Invariants — ne se négocient pas

1. **La boucle ne meurt jamais.** Dashboard, Telegram et écriture d'état ne
   peuvent pas faire tomber le trading.
2. **Le monitoring passe avant le scan.** Une position ouverte prime.
3. **Une donnée absente ne rejette jamais.** `None` passe, partout : pipeline,
   agents, rejeu. Le pipeline dégrade, il ne bloque pas.
4. **Les poids de score sont redistribués** ; le score reste sur 100.
5. **Écritures atomiques** (tmp + rename) pour `params.json`, `token_cache.json`,
   `state.json`.
6. **Aucun ajustement sans 10 échantillons dans le segment.**
7. **Tout paramètre ajustable est borné** (`PARAM_BOUNDS`).
8. **Mode PAPER.** Aucune transaction réelle n'est émise, le module d'exécution
   n'existe pas. Ne jamais l'écrire « en passant ».

## Où regarder

| question | fichier |
|---|---|
| comment un token est découvert | `src/pipeline.py::collect`, `src/apis/` |
| comment il est noté | `src/core/scoring.py` |
| pourquoi il est refusé | `src/pipeline.py::_rejection_reason` |
| quand une position sort | `src/core/positions.py::evaluate_exits` |
| comment les paramètres bougent | `src/core/learning.py` |
| ce que le bot a raté | `src/core/shadow.py` |
| l'enchaînement complet | `src/main.py::_cycle` |

## Aiguillage

| la demande porte sur… | skill |
|---|---|
| découverte, enrichissement, filtres, motifs de rejet | `pipeline-scan` |
| composants de score, poids, seuil d'entrée | `scoring-alpha` |
| stop loss, TP, trailing, time stop, coût de sortie | `sorties-positions` |
| ajouter/modifier/désactiver un bras, manifeste, capital | `bras-strategies` |
| `LearningEngine`, cadences, bornes, relâchements | `apprentissage-garde-fous` |
| nouvel agent de mesure, journaux `*_log.jsonl` | `agents-mesure` |
| endpoints FastAPI, composants React, types partagés | `api-dashboard` |
| écrire/lancer des tests, lint | `tests-qualite` |
| `scripts/`, entonnoir, IC95, rapport hebdo | `analyse-reporting` |
| `.env`, clés, allowlists, backup, lancement, PAPER/LIVE | `securite-exploitation` |
| décision structurante, mise à jour de doc | `adr-documentation` |
| bug ou tâche à consigner, état d'une issue | `issues-triage` |
| relire un diff avant commit | `revue-de-code` |

## Commandes

```bash
python -m src.main                            # la boucle
uvicorn api.server:app --reload --port 8000   # l'API du dashboard
python -m unittest discover -s tests          # la suite de tests
./scripts/backup.sh                           # avant toute opération risquée
```

## Style attendu

Docstrings, commentaires, noms de tests et messages **en français**. Les
commentaires expliquent le **pourquoi**, souvent en citant le bug évité.
Distinguer toujours « rien ajusté » de « pas assez de données pour ajuster ».
