# CONTEXT — MemeCoin Alpha Loop

Bot de détection et de trading papier de meme coins sur Solana, avec boucle
d'auto-amélioration. Un cycle scanne les tokens récents, les filtre, les note,
valide leur structure de prix, puis ouvre et suit des positions **simulées**.

**Aucune transaction réelle n'est émise.** Le module d'exécution n'existe pas.

---

## Vocabulaire

Ces termes ont un sens précis dans ce projet. Les employer autrement crée de
la confusion silencieuse.

### Candidat
Un token qui est sorti du scan. Objet **immuable** (`Candidate`, frozen
dataclass) : tout enrichissement retourne une copie via `with_fields()`.
Un candidat n'est pas une position — il n'engage aucun capital.

### Position
Un trade papier ouvert. Ses règles de sortie sont **figées à l'entrée** :
modifier `params.json` ne change pas rétroactivement le stop loss d'une
position déjà ouverte. Immuable elle aussi ; `apply_exit()` retourne une
nouvelle instance.

### Score alpha absolu vs score de classement
Deux nombres distincts, jamais interchangeables :

- `alpha_score_absolute` — seuils fixes, comparable d'un scan à l'autre.
  **C'est lui qui autorise une entrée.**
- `alpha_score` — 60% absolu + 40% rang dans le lot courant.
  **C'est lui qui trie les candidats entre eux.**

Confondre les deux fait entrer le bot sur le moins mauvais déchet d'une nuit
creuse. Voir ADR 003.

### Sûreté RugCheck
`rugcheck_score` dans ce code est un score de **sûreté** : plus haut = plus
sûr. L'API RugCheck renvoie l'inverse (un score de risque). La conversion est
faite dans `RugCheckAPI._parse()`. Voir ADR 001.

### Watchlist
Ensemble de tokens qualifiés, rafraîchis à **chaque** cycle en ignorant le TTL
du cache. Sans elle, l'historique de prix d'un candidat ne se remplit jamais.
Voir ADR 002.

### Shadow trade
Un candidat **rejeté**, suivi pendant 4 h pour mesurer ce qu'il serait devenu.
Trade fictif : pas de slippage, et une entrée réelle aurait bougé le prix.
Sert à arbitrer des **seuils**, jamais à mesurer une performance. Voir ADR 007.

### Famille de rejet
Regroupement des motifs de rejet (`liquidity`, `holders`, `concentration`,
`rugcheck`, `social`…) utilisé par l'apprentissage pour savoir quel paramètre
relâcher. Les familles `rugcheck` et `authority` ne sont **jamais** relâchées.

### Mode PAPER / LIVE
`PAPER` : positions simulées au prix observé. `LIVE` : verrouillé par
`LearningEngine.live_mode_allowed()` — 20 trades, WR > 40%, PF > 1.5 — et
inatteignable puisque le module d'exécution n'existe pas.

### Sortie finale vs partielle
Un trade = **une** sortie finale (`is_final_exit: true`). TP1 et TP2 vendent
des fractions et écrivent des lignes de journal intermédiaires. Compter
toutes les lignes double le nombre de trades : utiliser `read_final_exits()`.

---

## Invariants

1. **La boucle ne meurt jamais.** Toute exception de cycle est attrapée et
   logguée. Le dashboard, Telegram et l'écriture d'état ne peuvent pas faire
   tomber le trading.
2. **Le monitoring passe avant le scan.** Une position ouverte prime toujours
   sur la recherche d'une nouvelle entrée.
3. **Une donnée absente ne rejette pas.** Un filtre dont la donnée manque
   (holders sans clé Birdeye) laisse passer le candidat. Le pipeline dégrade,
   il ne bloque pas.
4. **Les poids de score sont redistribués.** Un composant indisponible est
   exclu et son poids réparti sur les autres ; le score reste sur 100.
   `sub_scores._weights_used` dit quelle fraction est réellement couverte.
5. **Écritures atomiques.** `params.json`, `token_cache.json` et `state.json`
   s'écrivent en tmp + rename. Un lecteur ne voit jamais un fichier à moitié
   écrit.
6. **Aucun ajustement de paramètre sans 10 échantillons dans le segment.**
7. **Tout paramètre ajustable est borné** (`PARAM_BOUNDS`). Voir ADR 007.

---

## Pièges connus

**Le P&L papier est optimiste.** Sorties au prix observé, sans slippage ni
frais. Sur 20K de liquidité, l'aller-retour coûte plusieurs pour cent en réel.
Un win rate papier de 45% ne vaut pas un win rate réel de 45%.

**Le stop loss sort sous son seuil.** Échantillonnage discret : mesuré à
-27.2% pour un SL réglé à -25%. Réduire `monitor_interval_seconds` diminue
l'écart sans jamais l'annuler.

**RugCheck ne discrimine pas les tokens jeunes.** Sur du moins d'une heure, le
score sort quasi systématiquement à 99. C'est un garde-fou (il rejette les
mint/freeze actifs et les rugs), pas un signal de classement.

**`holders` peut être une borne inférieure.** Via Helius, la pagination est
coupée : `holders_is_exact = False` signifie « au moins N », pas « N ».
Birdeye donne la valeur exacte en un appel et passe en premier.

**`smart_money_buys_30m` est toujours `None`.** GMGN n'a pas d'API publique.
Le champ existe, le filtre est inactif, le composant de score est exclu.

**Le journal de trades n'est pas versionné.** `data/` est gitignoré — c'est la
mémoire du bot et elle n'est protégée que par `./scripts_backup.sh`.

---

## Où regarder

| Question | Fichier |
|---|---|
| Comment un token est découvert | `src/apis/dexscreener.py` |
| Comment il est noté | `src/core/scoring.py` |
| Pourquoi il est refusé | `src/pipeline.py::_rejection_reason` |
| Quand une position sort | `src/core/positions.py::evaluate_exits` |
| Comment les paramètres bougent | `src/core/learning.py` |
| Ce que le bot a raté | `src/core/shadow.py` |
| L'enchaînement complet | `src/main.py::_cycle` |
