#!/bin/zsh
# Generate a local HTTPS certificate for the LAN web app (encrypts phone ↔ Mac traffic).
# Supabase connection is unchanged — still uses its own SSL separately.
cd "$(dirname "$0")"

mkdir -p certs

IP=$(python3 -c "import socket; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('8.8.8.8',80)); print(s.getsockname()[0]); s.close()" 2>/dev/null || echo "127.0.0.1")
HOST=$(scutil --get LocalHostName 2>/dev/null || echo "localhost")
FRITZ="${HOST}.fritz.box"

echo "=== LAN HTTPS Setup ==="
echo "LAN IP: $IP"
echo ""

# Prefer mkcert (trusted on Mac/iPhone after installing mkcert CA)
if command -v mkcert &>/dev/null; then
  echo "Using mkcert (recommended)..."
  mkcert -install 2>/dev/null || true
  mkcert -cert-file certs/lan-cert.pem -key-file certs/lan-key.pem \
    "$IP" "127.0.0.1" "localhost" "${HOST}.local" "$FRITZ" 2>/dev/null || \
  mkcert -cert-file certs/lan-cert.pem -key-file certs/lan-key.pem \
    "$IP" "127.0.0.1" "localhost"
  CA_FILE="$(mkcert -CAROOT)/rootCA.pem"
  cp "$CA_FILE" certs/iphone-trust-root-ca.pem 2>/dev/null || true
else
  echo "Using OpenSSL with a local Certificate Authority..."
  echo "(iPhone must install certs/iphone-trust-root-ca.pem once — see ./trust_iphone_ca.sh)"
  echo ""

  SAN="IP:127.0.0.1,DNS:localhost"
  [[ "$IP" != "127.0.0.1" ]] && SAN="${SAN},IP:${IP}"
  [[ -n "$HOST" ]] && SAN="${SAN},DNS:${HOST}.local,DNS:${FRITZ}"

  # Local CA (install THIS file on iPhone — not lan-cert.pem)
  if [[ ! -f certs/lan-ca-key.pem ]]; then
    openssl genrsa -out certs/lan-ca-key.pem 4096 2>/dev/null
    openssl req -x509 -new -nodes -key certs/lan-ca-key.pem -sha256 -days 3650 \
      -out certs/lan-ca.pem \
      -subj "/CN=Momentum LAN Root CA/O=Momentum Services"
  fi

  # Server key + certificate signed by our CA
  openssl genrsa -out certs/lan-key.pem 4096 2>/dev/null
  openssl req -new -key certs/lan-key.pem -out certs/lan.csr \
    -subj "/CN=Momentum Inventory LAN/O=Momentum Services" 2>/dev/null

  openssl x509 -req -in certs/lan.csr -CA certs/lan-ca.pem -CAkey certs/lan-ca-key.pem \
    -CAcreateserial -out certs/lan-cert.pem -days 825 -sha256 \
    -extfile <(printf "subjectAltName=%s" "$SAN") 2>/dev/null

  cp certs/lan-ca.pem certs/iphone-trust-root-ca.pem
  rm -f certs/lan.csr
fi

chmod 600 certs/lan-key.pem certs/lan-ca-key.pem 2>/dev/null
chmod 644 certs/lan-cert.pem certs/iphone-trust-root-ca.pem 2>/dev/null

# Trust CA on this Mac (stops Safari warning on Mac too)
if [[ -f certs/iphone-trust-root-ca.pem ]]; then
  security add-trusted-cert -d -r trustRoot -k ~/Library/Keychains/login.keychain-db \
    certs/iphone-trust-root-ca.pem 2>/dev/null || \
  security add-trusted-cert -d -r trustRoot -k ~/Library/Keychains/login.keychain \
    certs/iphone-trust-root-ca.pem 2>/dev/null || true
fi

# Restrict config file permissions (contains Supabase password)
[[ -f app_db_config.json ]] && chmod 600 app_db_config.json

echo ""
echo "Certificates saved to: certs/"
echo ""
echo "Restart the web server:"
echo "  ./apply_web_update.sh"
echo ""
echo "Then use HTTPS URLs on your phone:"
echo "  https://${IP}:8080"
[[ -n "$HOST" ]] && echo "  https://${HOST}.local:8080"
[[ -n "$FRITZ" ]] && echo "  https://${FRITZ}:8080"
echo ""
echo "=== iPhone: stop the 'not private' warning (one-time) ==="
echo "  1. AirDrop certs/iphone-trust-root-ca.pem to your iPhone"
echo "  2. Tap the file → Install Profile → enter passcode"
echo "  3. Settings → General → VPN & Device Management → Install"
echo "  4. Settings → General → About → Certificate Trust Settings"
echo "  5. Enable FULL TRUST for 'Momentum LAN Root CA'"
echo "  6. Re-open Safari — warning should be gone"
echo ""
echo "Run ./trust_iphone_ca.sh to open the CA file in Finder."
echo "Update your iPhone home-screen bookmark to the HTTPS URL above."
