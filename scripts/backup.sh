#!/bin/sh
# Sauvegarde horodatée des données non versionnées (journal de trades, cache).
# À lancer avant toute opération risquée.
set -e
DEST="backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"
cp -a data "$DEST/" 2>/dev/null || true
cp -a config "$DEST/" 2>/dev/null || true
cp .env "$DEST/env.backup" 2>/dev/null || true
chmod -R go-rwx "$DEST"
echo "Sauvegarde -> $DEST"
