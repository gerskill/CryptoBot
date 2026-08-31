# Skills du dépôt CryptobBot

Skills projet, chargés automatiquement par Claude Code dans ce dépôt.
Commencer **toujours** par `cryptobot-context`, qui porte le vocabulaire, les
invariants et l'aiguillage.

| skill | périmètre |
|---|---|
| `cryptobot-context` | contexte, invariants, aiguillage — point d'entrée |
| `pipeline-scan` | découverte, enrichissement, filtres, motifs de rejet |
| `scoring-alpha` | composants de score, poids, seuil d'entrée |
| `sorties-positions` | stop loss, TP, trailing, coût de sortie, journal |
| `bras-strategies` | manifeste des 7 bras, capital, bornes, confluence |
| `apprentissage-garde-fous` | `LearningEngine`, cadences, relâchements, backtests |
| `agents-mesure` | agents de `src/agents/`, journaux `*_log.jsonl` |
| `chasse-au-lookahead` | fuite d'information dans les rejeux et backtests |
| `api-dashboard` | FastAPI + React/TypeScript, types partagés |
| `tests-qualite` | unittest, lint, définition de « terminé » |
| `analyse-reporting` | scripts d'analyse, entonnoir, IC95, rapports |
| `securite-exploitation` | `.env`, allowlists, PAPER/LIVE, backup, quotas |
| `adr-documentation` | ADR, `CONTEXT.md`, état du projet |
| `issues-triage` | tracker markdown `.scratch/`, vocabulaire de triage |
| `revue-de-code` | liste de relecture avant commit |

Les autres dossiers (`gmgn-*`, `jupiter-*`) sont des skills d'outillage externe
installés séparément.
