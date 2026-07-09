#!/bin/zsh
# Show the root CA file to AirDrop to your iPhone (one-time trust setup).
cd "$(dirname "$0")"

CA="certs/iphone-trust-root-ca.pem"

if [[ ! -f "$CA" ]]; then
  echo "CA file not found. Running setup first..."
  ./setup_lan_https.sh
fi

echo ""
echo "=== Trust certificate on iPhone (one-time) ==="
echo ""
echo "File to send to iPhone:"
echo "  $(pwd)/$CA"
echo ""
echo "Steps on iPhone:"
echo "  1. AirDrop this file from your Mac to your iPhone"
echo "  2. Tap it → Allow → Install Profile → enter passcode"
echo "  3. Settings → General → VPN & Device Management → tap profile → Install"
echo "  4. Settings → General → About → Certificate Trust Settings"
echo "  5. Turn ON full trust for 'Momentum LAN Root CA'"
echo "  6. Close Safari completely, reopen https://192.168.178.48:8080"
echo ""
echo "Important: install the ROOT CA file above — NOT lan-cert.pem"
echo ""

open -R "$CA" 2>/dev/null || open "$(pwd)/certs" 2>/dev/null
