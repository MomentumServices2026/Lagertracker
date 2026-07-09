#!/bin/zsh
# Health check — restarts the web server if it is not responding.
cd "$(dirname "$0")"

LABEL="com.momentum.inventory.web"
DOMAIN="gui/$(id -u)"
PORT=8080
LOG="web_server.log"

if curl -sk --max-time 8 "https://127.0.0.1:${PORT}/api/auth/status" | grep -q '"authenticated"'; then
  exit 0
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') health check failed — restarting server" >> "$LOG"
launchctl kickstart -k "$DOMAIN/$LABEL" 2>/dev/null || ./start_web_background.sh
