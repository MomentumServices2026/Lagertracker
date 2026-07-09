#!/bin/zsh
cd "$(dirname "$0")"

LABEL="com.momentum.inventory.web"
HEALTH_LABEL="com.momentum.inventory.web.health"
DOMAIN="gui/$(id -u)"

launchctl bootout "$DOMAIN/$HEALTH_LABEL" 2>/dev/null || true
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -f "$HOME/Library/LaunchAgents/$HEALTH_LABEL.plist"

echo "Auto-start removed."
