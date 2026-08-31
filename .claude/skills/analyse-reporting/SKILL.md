---
name: analyse-reporting
description: Analyser les résultats de CryptobBot et produire un rapport honnête — scripts/analyse_rejets, analyse_sorties, analyse_shadow, audit, rapport_hebdo, export_vault. Utilise ce skill dès que la demande est « comment va le bot », « quel bras marche », « pourquoi il ne trade pas », « fais-moi un rapport », « est-ce que ce changement a aidé », ou toute lecture de performance, d'entonnoir, de win rate, de profit factor ou d'IC95 — y compris pour une réponse rapide, car la faute la plus courante ici est de conclure sur un échantillon qui ne permet rien.
---

# Analyse et reporting

Tous les scripts se lancent **depuis la racine, en module** :
`python -m scripts.<nom>`.

| script | question à laquelle il répond |
|---|---|
| `analyse_rejets` | où meurent les candidats, bras par bras (`--heures N`, `--bras NOM`) |
| `analyse_sorties` | perdants, gagnants, atteignabilité des seuils, grille comparative (`--bras`, `--sections`) |
| `analyse_shadow` | ce que les filtres ont coûté, IC95 par famille (`--bras`, `--seuil N`) |
| `audit` | intervalles de confiance, walk-forward, coût, corrélation |
| `rapport_hebdo` | rapport hebdomadaire multi-bras (`--jours N`, `--bras NOM`) |
| `export_vault` | journal → notes Obsidian reliées (`--vault CHEMIN`) |
| `verifie_outils` | dérive de version des CLI externes vs `tools.lock.json` |

`mesure_glissement.py` est obsolète et redirige vers `analyse_sorties.py`.

## Les règles de lecture

**Compter les trades avec `read_final_exits()`.** Les jambes TP1/TP2 écrivent
des lignes intermédiaires ; les compter double le nombre de trades et gonfle
le win rate.

**Lire par bras, jamais un fichier unique.** `settings.arm_paths(nom)` donne
les chemins d'un bras. Un chiffre global sans bras nommé a déjà été présenté
comme « le bot » alors qu'il ne montrait que le témoin.

**Dire la taille d'échantillon avec le chiffre.** « WR 45 % » sur 11 trades
n'est pas un résultat. Les seuils du dépôt : 15 trades par bras avant de juger,
10 par segment avant d'ajuster, 20 signaux avant de juger un agent, IC95
disjoints avant de déclarer un bras meilleur que le témoin.

**Ne pas conclure d'un bras qui ne trade pas qu'il est mauvais.** Un bras dont
la fenêtre âge/liquidité est vide ne trade pas ; `analyse_rejets` dit lequel des
deux cas on regarde. Et l'entonnoir seul désigne souvent le mauvais coupable :
le motif dominant est celui qui coupe **en dernier**, pas nécessairement celui
qui coûte le plus.

**Rappeler l'optimisme résiduel du P&L papier.** Le coût de sortie n'est déduit
que sur la jambe finale ; les jambes partielles restent au prix nu. Sur 20 K de
liquidité, l'aller-retour coûte plusieurs pour cent en réel (médiane 3,06 %).
Un rapport qui annonce un P&L sans cette réserve est trompeur.

**Le shadow trading arbitre des seuils, jamais une performance** (ADR 007) :
pas de slippage, et une entrée réelle aurait bougé le prix.

## Profondeur et durée du drawdown

`portfolio.stats()` porte deux grandeurs distinctes, à ne pas confondre :
`max_drawdown_pct` dit **jusqu'où** un bras est descendu, `longest_drawdown`
dit **combien de temps** il est resté sous son plus haut (`src/core/stats.py`,
`drawdown_episodes` / `longest_drawdown`).

Sur quelques dizaines de trades, la profondeur maximale tient à une ou deux
positions — c'est presque une statistique d'extrême. La durée agrège tout
l'intervalle, elle est plus stable, et c'est elle qui répond à « faut-il
attendre ce bras ou l'arrêter ». Le plus long épisode et le plus profond sont
rarement le même.

`recovered: false` signale un drawdown **encore en cours** : la durée affichée
est celle observée à ce jour, pas une durée finale. Un rapport qui la présente
comme définitive ment. Et `longest_drawdown: null` veut dire « jamais descendu
sous son pic », jamais « épisode de durée nulle ».

## Forme d'un rapport

1. Période, bras couverts, **nombre de trades** — avant tout chiffre.
2. Ce que les données permettent de conclure.
3. Ce qu'elles **ne** permettent pas de conclure, dit explicitement.
4. La commande exacte qui reproduit chaque chiffre.

Distinguer toujours « rien à signaler » de « pas assez de données pour
signaler ».
