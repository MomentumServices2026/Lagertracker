"""Vercel serverless entry point — exposes the Flask WSGI app."""

import os
import sys

# Project root (parent of api/)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from web_app import app  # noqa: E402, F401
