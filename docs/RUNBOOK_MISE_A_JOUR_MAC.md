# Runbook : mettre à jour le Mac mini après une fusion sur `main`

**Responsable :** Killian | **Fréquence :** à chaque PR fusionnée
**Mis à jour :** 2026-11-18 | **Dernier passage :** —

## But
Faire tourner sur le Mac le code fusionné (aujourd'hui : A2, B1, publication
iPhone) sans perdre de données ni lancer deux boucles en même temps.

## Prérequis
- [ ] Terminal sur le Mac, dans `~/Projects/CryptobBot`.
- [ ] Aucune opération manuelle en cours sur les fichiers de `data/`.
- [ ] Pour la publication iPhone : projet Supabase créé (`docs/APP_IPHONE.md`).

## Procédure

### 1. Sauvegarder
```bash
./scripts/backup.sh
```
**Attendu :** `Sauvegarde -> backups/AAAAMMJJ-HHMMSS`.
**Si ça échoue :** ne pas continuer ; vérifier l'espace disque (`df -h .`).

### 2. Arrêter ce qui tourne
Services installés :
```bash
./scripts/installe_services.sh --desinstalle
```
Sinon, Ctrl+C dans chaque terminal (bot, site, Liquid).
**Attendu :** `pgrep -fl "src.main|run_ui|run_prediction"` ne renvoie rien.
**Si un processus reste :** attendre la fin de son cycle (90 s), recommencer.

### 3. Récupérer le code
```bash
git pull
```
**Attendu :** avance rapide, sans conflit.
**Si conflit sur `config/` :** c'est l'apprentissage du Mac. Garder la version
du Mac : `git checkout --ours config/ && git add config/`, puis terminer la
fusion. Ne jamais écraser les paramètres appris par ceux du dépôt.

### 4. Dépendances
```bash
pip install -r requirements.txt
```
```bash
cd dashboard && npm ci && npm run build && cd ..
```
**Attendu :** aucune erreur ; `dashboard/dist/` régénéré.

### 5. Vérifier avant de relancer
```bash
python -m unittest discover -s tests
```
**Attendu :** `OK`. **Si échec :** ne pas relancer ; revenir en arrière (§ Retour arrière).

### 6. Relancer
```bash
./scripts/installe_services.sh
```
**Attendu :** `démarré : bot`, `ui`, `prediction`, `veille`, `publie`
(publie ne fait rien tant que les 4 variables Supabase manquent dans `.env`).

## Vérification (dans les 10 minutes)
- [ ] `tail -n 50 logs/bot.log` : cycles normaux, pas de traceback.
- [ ] **Attendu au premier cycle :** des lignes `[Monitor] … fermée à -100 %`
      (`ZOMBIE`) pour les positions restées sans prix. C'est voulu (ADR 020) :
      les chiffres des bras concernés baissent d'un coup.
- [ ] `tail -n 20 logs/bot.log | grep "recherche de sorties"` : issues
      « indéterminé », « rejeté » ou « confirmé » (ADR 019).
- [ ] Site http://127.0.0.1:8000 : onglet Weekly → « P&L par bras ».
- [ ] `python scripts/veille.py --sans-envoi` : les trois services OK.
- [ ] Si Supabase configuré : `python scripts/publie_supabase.py --sans-envoi`
      affiche 7 clés, puis l'app iPhone montre des données de moins de 3 min.

## Dépannage
| Symptôme | Cause probable | Correction |
|---|---|---|
| `verrou déjà pris` au démarrage du bot | une ancienne boucle tourne encore | `pgrep -fl src.main`, l'arrêter, relancer |
| port 8000 occupé | ancien site lancé à la main | arrêter l'ancien `run_ui.py` |
| Beaucoup de fermetures `ZOMBIE` hors premier cycle | sources de prix en panne (quota Birdeye, DexScreener) | `logs/bot.log`, vérifier les quotas avant de changer quoi que ce soit |
| `publie` : « variables manquantes » | `.env` incomplet | ajouter `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_EMAIL`, `SUPABASE_PASSWORD` |
| `publie` : HTTP 401 répété | mot de passe changé | mettre à jour `.env`, supprimer `data/supabase_session.json` |

## Retour arrière
```bash
./scripts/installe_services.sh --desinstalle
```
```bash
git checkout <commit d'avant le pull>
```
Restaurer `data/` et `config/` depuis la sauvegarde de l'étape 1
(`backups/AAAAMMJJ-HHMMSS/`) **seulement** si des fichiers ont été abîmés, puis
relancer. Note : les fermetures `ZOMBIE` déjà écrites au journal restent ; les
retirer fausserait le journal.

## Historique
| Date | Par | Notes |
|---|---|---|
| | | |
