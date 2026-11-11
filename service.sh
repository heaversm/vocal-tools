#!/usr/bin/env bash
# Runs the tools as a background service on macOS: starts at login, restarts itself if it crashes,
# and keeps running when windows are closed.
#   ./service.sh install    set it up and start it (setup.sh and start.command do this for you)
#   ./service.sh restart    restart it, e.g. after changing server.py
#   ./service.sh stop       stop it until the next login or install
#   ./service.sh uninstall  remove it
#   ./service.sh status     say whether it's running
#   ./service.sh log        show the last lines of its log
set -euo pipefail
cd "$(dirname "$0")"
LABEL="local.vocal-tools"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT="${PORT:-8010}"
LOG="$PWD/data/server.log"
DOMAIN="gui/$(id -u)"

if [ "$(uname)" != "Darwin" ]; then echo "The service is macOS-only. Use ./start.command instead."; exit 1; fi

write_plist() {
  mkdir -p "$HOME/Library/LaunchAgents" data
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PWD/.venv/bin/python</string>
    <string>-I</string>
    <string>$PWD/server.py</string>
    <string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$PWD</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>5</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
}

loaded() { launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; }
up() { curl -s -o /dev/null "http://localhost:$PORT"; }

case "${1:-status}" in
  install)
    [ -x .venv/bin/python ] || { echo "Not installed yet. Run ./setup.sh first."; exit 1; }
    loaded && launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    write_plist
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "Installed. Tools start at login on http://localhost:$PORT and restart if they crash."
    ;;
  restart) launchctl kickstart -k "$DOMAIN/$LABEL" && echo "Restarted." ;;
  stop)    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null && echo "Stopped until next login, or run ./service.sh install." || echo "Wasn't running." ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed. Use ./start.command to run the tools in a Terminal window instead."
    ;;
  status)
    if up; then echo "Running on http://localhost:$PORT"
    elif loaded; then echo "Service is set up but not answering yet. See ./service.sh log"
    else echo "Not set up. Run ./service.sh install"; fi
    ;;
  log) tail -n 40 "$LOG" 2>/dev/null || echo "No log yet." ;;
  *) echo "Usage: ./service.sh install|restart|stop|uninstall|status|log"; exit 1 ;;
esac
