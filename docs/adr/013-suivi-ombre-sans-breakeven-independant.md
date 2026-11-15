# 013. Suivi ombre sans le breakeven indépendant

Date : 2026-11-08
Statut : accepté

## Contexte

`BREAKEVEN_STOP` veut dire seulement `breakeven_moved`. Trois gestes posent
ce drapeau : le déclencheur `breakeven_trigger` (`update_water_marks`), le
TP1 (`apply_exit`), et l'ajout de taille de `scaled_be` (`apply_scale_in`,
stop exact au prix moyen). Les confondre, puis « couper le breakeven »,
coupe aussi les deux protections qui ne sont pas la question.

Le niveau n'est pas unique non plus. `breakeven_stop_exact` remonte le stop
à 0 %. Sans ce drapeau, le stop vaut 0 + `stop_loss_slippage_buffer_pct`
(+10 sur plusieurs bras, +15 sur sniper) : un plancher de profit, pas un
retour au prix d'entrée.

Un rejeu hors ligne sur des bougies récupérées après coup ne tranche pas.
Il lui manque le tick réel, le coût réellement mesuré, et il choisit la
paire après avoir vu la sortie. La mesure qui reste honnête est prospective :
à partir de maintenant, sur les prix que le bot voit déjà.

## Décision

Un sidecar, `scripts/shadow_nobe.py`, lit les positions ouvertes, `state.json`
et le journal des bras `tp1_30` et `scaled_be`. Pour chaque position vue
propre (ni breakeven, ni ajout, ni vente partielle), il tient une ombre
papier avec les mêmes règles figées, sauf `breakeven_trigger = 0`.

Le TP1 et l'ajout continuent d'armer leur stop, au niveau exact ou au
plancher, et l'origine est écrite à part. Après une sortie réelle due au
seul déclencheur, l'ombre poursuit le stop initial, les TP, les partiels
et le time stop. Les prix de cette poursuite viennent de DexScreener public,
sur la paire d'entrée, au plus quatre requêtes par minute. Pas de Helius,
pas de Jupiter, pas de collecte d'historique.

Les frais de l'ombre sont la médiane des coûts déjà mesurés du bras, figée
à l'entrée (moitié pour une partielle, plein pour la jambe finale, prix nu
sous dix mesures). Une mesure qui arrive ensuite est notée, elle ne réécrit
pas l'ombre. Le fichier de config, que l'apprentissage réécrit, est hashé.
S'il diverge, on le dit. La règle reste celle de la position.

Le livre de 7 000 USD sert au drawdown mark-to-market de la mesure. Ce n'est
pas la trésorerie du bras et pas un portefeuille alternatif. L'effet publié
est local à la paire. Un trou de prix de plus de 90 s, une cote manquante
jusqu'après le time stop, ou une position déjà sale au premier regard sort
du lot « exact ».

Le sidecar n'écrit que dans `data/shadow_nobe/`. Il ne peut pas passer en
LIVE : la constante `live_capable` est fausse, et un mode autre que PAPER
est refusé.

## Conséquences

- La boucle n'a pas à redémarrer. Le sidecar se lance à côté.
- Tant que la position source est ouverte, aucun appel réseau supplémentaire :
  le prix est celui déjà publié dans `state.json`.
- L'ombre peut survivre à la position réelle. C'est voulu. Ça ne libère pas
  et ne consomme pas le capital du bras.
- Un crash reprend le journal des événements. La même observation ne crée
  pas une seconde jambe.
- Effacer `data/shadow_nobe/` annule la mesure. Rien d'autre.

Lancer, depuis la racine du dépôt :

```bash
python3 scripts/shadow_nobe.py --once
python3 scripts/shadow_nobe.py --interval 5
```

Contrôle : `data/shadow_nobe/heartbeat.json` et `paired_stats.json`.
Arrêt : Ctrl-C. Le fichier `sidecar.pid` part avec le processus.
