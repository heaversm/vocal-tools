#!/usr/bin/env bash
# Starts the tools and opens the page. Double-click in Finder, use the Vocal Tools app, or run ./start.command
cd "$(dirname "$0")"
PORT="${PORT:-8010}"
URL="http://localhost:$PORT"
open_page() { command -v open >/dev/null && open "$URL" || xdg-open "$URL"; }

if [ ! -x .venv/bin/python ]; then
  echo "Not installed yet. Run ./setup.sh first."
  read -r -p "Press Enter to close."
  exit 1
fi
if curl -s -o /dev/null "$URL"; then
  echo "Already running. Opening $URL"
  open_page
  exit 0
fi
( sleep 2; open_page ) >/dev/null 2>&1 &
echo "Vocal tools running on $URL  (close this window or press Ctrl+C to stop)"
exec .venv/bin/python -I server.py "$PORT"
