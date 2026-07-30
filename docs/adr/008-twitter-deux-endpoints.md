# 008. Volume et qualité sociale viennent d'endpoints différents

Date : 2026-07-29
Statut : accepté

## Contexte

Première implémentation bâtie sur l'hypothèse d'un quota de 60 req / 15 min
pour Twitter. Elle groupait tous les candidats dans une requête `OR` unique
pour économiser le budget.

Quotas **réellement mesurés** sur les en-têtes `x-rate-limit-limit` du compte :

| Endpoint | Quota réel | Hypothèse initiale |
|---|---|---|
| `/2/tweets/search/recent` | **450** / 15 min | 60 |
| `/2/tweets/counts/recent` | **300** / 15 min | inexistant |

Le batching résolvait une contrainte qui n'existe pas. Pire, il introduisait
un vrai défaut : `search/recent` plafonne à 100 résultats, donc un token
populaire mangeait tout le budget de résultats et affamait les autres —
observé en live (« lot saturé à 100 tweets »).

## Décision

Deux endpoints, deux rôles distincts :

- **`counts/recent`** → volume **exact** (`meta.total_tweet_count`, aucun
  plafond) + courbe minute par minute d'où sort la vélocité 15 min.
  C'est la source de vérité du volume.
- **`search/recent`** → plafonné à 100, donc inutilisable pour compter. Sert
  d'**échantillon de qualité** : auteurs uniques et engagement.

Un token = 2 requêtes. À 8 tokens par cycle de 90 s : 160 req / 15 min sur 750
disponibles, soit 21% du budget. `max_lookups_per_cycle` plafonne si le scan
remonte 30 candidats d'un coup.

Le score social pondère volume (40%), diversité d'auteurs (35%) et engagement
(25%), puis applique une pénalité anti-spam sur la part d'auteurs distincts et
un facteur d'accélération.

## Conséquences

- Ne jamais supposer un quota : le lire dans `x-rate-limit-limit`.
- Un token sans aucune mention ne déclenche pas la requête de qualité.
- Les symboles de moins de 4 caractères et une liste de mots courants
  (`MOON`, `PUMP`, `TRUMP`, `BABY`…) sont cherchés par **adresse de contrat**.
  Une mention d'adresse est rare mais quasi incontestable.
- `unique_authors` porte sur l'échantillon (≤ 100), pas sur le total : c'est
  un taux, pas un compte.
