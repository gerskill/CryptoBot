#!/bin/sh
# Sauvegarde quotidienne (LaunchAgent com.cryptobbot.backup) : backup.sh + copie hors du projet
# Depuis le 22/11/2026 : copie sur le SSD si branché, sinon repli sur ~/Backups/CryptobBot
set -e
cd "$HOME/Projects/CryptobBot"
OUT=$(sh scripts/backup.sh)
echo "$(date) $OUT"
D=$(echo "$OUT" | sed -n 's/^Sauvegarde -> backups\///p')
SSD="/Volumes/PortableSSD/Sauvegardes-Mac/CryptoBot/quotidiennes"
if [ -d "$SSD" ]; then DEST="$SSD"; else DEST="$HOME/Backups/CryptobBot"; mkdir -p "$DEST"; fi
[ -n "$D" ] && cp -a "backups/$D" "$DEST/" && echo "Copie -> $DEST/$D"
