# Marchés de prédiction Liquid — mode d'emploi

Simulation PAPIER. Aucun pari réel n'est possible depuis ce code (ADR 015).

## Lancer

```bash
python3 scripts/run_prediction.py --sonde   # 1. vérifier ce que l'API renvoie vraiment
python3 scripts/run_prediction.py --once    # 2. un cycle, pour voir
python3 scripts/run_prediction.py           # 3. la boucle (un cycle par minute)
uvicorn api.server:app --port 8000          # l'API du dashboard
cd dashboard && npm run dev                 # http://localhost:5173, onglet « Liquid »
```

La sonde écrit la réponse brute dans `data/prediction/sonde_outcomeMeta.json`
et affiche le nombre de marchés lus par domaine. Si elle annonce « rien
d'exploitable », ne pas lancer la boucle : le format a changé.

## Les bras (`config/prediction_arms.json`)

| famille | bras | rôle | ce qu'il fait |
|---|---|---|---|
| transverse | `pm_temoin` | témoin | achète le favori (50–95 %), tous domaines, sans estimation |
| transverse | `pm_coherence` | observateur | journalise les incohérences, ne parie pas |
| transverse | `pm_consensus` | consensus | entre si deux bras estimateurs sont d'accord |
| prix | `pm_vol_edge` | estimateur | seuil crypto, distribution empirique des variations |
| prix | `pm_vol_normal` | estimateur | fork : loi log-normale au lieu de l'empirique |
| prix | `pm_cloture_actions` | estimateur | fork : actions et indices au lieu de crypto |
| prix | `pm_matieres` | estimateur | fork : or, argent, pétrole au lieu de crypto |
| prix | `pm_barriere` | estimateur | marchés « touche », principe de réflexion |
| sport | `pm_sport_favori` | règle fixe | match : achète le côté coté 60–90 % avant le coup d'envoi |
| sport | `pm_sport_outsider` | règle fixe | fork : achète le côté coté 10–40 % |
| sport | `pm_sport_reference` | désactivé | attend une source de cotes externe |
| questions | `pm_question_favori` | règle fixe | taux, introduction en bourse : côté coté 60–95 %, échéance < 30 j |
| pourcentages | `pm_quasi_certain` | règle fixe | fork du témoin : tranche 90–97 % |
| pourcentages | `pm_pile_ou_face` | règle fixe | fork du témoin : tranche 50–60 % |

Architecture et raisons : `docs/adr/016-…`. Les vainqueurs de tournoi et les
tranches de prix sont relevés mais jamais pariés.

Fichiers écrits dans `data/prediction/` à chaque cycle : `state.json`,
`catalogue.json` (tous les marchés et leurs prix), `perps.json` (marchés à
terme, affichage seul), `coherence.jsonl`, et toutes les 15 minutes
`snapshots/AAAA-MM-JJ.jsonl` (prix de tous les marchés).

Capital fictif 1 000 $ par bras, mise 2 %, plafond 10 % par domaine, un seul
pari par marché et par bras.

## Fichiers écrits

```
data/prediction/state.json                  dernier cycle, raisons de non-pari
data/prediction/arms/<bras>/open_bets.json  paris ouverts
data/prediction/arms/<bras>/bets.jsonl      paris réglés, une ligne par pari
data/prediction/prices/<N>.jsonl            prix de la pièce #N, pour le graphique
data/prediction/coherence.jsonl             incohérences relevées
```

## Contrôle tableau ↔ graphique

1. `python3 scripts/prediction_demo.py` écrit un **exemple fictif** dans
   `data/prediction_demo/`.
2. Onglet Liquid → « Voir l'exemple fictif » → cliquer un bras, puis un pari.
3. Sous le graphique, la ligne « Entrée » doit reprendre exactement la date et
   le prix de la ligne du tableau, et la ligne « Contrôle » doit afficher un
   écart de temps de 0 s avec le même prix.

L'exemple fictif n'est jamais mêlé au réel : dossier distinct, bandeau
permanent, `exemple_fictif: true` sur chaque ligne servie.

## Obsidian

```bash
python3 scripts/export_vault_prediction.py            # ~/vaults/cryptobot/Prediction
python3 scripts/export_vault_prediction.py --vault "<chemin du vault>/Prediction"
```

Une note par bras, par domaine et par pari. Le dossier est régénéré à chaque
export.

## Mettre en ligne

Le dashboard a besoin de l'API, qui lit les fichiers du Mac. Il n'est donc pas
publiable tel quel sur GitHub Pages, Netlify ou Vercel : ces hébergeurs ne
servent que des fichiers statiques.

- **Usage normal** : en local sur le Mac mini (`npm run dev`, ou
  `npm run build` puis servir `dashboard/dist` derrière l'API).
- **Depuis le téléphone** : un tunnel privé (Tailscale) vers le Mac mini, après
  avoir défini `DASHBOARD_TOKEN` dans `.env`. Ne jamais ouvrir le port 8000 sur
  Internet sans jeton.

## Avant de croire un chiffre

30 paris réglés minimum par bras, frais déduits, drawdown max et pires 5 %
affichés, paris indéterminés comptés à part, verdict du juge.
