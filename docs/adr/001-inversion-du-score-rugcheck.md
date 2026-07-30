# 001. Inversion du score RugCheck

Date : 2026-07-29
Statut : accepté

## Contexte

`params.json` définit `min_rugcheck_score: 70` avec la sémantique « plus haut
= plus sûr ». L'API RugCheck renvoie l'inverse : `score_normalised` est un
score de **risque**.

Mesuré sur `/v1/tokens/{mint}/report` :

| Token | `score_normalised` | Interprétation |
|---|---|---|
| BONK | 7 | très sûr |
| SOL | 1 | très sûr |
| LEVI (memecoin) | 44 | douteux |

Appliquer `score >= 70` sur la valeur brute aurait rejeté BONK et SOL, et
accepté tout ce qui est dangereux. Le filtre aurait fonctionné exactement à
l'envers, silencieusement.

## Décision

`RugCheckAPI._parse()` convertit systématiquement :

```python
safety_score = max(0.0, 100.0 - min(raw_risk, 100.0))
```

Le champ exposé s'appelle `safety_score`, jamais `score`, pour que la
sémantique soit portée par le nom. C'est `safety_score` qui alimente
`Candidate.rugcheck_score` et qui est comparé à `min_rugcheck_score`.

Utilisation de `/report` (complet) plutôt que `/report/summary` : même coût de
quota, mais fournit en plus `creator`, `creatorBalance` (donc le % dev wallet),
le flag `rugged` et les autorités mint/freeze.

## Conséquences

- Toute nouvelle source de sécurité doit expliciter son sens avant intégration.
- `lpLockedPct` se trouve au top-level dans `/summary` mais sous
  `markets[].lp` dans `/report` : `_lp_locked_pct()` gère les deux.
- Sur les tokens de moins d'une heure, la sûreté sort quasi toujours à 99.
  Le filtre agit comme garde-fou, pas comme signal de classement.
