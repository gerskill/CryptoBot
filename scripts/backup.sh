#!/bin/sh
# Sauvegarde horodatée des données non versionnées (journal de trades, cache).
# À lancer avant toute opération risquée.
set -e
KEEP=15  # snapshots conservés ; au-delà, purge des plus anciens (FIFO)
DEST="backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"
cp -a data "$DEST/" 2>/dev/null || true
cp -a config "$DEST/" 2>/dev/null || true
cp .env "$DEST/env.backup" 2>/dev/null || true
chmod -R go-rwx "$DEST"
echo "Sauvegarde -> $DEST"

# Purge : ne garder que les $KEEP snapshots les plus récents. Uniquement les
# dossiers horodatés (arms-v1 et les .tgz manuels ne sont jamais comptés).
count=$(find backups -maxdepth 1 -type d -name '2*' | wc -l | tr -d ' ')
if [ "$count" -gt "$KEEP" ]; then
  find backups -maxdepth 1 -type d -name '2*' | sort | head -n "$((count - KEEP))" | while read -r old; do
    echo "Purge -> $old"
    rm -rf "$old"
  done
fi
