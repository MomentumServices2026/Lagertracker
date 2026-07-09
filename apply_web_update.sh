#!/bin/zsh
# Run after editing web_app.py in Cursor — restarts server so phone refresh picks up changes.
cd "$(dirname "$0")"

LABEL="com.momentum.inventory.web"
DOMAIN="gui/$(id -u)"

launchctl kickstart -k "$DOMAIN/$LABEL" 2>/dev/null || {
  echo "Auto-start not installed. Starting manually..."
  ./start_web_background.sh
}

sleep 2
if lsof -i :8080 -sTCP:LISTEN &>/dev/null; then
  VERSION=$(python3 -c "import os; print(int(os.path.getmtime('web_app.py')))")
  echo "Web app updated and running."
  echo "Version: $VERSION"
  echo ""
  echo "On your iPhone home screen app, tap the ↻ button to load the update."
  ./show_phone_url.sh
else
  echo "Server failed to start. Check web_server.log"
  tail -5 web_server.log 2>/dev/null
  exit 1
fi
