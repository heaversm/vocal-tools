#!/usr/bin/env bash
# Builds "Vocal Tools.app" in ~/Applications (macOS only), pointing at this folder.
# Rerun it if you move the folder. setup.sh runs it for you.
set -euo pipefail
cd "$(dirname "$0")"
if [ "$(uname)" != "Darwin" ]; then echo "The app is macOS-only. Use ./start.command instead."; exit 0; fi
APP="$HOME/Applications/Vocal Tools.app"
mkdir -p "$HOME/Applications"
rm -rf "$APP"
osacompile -o "$APP" -e "do shell script \"open -a Terminal \" & quoted form of \"$PWD/start.command\""
echo "Built $APP (drag it to the Dock to keep it handy)."
