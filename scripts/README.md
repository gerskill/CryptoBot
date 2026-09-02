# scripts/

Scripts utilitaires du dépôt (analyse, audit, export, sauvegarde). Tous les
scripts Python remontent la racine du dépôt via
`os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` pour importer
`src.*` — lancez-les depuis la racine, en module : `python -m scripts.nom`.

- `analyse_rejets.py` — Entonnoir de décision : où meurent les candidats, bras par bras (marché vide, seuil trop strict, technique qui refuse, carnet trop mince). Usage : `python -m scripts.analyse_rejets [--heures N] [--bras NOM]`.
- `analyse_sorties.py` — Rapport de sorties par bras : perdants, gagnants, atteignabilité des seuils, grille comparative. Remplace `mesure_glissement.py`. Usage : `python -m scripts.analyse_sorties [--bras NOM] [--sections LISTE]`.
- `analyse_shadow.py` — Ce que les filtres ont coûté : rejets shadow rejoués, IC95 par famille de filtre. Usage : `python -m scripts.analyse_shadow [--bras NOM] [--seuil N]`.
- `audit.py` — Audit statistique du projet : intervalles de confiance, walk-forward, coût, corrélation — ce qu'on peut conclure et ce qu'on ne peut pas. Usage : `python -m scripts.audit`.
- `export_vault.py` — Exporte le journal de trading en notes Obsidian reliées (recoupement par token, bras, raison de sortie). Usage : `python -m scripts.export_vault [--vault CHEMIN] [--bras NOM]`.
- `mesure_glissement.py` — Obsolète, remplacé par `analyse_sorties.py` ; redirige vers lui à l'exécution.
- `verifie_outils.py` — Vérifie que les CLI externes (`gmgn-cli`, `@jup-ag/cli`) correspondent aux versions épinglées dans `tools.lock.json`, pour détecter une dérive de supply chain. Usage : `python -m scripts.verifie_outils`.
- `rapport_hebdo.py` — Rapport hebdomadaire multi-bras — lit `settings.arm_paths()` par bras (jamais un fichier unique), filtre sur `timestamp_exit`, groupe par bras réel. Usage : `python -m scripts.rapport_hebdo [--jours N] [--bras NOM]`.
- `install_hooks.sh` — Installe les hooks git versionnés de `scripts/git-hooks/` en pointant `core.hooksPath` dessus. À lancer une fois par clone : git ne clone jamais les hooks, donc le barrage anti-secret n'existe que sur les postes où ce script a été lancé. Usage : `./scripts/install_hooks.sh` (ou `--check` pour vérifier sans rien modifier).
- `git-hooks/pre-commit` — Bloque la mise en index d'un `.env`, d'une variable sensible recevant une vraie valeur (y compris un `.env.example` rempli par erreur) et de quelques empreintes de secrets (clé PEM, token Telegram, bearer Twitter, tableau de clé Solana). Une lecture via `os.getenv(...)` n'est jamais bloquée. Contournement : `git commit --no-verify`.
- `diagnostic_reprise.py` — Diagnostic de reprise après une interruption : la boucle tourne-t-elle, depuis quand est-elle arrêtée, ce que `data/` a accumulé, ce que les gardes du 2026-08-17 ont écarté. **Lecture seule** — une relance reprend le verrou et écrit dans `data/`, donc efface la date de l'arrêt. Usage : `python -m scripts.diagnostic_reprise [--depuis AAAA-MM-JJ]`.
- `backup.sh` — Sauvegarde horodatée de `data/`, `config/` et `.env` dans `backups/<horodatage>/`, à lancer avant toute opération risquée. Usage : `./scripts/backup.sh` (depuis la racine du dépôt).
