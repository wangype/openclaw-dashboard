#!/usr/bin/env python3
"""Security helper utilities for OpenClaw Dashboard backend.

Production detection and validation for Flask secret and asset drawer password.
"""

import os


def is_production_mode():
    """Return True if OPENCLAW_ENV or FLASK_ENV is prod/production."""
    env = (os.getenv("OPENCLAW_ENV") or os.getenv("FLASK_ENV") or "").strip().lower()
    return env in {"prod", "production"}


def is_strong_secret(secret):
    """Return True if secret is at least 24 chars and does not contain weak markers (e.g. change-me, dev)."""
    if not secret:
        return False
    secret = secret.strip()
    if len(secret) < 24:
        return False
    weak_markers = {"change-me", "dev", "example", "test", "default"}
    low = secret.lower()
    return not any(m in low for m in weak_markers)


def is_strong_drawer_pass(pwd):
    """Return True if password is not default 1234 and has at least 8 characters."""
    if not pwd:
        return False
    pwd = pwd.strip()
    if pwd == "1234":
        return False
    return len(pwd) >= 8
