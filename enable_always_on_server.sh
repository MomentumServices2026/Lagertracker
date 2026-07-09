#!/bin/zsh
# Keep Mac mini awake so the inventory web app stays reachable on your phone.
cd "$(dirname "$0")"

echo "=== Always-On Inventory Server ==="
echo ""
echo "Note: A fully sleeping Mac cannot run apps or accept network connections."
echo "This setup prevents sleep while the server is running."
echo ""

# Reinstall launch agent (uses caffeinate to block idle/system sleep)
./install_autostart.sh

echo ""
echo "Configuring power settings (plugged in / Mac mini)..."

# Mac mini is always on AC power — prevent system sleep while plugged in.
# May ask for your password.
if sudo -n true 2>/dev/null; then
  HAS_SUDO=1
else
  echo "Enter your Mac password to allow sleep settings (recommended):"
fi

if sudo pmset -c sleep 0 2>/dev/null; then
  echo "  System sleep (on power): OFF"
else
  echo "  Could not change pmset (skipped). Caffeinate still helps."
fi

if sudo pmset -c disksleep 0 2>/dev/null; then
  echo "  Disk sleep (on power): OFF"
fi

if sudo pmset -c tcpkeepalive 1 2>/dev/null; then
  echo "  Network keep-alive: ON"
fi

# Display can still turn off to save energy — server keeps running.
sudo pmset -c displaysleep 15 2>/dev/null && echo "  Display sleep: 15 min (screen off is OK)"

echo ""
echo "Done. Your Mac will stay reachable while the inventory server runs."
echo "The display may turn off — that is fine."
echo ""
echo "Also check: System Settings → Energy → prevent sleep when possible."
echo "If the Mac is manually put to sleep (Apple menu → Sleep), the app will stop until it wakes."
