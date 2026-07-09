#!/bin/zsh
cd "$(dirname "$0")"

PID_FILE="web_server.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "No background web server found."
  exit 0
fi

PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "Stopped web server (PID $PID)"
else
  echo "Web server was not running."
fi

rm -f "$PID_FILE"
