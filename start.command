#!/usr/bin/env bash
# Opens the tools in your browser. Use the Vocal Tools app, double-click this in Finder, or run ./start.command
# On a Mac the tools run as a background service (see service.sh): no window to keep open, and they
# restart themselves if they crash. On Linux they run in this terminal until you press Ctrl+C.
cd "$(dirname "$0")"
PORT="${PORT:-8010}"
URL="http://localhost:$PORT"
up() { curl -s -o /dev/null "$URL"; }

fail() {
  echo "$1"
  if [ "$(uname)" = "Darwin" ]; then
    osascript -e "display alert \"Vocal Tools didn't start\" message \"$1\"" >/dev/null 2>&1
  fi
  exit 1
}

[ -x .venv/bin/python ] || fail "Not installed yet. In Terminal, cd to $PWD and run ./setup.sh"

if [ "$(uname)" != "Darwin" ]; then
  if up; then xdg-open "$URL"; exit 0; fi
  ( sleep 2; xdg-open "$URL" ) >/dev/null 2>&1 &
  echo "Vocal tools running on $URL  (press Ctrl+C to stop)"
  exec .venv/bin/python -I server.py "$PORT"
fi

if ! up; then
  if launchctl print "gui/$(id -u)/local.vocal-tools" >/dev/null 2>&1; then
    launchctl kickstart "gui/$(id -u)/local.vocal-tools" >/dev/null 2>&1
  else
    ./service.sh install >/dev/null || fail "Couldn't set up the background service. In Terminal, run: $PWD/service.sh install"
  fi
  for _ in $(seq 1 60); do up && break; sleep 0.5; done
  up || fail "The tools didn't respond within 30 seconds. The log is at $PWD/data/server.log"
fi
open "$URL"
