# CryptobBot — MemeCoin Alpha Loop

Bot de scan et de trading papier de meme coins Solana, avec boucle
d'auto-amélioration. **Mode PAPER : aucune transaction réelle n'est émise.**

## Lancer

```bash
python -m src.main                              # la boucle
uvicorn api.server:app --reload --port 8000     # l'API du dashboard
python -m unittest discover -s tests            # les 361 tests
```

## Règles non négociables

- **Jamais d'exécution de trade réel** sans module d'exécution explicite,
  revu et validé. Le passage en LIVE est verrouillé par
  `LearningEngine.live_mode_allowed()` : 20 trades papier, WR > 40%, PF > 1.5.
- **Aucune clé en dur.** Tout passe par `.env`, jamais commité, perms 600.
  Un hook pre-commit bloque les commits contenant `.env` ou une clé en clair.
- **Sauvegarder avant toute opération risquée** : `./scripts_backup.sh`.
  `data/` contient le journal de trades — c'est la mémoire du bot, et il
  n'est pas versionné.
- **Jamais de flag `--yes` / `-y`** sur une commande qui peut supprimer.
  Un scaffold se fait dans un dossier vide créé exprès, jamais à la racine.

## Agent skills

### Issue tracker

Issues en markdown local sous `.scratch/<feature>/`, pas de service externe.
Voir `docs/agents/issue-tracker.md`.

### Triage labels

Vocabulaire canonique sans renommage, porté par le champ `status` de
l'en-tête YAML de chaque issue. Voir `docs/agents/triage-labels.md`.

### Domain docs

Mono-contexte : `CONTEXT.md` et `docs/adr/` à la racine, partagés par le bot
et le dashboard. Voir `docs/agents/domain.md`.
