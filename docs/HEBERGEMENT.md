# Hébergement gratuit — bot, site et données

Date : 2026-11-17. Mode PAPER uniquement.

## Ce qui tourne aujourd'hui (constaté, pas supposé)

- **Tout tourne sur le Mac mini** : la boucle memecoin (`python -m src.main`),
  le site (`scripts/run_ui.py`, port 8000, 127.0.0.1) et la simulation Liquid
  (`scripts/run_prediction.py`). Les données sont dans `data/` sur ce Mac.
- **Cloudflare ne sert que de tunnel** pour voir le site depuis l'extérieur :
  un *quick tunnel* (`cloudflared tunnel --url …`, adresse en
  `*.trycloudflare.com` notée dans `logs/tunnel.log`). Ce tunnel est
  **gratuit**, mais son adresse change à chaque redémarrage.
- Rien dans le dépôt n'utilise de service Cloudflare payant (pas de Worker,
  pas de `wrangler`). Si une facture arrive, elle vient d'ailleurs : vérifier
  *dash.cloudflare.com → Manage Account → Billing* (domaine, Workers Paid à
  5 $/mois, Zero Trust payant) et résilier ce qui n'est pas utilisé.
- Point faible réel : **rien ne redémarre seul**. Un reboot du Mac arrête
  tout, sans prévenir.

## Le besoin

| composant | contrainte |
|---|---|
| boucle memecoin | processus permanent (cycle 90 s, surveillance 5 s) — exclut le serverless |
| site FastAPI | petit, lit les fichiers de `data/` |
| `data/` | journaux en ajout continu, **mémoire du bot** — disque persistant obligatoire |

## Options gratuites comparées

| option | coût | disponibilité | données | complexité | verdict |
|---|---|---|---|---|---|
| **Mac mini + launchd + Tailscale** | 0 € (électricité) | celle de la box et du Mac | locales, `backup_daily.sh` existe | faible | **recommandé** |
| Oracle Cloud Free Tier (ARM Ampere) | 0 €, carte bancaire demandée | bonne | disque persistant | moyenne (Linux, systemd, pare-feu) | plan B si le Mac doit s'éteindre |
| Fly.io / Railway / Render | plus de vrai gratuit 24/7 avec volume | — | volume payant | faible | écarté |
| Serverless (Workers, Lambda, Vercel) | — | — | pas de disque | — | incompatible avec une boucle permanente |

Pièges d'Oracle Free Tier : instances « Always Free » parfois récupérées si
jugées inactives, capacité ARM souvent indisponible à la création, et IP
publique à protéger. Les clés (`.env`) y vivraient chez un tiers.

## Recommandé : rester sur le Mac mini, mais en services

### 1. Redémarrage automatique (launchd)

```bash
./scripts/installe_services.sh
```

Installe quatre LaunchAgents (`scripts/launchd/*.plist.modele`) :

| service | rôle | relance |
|---|---|---|
| `com.cryptobbot.bot` | boucle memecoin | permanente, 1 relance/min max |
| `com.cryptobbot.ui` | site sur 127.0.0.1:8000 | permanente |
| `com.cryptobbot.prediction` | simulation Liquid | permanente |
| `com.cryptobbot.veille` | alerte Telegram si un service se tait | toutes les 5 min |

Le script **refuse** de démarrer un service dont une copie lancée à la main
tourne encore (deux boucles = journal faussé) : arrêter d'abord les terminaux
ouverts. Journaux dans `logs/<service>.log`. Retrait :
`./scripts/installe_services.sh --desinstalle`.

### 2. Le Mac ne doit pas dormir

*Réglages Système → Énergie* : empêcher la mise en veille automatique, et
activer « Démarrer automatiquement après une panne de courant ». En
ligne de commande : `sudo pmset -a sleep 0 autorestart 1`.

### 3. Être prévenu (veille)

`scripts/veille.py` envoie un message Telegram (même bot que les alertes de
trade) quand le bot, le site ou Liquid cessent de donner signe de vie, puis
quand ils reviennent. Pour le cas « Mac éteint » qu'il ne peut pas voir :
créer un check gratuit sur healthchecks.io (période 5 min, tolérance 15 min)
et mettre son adresse dans `.env` :

```
HEALTHCHECK_PING_URL=https://hc-ping.com/<uuid>
```

**Vérifier que les alertes arrivent sur le téléphone** (à refaire après chaque
changement de `.env`) :

1. Message de test, sans rien casser :
   `python scripts/veille.py --test`. Un message « test de la veille » doit
   arriver sur Telegram ; sinon la commande dit quelle variable manque.
2. Vraie panne, sur le site seulement (le bot n'est pas touché) :
   `launchctl bootout gui/$(id -u)/com.cryptobbot.ui`. Dans les 5 min :
   « 🔴 ui : EN PANNE » sur Telegram, et une alerte « Panne » dans l'app.
3. Remise en route : `./scripts/installe_services.sh ui`. Au passage suivant :
   « ✅ ui : rétabli après N min ».

La veille vérifie aussi la **santé des données** (`donnees`) : une source
coupée envoie un message, un simple repli non (voir `src/core/sante_donnees.py`).

### 4. Accès depuis le téléphone

- **Tailscale** (gratuit, recommandé) : le téléphone rejoint le réseau privé
  du Mac, le site reste **invisible d'Internet**. `http://<nom-du-mac>:8000`
  nécessite que `run_ui.py` écoute sur l'IP Tailscale ; le plus simple est
  `tailscale serve` (`tailscale serve --bg 8000` sur les versions récentes),
  qui garde uvicorn sur 127.0.0.1.
- **Tunnel Cloudflare rapide** (gratuit, actuel) : public, adresse changeante.
  Le jeton `DASHBOARD_TOKEN` est alors **obligatoire**.
- Tunnel Cloudflare nommé : adresse fixe, mais demande un domaine (~10 €/an).

### 5. Installer le site comme une appli (portable, téléphone)

Le dashboard a un manifeste (`dashboard/public/manifest.webmanifest`) et des
icônes : il s'installe comme une appli, avec sa propre fenêtre et son icône.

1. Sur le Mac mini, après `git pull` : `cd dashboard && npm run build`
   (`dist/` n'est pas versionné), puis relancer le service `ui`.
2. Donner au site une **adresse fixe en HTTPS** — l'installation l'exige, et une
   appli installée sur une adresse `trycloudflare.com` casse au prochain
   redémarrage du tunnel. Le plus simple : Tailscale sur le Mac et sur le
   portable, puis `tailscale serve --bg 8000` sur le Mac (activer « HTTPS
   Certificates » et MagicDNS dans la console Tailscale). Adresse obtenue :
   `https://<nom-du-mac>.<tailnet>.ts.net`.
3. Sur le portable, ouvrir cette adresse puis :
   - Chrome / Edge / Brave : icône « Installer » dans la barre d'adresse
     (ou menu ⋮ → *Caster, enregistrer et partager* → *Installer la page en tant qu'appli*).
   - Safari (Mac) : *Fichier → Ajouter au Dock*.
   - iPhone (Safari) : *Partager → Sur l'écran d'accueil*. Android (Chrome) : ⋮ → *Installer l'appli*.

Le jeton n'est pas mémorisé (choix de sécurité de `TokenGate`) : il se
ressaisit à chaque ouverture de l'appli pour les pages qui l'exigent.

### Jeton et tunnel — à ne pas casser

Derrière un tunnel (cloudflared, ngrok, `tailscale serve`), les requêtes
extérieures arrivent sur uvicorn **depuis 127.0.0.1**. Toute idée du type
« pas de jeton en local » ouvrirait donc `/api/params` (la stratégie complète)
à Internet. Pour lire un bras sans jeton, passer par les fichiers :

```bash
python scripts/voir_bras.py quality             # règles en vigueur
python scripts/voir_bras.py quality --diff      # écarts avec le témoin
python scripts/voir_bras.py quality --perf 30d  # P&L du bras sur 30 jours
```

## Plan B : Oracle Cloud Free Tier (Linux, systemd)

Même découpage en services. Exemple pour la boucle, à adapter pour `ui`,
`prediction` et un *timer* pour `veille` :

```ini
# /etc/systemd/system/cryptobbot-bot.service
[Unit]
Description=CryptobBot boucle memecoin (PAPER)
After=network-online.target

[Service]
WorkingDirectory=/opt/CryptobBot
ExecStart=/opt/CryptobBot/.venv/bin/python -m src.main
Restart=always
RestartSec=60
User=cryptobbot

[Install]
WantedBy=multi-user.target
```

Avant de migrer : `./scripts/backup.sh`, copier `data/` et `.env` (perms 600)
par `scp`, ne **jamais** ouvrir le port 8000 dans le pare-feu Oracle — accès
par Tailscale uniquement.
