"""LAN HTTPS certificate helpers and auth configuration."""

import os
import ssl

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CERT_DIR = os.path.join(SCRIPT_DIR, "certs")
CERT_FILE = os.path.join(CERT_DIR, "lan-cert.pem")
KEY_FILE = os.path.join(CERT_DIR, "lan-key.pem")
WEB_PASSCODE = os.environ.get("WEB_PASSCODE", "0170")
SESSION_SECRET_FILE = os.path.join(SCRIPT_DIR, ".web_session_secret")


def is_vercel():
    return os.environ.get("VERCEL") == "1"


def https_enabled():
    if is_vercel():
        return True
    return os.path.isfile(CERT_FILE) and os.path.isfile(KEY_FILE)


def url_scheme():
    return "https" if https_enabled() else "http"


def build_ssl_context():
    if not https_enabled() or is_vercel():
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(CERT_FILE, KEY_FILE)
    return ctx


def get_session_secret():
    for key in ("SESSION_SECRET", "FLASK_SECRET_KEY"):
        secret = os.environ.get(key)
        if secret:
            return secret
    if os.path.isfile(SESSION_SECRET_FILE):
        with open(SESSION_SECRET_FILE, encoding="utf-8") as f:
            return f.read().strip()
    secret = os.urandom(32).hex()
    with open(SESSION_SECRET_FILE, "w", encoding="utf-8") as f:
        f.write(secret)
    os.chmod(SESSION_SECRET_FILE, 0o600)
    return secret
