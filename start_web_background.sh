#!/bin/zsh
cd "$(dirname "$0")"

PID_FILE="web_server.pid"
LOG_FILE="web_server.log"
SCHEME="http"
[[ -f certs/lan-cert.pem && -f certs/lan-key.pem ]] && SCHEME="https"

if [[ -f "$PID_FILE" ]]; then
  OLD_PID=$(cat "$PID_FILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "Web server already running (PID $OLD_PID)"
    ipconfig getifaddr en0 2>/dev/null | read IP || IP="127.0.0.1"
    echo "Open on phone: ${SCHEME}://${IP:-127.0.0.1}:8080"
    exit 0
  fi
fi

chmod +x run_web_server.sh 2>/dev/null

PYTHON="./.venv/bin/python3"
[[ -x "$PYTHON" ]] || PYTHON="$(which python3)"

"$PYTHON" -m pip install flask 'psycopg[binary]' reportlab -q 2>/dev/null

nohup ./run_web_server.sh >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

sleep 1
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "127.0.0.1")

echo "Web server started in background (PID $(cat "$PID_FILE"))"
echo "  Phone URL: ${SCHEME}://$IP:8080"
echo "  Log file:  $LOG_FILE"
echo "  Stop with: ./stop_web_background.sh"
if [[ "$SCHEME" == "http" ]]; then
  echo "  Enable HTTPS: ./enable_lan_security.sh"
fi
