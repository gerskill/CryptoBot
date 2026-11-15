#!/bin/sh
# Sauvegarde quotidienne (LaunchAgent com.cryptobbot.backup) : backup.sh + copie dans ~/Backups/CryptobBot
set -e
cd "$HOME/Projects/CryptobBot"
OUT=$(sh scripts/backup.sh)
echo "$(date) $OUT"
D=$(echo "$OUT" | sed -n 's/^Sauvegarde -> backups\///p')
[ -n "$D" ] && mkdir -p "$HOME/Backups/CryptobBot" && cp -a "backups/$D" "$HOME/Backups/CryptobBot/" && echo "Copie -> ~/Backups/CryptobBot/$D"
