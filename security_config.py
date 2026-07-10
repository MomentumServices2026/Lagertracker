"""Auth and session configuration."""

import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_PASSCODE = os.environ.get("WEB_PASSCODE", "0170")
SESSION_SECRET_FILE = os.path.join(SCRIPT_DIR, ".web_session_secret")


def is_vercel():
    return os.environ.get("VERCEL") == "1"


def https_enabled():
    return is_vercel()


def get_session_secret():
    for key in ("SESSION_SECRET", "FLASK_SECRET_KEY"):
        secret = os.environ.get(key)
        if secret:
            return secret
    if is_vercel():
        import hashlib

        # Vercel filesystem is read-only — derive a stable secret from deployment env.
        material = ":".join(
            filter(
                None,
                [
                    os.environ.get("VERCEL_URL", ""),
                    os.environ.get("SUPABASE_DB_HOST", ""),
                    os.environ.get("SUPABASE_DB_PASSWORD", ""),
                ],
            )
        )
        if material:
            return hashlib.sha256(material.encode()).hexdigest()
        raise RuntimeError(
            "Add environment variables in Vercel Project Settings: "
            "SUPABASE_DB_HOST, SUPABASE_DB_PASSWORD, and optionally SESSION_SECRET."
        )
    if os.path.isfile(SESSION_SECRET_FILE):
        with open(SESSION_SECRET_FILE, encoding="utf-8") as f:
            return f.read().strip()
    secret = os.urandom(32).hex()
    with open(SESSION_SECRET_FILE, "w", encoding="utf-8") as f:
        f.write(secret)
    os.chmod(SESSION_SECRET_FILE, 0o600)
    return secret
