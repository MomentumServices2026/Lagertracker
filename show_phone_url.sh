#!/bin/zsh
cd "$(dirname "$0")"

PORT=8080
IP=$(python3 -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('8.8.8.8',80)); print(s.getsockname()[0]); s.close()" 2>/dev/null)
HOST=$(scutil --get LocalHostName 2>/dev/null)
FRITZ="${HOST}.fritz.box"

SCHEME="http"
[[ -f certs/lan-cert.pem && -f certs/lan-key.pem ]] && SCHEME="https"

echo "=== Phone connection URLs ==="
echo ""
if lsof -i :$PORT -sTCP:LISTEN &>/dev/null; then
  echo "Server status: RUNNING on port $PORT ($SCHEME)"
else
  echo "Server status: NOT RUNNING"
  echo "Start with: ./start_web_background.sh"
  echo ""
fi

[[ -n "$IP" ]] && echo "  ${SCHEME}://$IP:$PORT"
[[ -n "$HOST" ]] && echo "  ${SCHEME}://${HOST}.local:$PORT"
[[ -n "$FRITZ" ]] && echo "  ${SCHEME}://${FRITZ}:$PORT"
echo ""
if [[ "$SCHEME" == "https" ]]; then
  echo "HTTPS is enabled — LAN traffic is encrypted."
else
  echo "Enable HTTPS: ./enable_lan_security.sh"
fi
echo "iPhone must be on the MAIN Wi-Fi (not guest / Gast-WLAN)."
