#!/usr/bin/env python3
"""Security helper utilities for OpenClaw Dashboard backend.

Production detection and validation for Flask secret and asset drawer password.
Password authentication with SHA-256 + salt.
"""

import os
import hashlib
import time
import json

# Password storage file
PASSWORD_FILE = os.path.join(os.path.dirname(__file__), 'auth_config.json')
# Login failure tracking (in-memory)
LOGIN_FAILURES = {}
# Lockout threshold and duration
MAX_FAILURES = 5
LOCKOUT_DURATION = 300  # 5 minutes


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


def hash_password(password, salt=None):
    """Hash password with SHA-256 and salt.

    Returns tuple of (hashed_password, salt).
    """
    if salt is None:
        salt = os.urandom(32).hex()
    hashed = hashlib.sha256((password + salt).encode('utf-8')).hexdigest()
    return hashed, salt


def verify_password(password, stored_hash, salt):
    """Verify password against stored hash."""
    hashed, _ = hash_password(password, salt)
    return hashed == stored_hash


def load_auth_config():
    """Load authentication config from file."""
    if not os.path.exists(PASSWORD_FILE):
        return None
    try:
        with open(PASSWORD_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def save_auth_config(config):
    """Save authentication config to file."""
    with open(PASSWORD_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)


def is_password_set():
    """Check if password has been configured."""
    config = load_auth_config()
    return config is not None and 'password_hash' in config


def set_password(password):
    """Set the authentication password."""
    hashed, salt = hash_password(password)
    config = {
        'password_hash': hashed,
        'salt': salt,
        'created_at': time.time(),
    }
    save_auth_config(config)
    return True


def check_login(password, client_ip=None):
    """Check login password.

    Returns tuple of (success, error_message).
    Implements lockout after MAX_FAILURES failures.
    """
    config = load_auth_config()
    if not config:
        return False, "Password not configured"

    if client_ip is None:
        client_ip = "unknown"

    # Check lockout
    client_ip = "local"  # Could use request.remote_addr in production
    if client_ip in LOGIN_FAILURES:
        failures, locked_until = LOGIN_FAILURES[client_ip]
        if time.time() < locked_until:
            remaining = int(locked_until - time.time())
            return False, f"Account locked. Try again in {remaining} seconds"
        else:
            # Lockout expired, reset
            del LOGIN_FAILURES[client_ip]

    # Verify password
    if verify_password(password, config['password_hash'], config['salt']):
        # Success - clear any failures
        if client_ip in LOGIN_FAILURES:
            del LOGIN_FAILURES[client_ip]
        return True, None
    else:
        # Failure - track
        if client_ip not in LOGIN_FAILURES:
            LOGIN_FAILURES[client_ip] = (0, 0)
        failures, _ = LOGIN_FAILURES[client_ip]
        failures += 1
        if failures >= MAX_FAILURES:
            locked_until = time.time() + LOCKOUT_DURATION
            LOGIN_FAILURES[client_ip] = (failures, locked_until)
            return False, f"Too many failed attempts. Locked for {LOCKOUT_DURATION // 60} minutes"
        else:
            LOGIN_FAILURES[client_ip] = (failures, time.time() + 60)  # Track for 1 minute
            remaining = MAX_FAILURES - failures
            return False, f"Invalid password. {remaining} attempts remaining"


def clear_login_failures(client_ip=None):
    """Clear login failures for a client (e.g., after successful logout)."""
    if client_ip is None:
        LOGIN_FAILURES.clear()
    elif client_ip in LOGIN_FAILURES:
        del LOGIN_FAILURES[client_ip]
