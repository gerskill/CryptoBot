# Conception — B1 : positions « zombies » (sans prix)

Date : 2026-11-18. Approuvée par Killian le 2026-11-18. Mode PAPER.
Origine : `docs/AUDIT_2026-11-17.md`, constat B1 (vérifié).

## Intention

Quand aucune source ne donne de prix exploitable, `_monitor_arm` saute la
position (`src/main.py`, `if not quote_is_usable(price): … continue`) AVANT le
time stop et la détection de rug ; `/STOP` la laisse aussi ouverte. Elle peut
rester ouverte indéfiniment et, sans jambe finale, sort de tous les chiffres
(`read_positions()`). Les tokens morts ou rugués sont justement ceux qui
perdent leur prix : les pires pertes sont invisibles (7 positions bloquées
documentées, dont une à -99 %).

## Décisions (Killian)

- Une position sans prix est comptée à **-100 %** de sa part restante quand
  on la ferme d'office : pire cas honnête, ne flatte jamais un bras.

## Approches

| approche | coût | retenue |
|---|---|---|
| **Fermer dans `_monitor_arm`** quand prix inutilisable ET âge > time stop + délai de grâce | aucun état à mémoriser, survit aux redémarrages | **oui** |
| Balayeur séparé | second chemin d'écriture dans le portefeuille | non |
| Compteur « sans prix depuis » en mémoire | perdu au redémarrage | non |

## Conception

1. `ZOMBIE_GRACE_MINUTES = 30` (constante commentée). Dans `_monitor_arm`, si
   le prix est inutilisable ET `position.duration_minutes() >=
   position.max_hold_time_minutes + ZOMBIE_GRACE_MINUTES`, la position est
   fermée à -100 %, raison `ZOMBIE (sans prix depuis le time stop)`. Avec un
   prix, le time stop l'aurait déjà fermée : encore ouverte 30 min après,
   elle est forcément bloquée.
2. Fermeture : nouvelle méthode du portefeuille (le chemin `force_close`
   refuse un prix ≤ 0). Aucun devis (token invendable) : coût de sortie
   `None`, P&L de la jambe -100 % de la part restante, capital crédité de 0.
   Les jambes TP1/TP2 déjà encaissées restent comptées (`read_positions()`).
3. `/STOP` (`_panic_exit`) ferme aussi les positions sans prix à -100 %,
   raison `PANIC_STOP (sans prix)`, au lieu de « position NON fermée ».
4. Les zombies existants seront fermés au premier cycle après déploiement :
   baisse brutale et voulue des chiffres des bras concernés.
5. Invariants : la boucle ne meurt jamais ; écriture atomique des positions
   (`save_positions`) ; aucune autre règle de sortie modifiée ; la raison
   `ZOMBIE` est reconnue comme automatique (pas « fermeture manuelle ») dans
   `src/core/stats.py::RAISONS_AUTOMATIQUES` et `scripts/rapport_hebdo.py`.

## Hors périmètre
Afficher la perte latente des positions ouvertes dans les chiffres.
