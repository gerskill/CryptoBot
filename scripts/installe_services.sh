#!/bin/sh
# Installe le bot, le site, la simulation Liquid, la veille et la publication iPhone comme services
# launchd (macOS) : ils redémarrent seuls après un plantage ou un reboot.
#
# POURQUOI. Tout tournait lancé à la main sur le Mac mini : un redémarrage
# arrêtait tout sans prévenir, et le bot a tourné des jours sur un ancien code.
# Gratuit, local, sans hébergeur : voir docs/HEBERGEMENT.md.
#
# Usage (depuis la racine du dépôt) :
#   ./scripts/installe_services.sh                    # les cinq services
#   ./scripts/installe_services.sh bot veille         # seulement ceux-là
#   ./scripts/installe_services.sh --desinstalle      # retire les cinq
#   ./scripts/installe_services.sh --simule DOSSIER   # écrit les .plist sans rien charger
#
# GARDE-FOU. Refuse de démarrer un service dont une copie lancée à la main
# tourne déjà : deux boucles écriraient le même journal (le verrou de
# src/core/lock.py ferait échouer la seconde en boucle), deux sites se
# disputeraient le port 8000. Le script n'arrête RIEN lui-même.
set -eu

PROJET=$(cd "$(dirname "$0")/.." && pwd)
MODELES="$PROJET/scripts/launchd"
AGENTS="$HOME/Library/LaunchAgents"
TOUS="bot ui prediction veille publie"
SIMULE=""
DESINSTALLE=0

# Python du projet : le venv s'il existe, sinon python3 du PATH.
if [ -x "$PROJET/.venv/bin/python" ]; then
	PYTHON="$PROJET/.venv/bin/python"
else
	PYTHON=$(command -v python3 || true)
fi

SERVICES=""
while [ $# -gt 0 ]; do
	case "$1" in
		--desinstalle) DESINSTALLE=1 ;;
		--simule) shift; SIMULE="${1:?--simule demande un dossier}" ;;
		bot|ui|prediction|veille|publie) SERVICES="$SERVICES $1" ;;
		*) echo "Argument inconnu : $1" >&2; exit 2 ;;
	esac
	shift
done
[ -n "$SERVICES" ] || SERVICES="$TOUS"

motif() {
	case "$1" in
		bot) echo "src\.main" ;;
		ui) echo "scripts/run_ui\.py" ;;
		prediction) echo "scripts/run_prediction\.py" ;;
		veille) echo "scripts/veille\.py" ;;
		publie) echo "scripts/publie_supabase\.py" ;;
	esac
}

genere() {
	# Remplacement en Python : les chemins et le PATH sont échappés pour XML
	# (un « & » dans un dossier casserait le .plist avec sed).
	"$PYTHON" - "$MODELES/com.cryptobbot.$1.plist.modele" "$2" "$PROJET" "$PYTHON" "$PATH" <<'PY'
import sys
from xml.sax.saxutils import escape
modele, sortie, projet, python, path = sys.argv[1:6]
texte = open(modele, encoding="utf-8").read()
for cle, valeur in (("__PROJET__", projet), ("__PYTHON__", python), ("__PATH__", path)):
    texte = texte.replace(cle, escape(valeur))
open(sortie, "w", encoding="utf-8").write(texte)
PY
}

if [ -z "$PYTHON" ]; then
	echo "python3 introuvable." >&2; exit 1
fi

if [ -n "$SIMULE" ]; then
	mkdir -p "$SIMULE"
	for s in $SERVICES; do
		genere "$s" "$SIMULE/com.cryptobbot.$s.plist"
		echo "écrit : $SIMULE/com.cryptobbot.$s.plist"
	done
	exit 0
fi

if [ "$(uname)" != "Darwin" ]; then
	echo "launchd n'existe que sur macOS. Sur Linux, voir docs/HEBERGEMENT.md (systemd)." >&2
	exit 1
fi

DOMAINE="gui/$(id -u)"

if [ "$DESINSTALLE" -eq 1 ]; then
	for s in $SERVICES; do
		launchctl bootout "$DOMAINE/com.cryptobbot.$s" 2>/dev/null && echo "arrêté : $s" || true
		rm -f "$AGENTS/com.cryptobbot.$s.plist"
	done
	exit 0
fi

# Garde-fou : une copie manuelle tourne-t-elle ? (hors service déjà installé)
BLOQUE=0
for s in $SERVICES; do
	if launchctl print "$DOMAINE/com.cryptobbot.$s" >/dev/null 2>&1; then
		continue  # déjà un service : il sera remplacé proprement ci-dessous
	fi
	if pgrep -f "$(motif "$s")" >/dev/null 2>&1; then
		echo "⚠ $s tourne déjà hors launchd (PID $(pgrep -f "$(motif "$s")" | tr '\n' ' '))." >&2
		BLOQUE=1
	fi
done
if [ "$BLOQUE" -eq 1 ]; then
	echo "Arrête d'abord ces processus (Ctrl+C dans leur terminal), puis relance ce script." >&2
	exit 1
fi

# `launchctl bootout` rend la main avant que le service soit vraiment déchargé : relancer
# aussitôt donne « Bootstrap failed: 5: Input/output error » et laisse le service arrêté
# (vu le 2026-11-21 sur ui puis prediction). On attend donc la fin du déchargement, puis
# on retente quelques fois ; un service qui échoue n'arrête pas les autres.
attend_decharge() {
	n=0
	while launchctl print "$DOMAINE/com.cryptobbot.$1" >/dev/null 2>&1; do
		n=$((n + 1))
		[ "$n" -le 30 ] || return 1
		sleep 1
	done
}

charge() {
	essai=1
	while ! launchctl bootstrap "$DOMAINE" "$1" 2>/dev/null; do
		[ "$essai" -lt 4 ] || return 1
		essai=$((essai + 1))
		sleep 2
	done
}

mkdir -p "$AGENTS" "$PROJET/logs"
ECHECS=""
for s in $SERVICES; do
	cible="$AGENTS/com.cryptobbot.$s.plist"
	genere "$s" "$cible"
	plutil -lint "$cible" >/dev/null
	launchctl bootout "$DOMAINE/com.cryptobbot.$s" 2>/dev/null || true
	attend_decharge "$s" || echo "⚠ $s met du temps à se décharger, j'essaie quand même." >&2
	if charge "$cible"; then
		echo "démarré : $s  (journal : logs/$s.log)"
	else
		echo "✗ $s n'a pas démarré (launchctl bootstrap a échoué 4 fois)." >&2
		ECHECS="$ECHECS $s"
	fi
done
echo "État : launchctl list | grep cryptobbot"
if [ -n "$ECHECS" ]; then
	echo "Services à relancer :$ECHECS" >&2
	exit 1
fi
