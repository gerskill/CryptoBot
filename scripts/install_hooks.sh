#!/bin/sh
# Installe les hooks git versionnes du depot (scripts/git-hooks/).
# A lancer une fois par clone : git ne clone jamais les hooks, donc le barrage
# anti-secret decrit dans AGENTS.md n'existe que sur les postes ou quelqu'un
# a lance ce script.
#
# Usage : ./scripts/install_hooks.sh          installe
#         ./scripts/install_hooks.sh --check  verifie sans rien modifier
set -e

cd "$(dirname "$0")/.."
RACINE=$(pwd)
HOOKS=scripts/git-hooks

git rev-parse --git-dir >/dev/null 2>&1 || {
  echo "Erreur : $RACINE n'est pas un depot git." >&2
  exit 1
}

if [ "${1:-}" = "--check" ]; then
  actuel=$(git config --get core.hooksPath || echo "")
  if [ "$actuel" = "$HOOKS" ]; then
    echo "OK : core.hooksPath = $HOOKS"
    exit 0
  fi
  echo "Hooks NON installes (core.hooksPath = ${actuel:-<non defini>})." >&2
  echo "Lancez : ./scripts/install_hooks.sh" >&2
  exit 1
fi

chmod +x "$HOOKS"/*

# core.hooksPath masque .git/hooks : prevenir si des hooks y dorment deja.
existants=$(find .git/hooks -maxdepth 1 -type f ! -name '*.sample' 2>/dev/null || true)
if [ -n "$existants" ]; then
  echo "Attention : ces hooks de .git/hooks vont etre masques par core.hooksPath :"
  printf '%s\n' "$existants" | sed 's/^/  /'
fi

git config core.hooksPath "$HOOKS"

echo "core.hooksPath -> $HOOKS"
for h in "$HOOKS"/*; do
  [ -f "$h" ] && echo "  actif : ${h##*/}"
done
echo
echo "Verification : ./scripts/install_hooks.sh --check"
