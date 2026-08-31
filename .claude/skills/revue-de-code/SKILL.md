---
name: revue-de-code
description: Relire un diff, un patch ou un commit de CryptobBot avant de le considérer comme terminé. Utilise ce skill quand l'utilisateur demande une revue, un avis sur du code, « c'est bon ? », « prêt à commit ? », ou juste après avoir toi-même modifié plusieurs fichiers du dépôt — la plupart des régressions de ce projet sont des invariants violés discrètement, pas des erreurs de syntaxe, et elles ne se voient qu'avec cette liste en main.
---

# Revue de code — CryptobBot

Relire dans cet ordre. Chaque point vient d'un bug réellement survenu.

## 1. Invariants

- [ ] **Une donnée absente ne rejette pas.** Aucun `if c.x < seuil` sans
      `is not None` ; aucun `if not c.x` qui confondrait `0` et `None`.
- [ ] **Aucune écriture réseau dans `evaluate()`** — la collecte est partagée,
      l'évaluation par bras est du CPU pur. Un appel API ici multiplie le coût
      par sept.
- [ ] **La boucle ne peut pas mourir** : tout nouvel appel depuis `_cycle`,
      un agent, le dashboard, Telegram ou l'écriture d'état est enveloppé.
- [ ] **Écritures atomiques** (tmp + rename) pour `params.json`,
      `token_cache.json`, `state.json`. Pas de `open(..., "w")` direct.
- [ ] **Immuabilité** : `Candidate` et `Position` frozen, copie via
      `with_fields()` / `apply_exit()`. Aucun `object.__setattr__`.

## 2. Comptage et vocabulaire

- [ ] Compte de trades via `read_final_exits()`, pas `read_all()` — sinon TP1
      et TP2 doublent le total.
- [ ] `alpha_score_absolute` pour autoriser une entrée,
      `alpha_score` **uniquement** pour trier. Le nom de variable le dit.
- [ ] `rugcheck_score` traité comme un score de **sûreté** (haut = sûr).
- [ ] Tout chiffre affiché ou logué nomme **son bras**, ou dit qu'il agrège
      les sept.
- [ ] Termes du `CONTEXT.md` employés au sens du `CONTEXT.md`, des deux côtés
      (Python et TypeScript).

## 3. Apprentissage et seuils

- [ ] Aucun ajustement sans taille d'échantillon minimale explicite.
- [ ] Passage par `_bounded_set` / `_relax_set`, jamais d'écriture directe de
      paramètre.
- [ ] Un seuil nouveau ou modifié est **commenté avec la mesure** qui le
      justifie, pas avec une intuition.
- [ ] Les familles `rugcheck` et `authority` restent hors relâchement.
- [ ] « Rien à ajuster » et « pas assez de données » sont deux messages
      distincts.

## 4. Sécurité

- [ ] Aucune clé en dur, aucune clé loguée, `.env` intouché.
- [ ] Allowlists `gmgn.py` / `jupiter.py` inchangées, ou changement assumé avec
      ADR.
- [ ] Aucun chemin vers une exécution de trade réel.
- [ ] Aucune commande destructrice avec `--yes` / `-y`.
- [ ] L'API reste en lecture seule sur `data/`.

## 5. Tests et documentation

- [ ] Un test **nomme le bug** corrigé, en français, et couvre le cas « donnée
      absente ».
- [ ] `python -m unittest discover -s tests` passe ; `ruff check` passe ; si le
      front bouge, `npm run build` et `npm run lint` passent.
- [ ] `CONTEXT.md` mis à jour si un terme, un invariant ou un piège a changé.
- [ ] ADR écrit si la décision est structurante ; ADR existant relu si le
      changement le contredit.
- [ ] Commentaires en français, expliquant le **pourquoi**.

## Formuler la revue

Séparer ce qui **bloque** (invariant violé, sécurité, comptage faux) de ce qui
est une **suggestion**. Pour chaque point bloquant : le fichier, la ligne, la
conséquence concrète — et, quand c'est mesurable, la commande qui le montre.
