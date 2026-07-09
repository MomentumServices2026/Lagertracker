#!/bin/zsh
# Enable HTTPS + restart web server. Supabase is not modified.
cd "$(dirname "$0")"
./setup_lan_https.sh
./apply_web_update.sh
