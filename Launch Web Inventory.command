#!/bin/zsh
cd "/Users/maxmichels/Applications/Lager Tracker"
./.venv/bin/python3 -m pip install flask 'psycopg[binary]' -q 2>/dev/null || python3 -m pip install flask 'psycopg[binary]' -q 2>/dev/null
./run_web_server.sh
