#!/bin/zsh
# Start web server (auto-enables HTTPS when certs/lan-cert.pem exists).
cd "$(dirname "$0")"

LOG="web_server.log"

# Wait for network after boot/login (Supabase needs outbound HTTPS).
for _ in {1..30}; do
  if ping -c 1 -W 1000 8.8.8.8 >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

PYTHON=""
if [[ -x "./.venv/bin/python3" ]]; then
  PYTHON="./.venv/bin/python3"
else
  for candidate in \
    "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3" \
    "/usr/local/bin/python3" \
    "/opt/homebrew/bin/python3" \
    "$(which python3 2>/dev/null)"
  do
    if [[ -x "$candidate" ]]; then
      PYTHON="$candidate"
      break
    fi
  done
fi

if [[ -z "$PYTHON" ]]; then
  echo "No Python found." >&2
  exit 1
fi

"$PYTHON" -m pip install flask 'psycopg[binary]' reportlab -q 2>/dev/null

/usr/bin/caffeinate -s -i -- /bin/zsh -c "
  cd \"$(pwd)\"
  PYTHON=\"$PYTHON\"
  LOG=\"$LOG\"

  wait_for_imports() {
    local n=0
    while (( n < 60 )); do
      if \"\$PYTHON\" -c \"import flask; import web_logic; import web_app\" 2>/dev/null; then
        return 0
      fi
      n=\$(( n + 1 ))
      sleep 2
    done
    return 1
  }

  while true; do
    if wait_for_imports; then
      \"\$PYTHON\" web_app.py
    else
      echo \"\$(date '+%Y-%m-%d %H:%M:%S') import retry timeout — trying web_app anyway\" >> \"\$LOG\"
      \"\$PYTHON\" web_app.py
    fi
    code=\$?
    echo \"\$(date '+%Y-%m-%d %H:%M:%S') web_app.py exited (\$code) — restarting in 3s\" >> \"\$LOG\"
    sleep 3
  done
"
