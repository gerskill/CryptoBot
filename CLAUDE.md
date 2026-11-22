# CryptobBot — consignes pour Claude Code

Lire aussi `AGENTS.md` et `CONTEXT.md`. Mode **PAPER uniquement** : aucune transaction réelle.

---

## 🧑‍⚖️ Juge et 📒 Chef de projet (ajouté le 2026-11-01)

### Avant d'annoncer un résultat
- Avant d'annoncer **tout** résultat (backtest, simulation papier, P&L, rapport chiffré), appelle le sous-agent **juge** (`.claude/agents/juge.md`) et attends son verdict.
- Si le verdict est **REFUSÉ**, ne présente pas le résultat comme valide : corrige ou marque-le [À TESTER].
- Recopie toujours le verdict du juge (VALIDÉ / REFUSÉ + raisons) dans ta réponse.

### Journal automatique (chef de projet)
À la **fin de chaque session**, ajoute (sans jamais effacer ni réécrire ce qui existe) une entrée datée **à la fin** du fichier :
`~/Documents/Obsidian Vault/CryptoBot/Journal.md`

Format de l'entrée :
```
## AAAA-MM-JJ — titre court
- **Testé :** …
- **Décisions :** …
- **Verdict du juge :** VALIDÉ / REFUSÉ / aucun résultat à juger
- **Prochaines étapes :** …
```
Utilise uniquement un ajout en fin de fichier (par ex. `cat >> fichier <<'FIN'`), jamais `>` ni un outil qui réécrit le fichier entier.

---

## 🎯 Discipline d'exécution (ajout 2026-11-02)

- **Contrat de tâche :** avant de commencer une tâche, écris en une ligne « **Fini quand :** … » avec un critère vérifiable (ex. tests verts, backtest lancé avec rapport). N'annonce jamais « fini » si ce critère n'est pas vérifié, puis passe par le **juge** comme prévu ci-dessus.
- **Limite d'essais :** une même commande ou correction qui échoue est retentée **3 fois maximum**. Ensuite **STOP** : résume l'erreur et demande à Killian. Pas de boucle d'appels API.
- **Fichier de passation :** en fin de session (ou avant de changer de conversation), crée/mets à jour `HANDOFF.md` à la racine du projet : objectif en cours, décisions confirmées, fichiers concernés, questions ouvertes, prochaine action. 400 mots max, liens vers les preuves plutôt que copies. En début de nouvelle session, lis `HANDOFF.md` d'abord.

## 📊 Dashboard officiel (décidé par Killian le 2026-11-22)

- Dashboard officiel : **http://127.0.0.1:8002** (service launchd `com.cryptobbot.ui-nouveau`, build `dashboard/dist`, Memecoins + Liquid).
- Quand Killian demande « le dashboard », ouvrir/afficher **:8002**.
- Ne pas remettre l'ancien War Room (build du 11/10/2026) sur :8000. Il est archivé dans `~/Backups/dashboard-20261122-1205/dist.ancien-warroom-20261011`.
- Le port 8000 n'est plus utilisé : l'ancien service `com.cryptobbot.ui` est retiré, la veille (`scripts/veille.py`) surveille `http://127.0.0.1:8002/api/health`, et `scripts/installe_services.sh` installe `ui-nouveau`.
- Après `npm run build` dans `dashboard/` : `launchctl kickstart -k gui/$(id -u)/com.cryptobbot.ui-nouveau`.
