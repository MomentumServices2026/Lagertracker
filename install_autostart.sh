#!/bin/zsh
# Install the web server to start automatically when you log in to your Mac.

set -e
cd "$(dirname "$0")"

LABEL="com.momentum.inventory.web"
HEALTH_LABEL="com.momentum.inventory.web.health"
PLIST_NAME="$LABEL.plist"
HEALTH_PLIST="com.momentum.inventory.web.health.plist"
SOURCE="$(pwd)/$PLIST_NAME"
HEALTH_SOURCE="$(pwd)/$HEALTH_PLIST"
DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"
HEALTH_DEST="$HOME/Library/LaunchAgents/$HEALTH_PLIST"
DOMAIN="gui/$(id -u)"

mkdir -p "$HOME/Library/LaunchAgents"
cp "$SOURCE" "$DEST"
cp "$HEALTH_SOURCE" "$HEALTH_DEST"

chmod +x "$(pwd)/run_web_server.sh"
chmod +x "$(pwd)/check_web_server.sh"
chmod +x "$(pwd)/setup_lan_https.sh" 2>/dev/null || true
chmod +x "$(pwd)/enable_lan_security.sh" 2>/dev/null || true

launchctl bootout "$DOMAIN/$HEALTH_LABEL" 2>/dev/null || true
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$DEST"
launchctl bootstrap "$DOMAIN" "$HEALTH_DEST"
sleep 2
launchctl kickstart -k "$DOMAIN/$LABEL" 2>/dev/null || true

IP=$(python3 -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('8.8.8.8',80)); print(s.getsockname()[0]); s.close()" 2>/dev/null || echo "127.0.0.1")
HOST=$(scutil --get LocalHostName 2>/dev/null || echo "Mac")
SCHEME="http"
[[ -f certs/lan-cert.pem && -f certs/lan-key.pem ]] && SCHEME="https"
echo "Auto-start installed."
echo "The web server will now start when you log in and restart if it crashes."
echo "Phone URLs:"
echo "  ${SCHEME}://$IP:8080"
echo "  ${SCHEME}://${HOST}.local:8080"
echo "  ${SCHEME}://${HOST}.fritz.box:8080"
if [[ "$SCHEME" == "http" ]]; then
  echo ""
  echo "Enable HTTPS: ./enable_lan_security.sh"
fi
echo ""
echo "To remove auto-start later, run: ./uninstall_autostart.sh"
