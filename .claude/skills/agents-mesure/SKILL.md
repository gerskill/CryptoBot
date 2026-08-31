---
name: agents-mesure
description: Ajouter ou modifier un agent de mesure de CryptobBot (src/agents/ — counterfactual_timing, dev_history, volatility, microstructure, rsi, telegram_reporter). Utilise ce skill dès qu'il est question de mesurer quelque chose sur une entrée ou une sortie, de journaliser un signal, d'un fichier *_log.jsonl, de scoreboard/justesse d'agent, ou d'« ajouter un indicateur » — y compris quand la demande sonne comme « fais que le bot tienne compte de X », car ici un agent mesure et journalise, il ne décide jamais.
---

# Agents de mesure

**Ils calculent et journalisent ; aucun n'écrit un paramètre ni ne refuse une
entrée.** C'est la contrainte fondatrice du paquet : un agent qui apprend
réclame un échantillon, et le dépôt en compte quelques dizaines. Greffer une
douzaine d'apprenants là-dessus produirait douze surapprentissages parallèles.
Les agents produisent **d'abord** la donnée que la couche d'apprentissage lira
ensuite ; l'ordre inverse donne des lecteurs de fichiers vides.

| agent | mesure | quand | journal |
|---|---|---|---|
| `counterfactual_timing` | prix à −1, −2, −3 cycles avant l'entrée | ouverture | `counterfactual_log.jsonl` |
| `dev_history` | score créateur 0-100 sur 6 signaux déjà collectés | ouverture | `dev_history_log.jsonl` |
| `volatility_agent` | écart-type des rendements log, base horaire | clôture | `volatility_log.jsonl` |
| `microstructure_agent` | dérive liquidité/prix, devis A/R, profondeur | clôture | `microstructure_log.jsonl` |
| `rsi_agent` | RSI de Wilder sur bougies reconstruites | clôture | `rsi_log.jsonl` |
| `telegram_reporter` | rapports poussés | périodique | `telegram_reporter_log.jsonl` |

## Choisir le point de mesure — la leçon mesurée

Les cinq agents tournaient d'abord tous à l'ouverture. Résultat sur les
premières entrées réelles : `rsi` avait 1 et 3 échantillons (il en faut 15),
`volatility` 1 et 5 (il en faut 8). **Le bot entre vite après la découverte**,
donc `PriceHistory` est presque vide au moment d'ouvrir.

La règle qui en découle :

- **À l'ouverture** — ce qui n'existe **qu'à cet instant** : les snapshots
  d'avant l'entrée disparaissent au cycle suivant, et le candidat enrichi
  aussi.
- **À la clôture** — tout ce qui a besoin d'une accumulation de points.

Un nouvel agent commence par répondre à cette question, et le justifier en
docstring.

## Écrire un agent

1. Fonction pure de mesure, qui retourne `None` quand la donnée manque — jamais
   une valeur par défaut qui se confondrait avec une mesure réelle.
2. Journalisation par `src/agents/_journal.py` : une ligne JSONL par
   événement, horodatée, avec `token_address`, le bras, et **la version de la
   mesure** si sa formule peut changer (sinon deux formules se mélangent dans
   un même fichier et l'échantillon devient inexploitable).
3. Un chemin de journal déclaré dans `src/settings.py`, jamais un chemin en dur.
4. Appel depuis `src/main.py::_measure_entry` ou `::_measure_hold` selon le
   point de mesure choisi. **Enveloppé pour ne jamais faire tomber la boucle** :
   une exception d'agent se logue, elle n'interrompt pas le trading.
5. Test dédié `tests/test_agents_<nom>.py`, en français, avec le cas « donnée
   absente ».

## Juger un agent

`src/core/scoreboard.py` mesure la justesse par agent. Aucun jugement avant
`MIN_SIGNALS_PER_AGENT = 20` signaux, et aucune pondération avant 50 trades.
Avant ces seuils, la seule réponse honnête est « pas assez de données ».

```bash
python -m unittest discover -s tests -p 'test_agents_*.py'
```
