# Rapport d'analyse — retour de vacances (2026-08-30)

## 1. État général

**Périmètre vérifié** : dépôt (branche `claude/bot-analysis-report-0smiqr` alignée
sur `main`, arbre de travail propre), configuration, suite de tests.
**Périmètre non vérifiable ici** : l'analyse tourne sur un clone neuf — pas de
`data/`, pas de `logs/`, pas de `.env`, aucun processus en cours. Rien ne peut
donc être affirmé sur l'exécution réelle du bot ; c'est la vérification P0.

Le code est sain : **791 tests, tous verts** une fois `fastapi` installé. Les 9
erreurs de la première exécution étaient toutes le même
`ModuleNotFoundError: fastapi` — dépendance absente de l'environnement, pas un
défaut de code ; l'installer les fait disparaître, c'est vérifié et non déduit.
27 `except Exception`, aucun silencieux.

Dernier commit : 2026-08-17 17:04. **13 jours sans activité**, cohérent avec
l'absence.

## 2. Vérifications, par priorité

### P0 — La boucle tourne-t-elle encore ?

*Observation.* Le verrou d'instance utilise `flock` : le noyau le libère à la
mort du process. Un arrêt (crash, OOM, reboot) ne laisse donc aucun verrou
résiduel et ne s'annonce pas.

*Proposition.* `ps aux | grep src.main`, date de la dernière ligne de
`data/trades_log.jsonl`, dernier message Telegram. Dater l'arrêt **avant** toute
relance : une reprise efface l'indice.

### P0 — Croissance des fichiers sur 13 jours

*Observation.* Seul `funnel_log.jsonl` est borné (rotation à 8 Mo).
`trades_log.jsonl`, les shadow logs et les journaux d'agents sont append-only
sans borne.

*Proposition.* `du -sh data/`, puis `./scripts/backup.sh` avant toute
manipulation. Étendre la rotation de `funnel.py` aux autres journaux.

### P1 — Quatre gardes livrées la veille du départ

*Observation.* Filtre LP verrouillée, watchdog anti-slow-rug, concentration
sectorielle, échelle de sortie à N barreaux : tous commités le 17/08, jamais
observés sur une longue série.

*Proposition.* `python3 -m scripts.analyse_rejets` et `scripts.analyse_sorties`
pour mesurer ce que chaque garde a réellement écarté, avant d'en toucher les
seuils.

### P1 — Piège latent sur `capital_pct`

*Observation.* La somme sur les 8 bras du manifeste vaut 1,05.
`bootstrap_arms()` ne somme que les bras **actifs** : 1,0000 exactement, parce
que `narrative` (0,05) est désactivé. Réactiver `narrative` ferait échouer le
démarrage (`ManifestError`).

*Proposition.* Rééquilibrer au moment de la réactivation, pas au moment de
l'incident.

### P1 — Shadow tracking perdu au redémarrage

*Observation.* `ShadowTracker._tracked` vit en mémoire. Si la boucle a
redémarré pendant l'absence, tous les suivis de moins de 4 h sont perdus. Le
bras `sniper_young` (créé le 09/08) est le plus exposé.

*Proposition.* Persister `_tracked` ; d'ici là, lire les compteurs shadow comme
un plancher, jamais comme un total.

### P2 — Documentation en retard sur le code

*Observation.* `ETAT_DU_PROJET.md` date du 07/08 : 14 commits plus tard, il
ignore `sniper_young` (8ᵉ bras, absent du §13.2) et annonce encore comme non
résolu le ratchet du tampon de glissement, corrigé les 09/08 et 15/08.
`AGENTS.md` annonce 361 tests contre 760 réels.

*Proposition.* Une passe de mise à jour avant tout nouveau développement.

## 3. Conclusion

Trois actions, dans l'ordre : confirmer que la boucle tourne et dater tout
arrêt ; sauvegarder puis mesurer `data/` ; qualifier les gardes du 17/08 sur
données réelles. Le reste — bornes de `capital_pct`, persistance du shadow,
documentation — est de la dette identifiée, pas du risque immédiat. Aucun
élément observé ne remet en cause le verrou PAPER.

---

## 4. Suites données le 2026-08-30

Trois points de ce rapport sont traités dans le code, trois demandent la machine
où tourne la boucle.

### Traité

- **P1 `capital_pct`** — avertissement au démarrage nommant le bras dormant et le
  total du manifeste. Le refus sur les bras actifs reste fatal, inchangé.
- **P1 shadow tracking** — les suivis en cours sont persistés par bras et
  survivent à un redémarrage. Un suivi périmé pendant une coupure est **écarté,
  pas jugé** : son pic date d'avant l'arrêt, le juger mesurerait la durée de la
  coupure et non le token.
- **P2 documentation** — `ETAT_DU_PROJET.md` §14 rattrape les quatorze commits du
  07/08 au 17/08 et corrige ce que §10 et §13 affirmaient encore. `AGENTS.md`
  annonce le bon nombre de tests.
- **Défaut trouvé en route, absent du rapport initial** — les rejets du filtre LP
  verrouillée tombaient dans la famille « autre », qu'aucun paramètre ne dessert :
  la garde du 17/08 était **invisible au comptage par famille**, donc impossible à
  qualifier. Familles `lp_lock` et `sector` ajoutées, sans entrée dans
  `RELAXATIONS` — visible ne veut pas dire relâchable.

### Reste à faire sur la machine du bot

Les deux P0 et la qualification des gardes ne se lisent pas dans le dépôt. Une
commande les traite, **en lecture seule** :

```
python3 -m scripts.diagnostic_reprise
```

Elle date l'arrêt avant qu'une relance ne l'efface, mesure `data/`, et compte ce
que les gardes du 17/08 ont réellement écarté. Elle se termine en imprimant la
séquence de reprise — relancer reste une décision du propriétaire.
