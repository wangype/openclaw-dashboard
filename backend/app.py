#!/usr/bin/env python3
"""OpenClaw Dashboard - Backend API Server

A Flask-based backend that exposes OpenClaw agent status, gateway health,
and memo data through REST APIs for the pixel-art dashboard frontend.

Port: 19001
Data sources: openclaw CLI (subprocess), local files (memory/*.md)
"""

from flask import Flask, jsonify, send_from_directory, make_response, request
from datetime import datetime
import json
import os
import re

from security_utils import is_production_mode, is_strong_secret, is_strong_drawer_pass
from memo_utils import get_yesterday_date_str, sanitize_content, extract_memo_from_file
from openclaw_client import (
    get_agents_list,
    get_gateway_health,
    get_channel_health,
    get_agent_detail,
    get_system_status
)

# === Paths ===
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")
FRONTEND_INDEX_FILE = os.path.join(FRONTEND_DIR, "index.html")
MEMORY_DIR = os.path.join(os.path.dirname(ROOT_DIR), "memory")
OPENCLAW_WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE") or os.path.join(os.path.expanduser("~"), ".openclaw", "workspace")
IDENTITY_FILE = os.path.join(OPENCLAW_WORKSPACE, "IDENTITY.md")

# === State mapping (same as Star-Office-UI) ===
VALID_AGENT_STATES = frozenset({"idle", "writing", "researching", "executing", "syncing", "error"})
STATE_TO_AREA_MAP = {
    "idle": "breakroom",
    "writing": "writing",
    "researching": "writing",
    "executing": "writing",
    "syncing": "writing",
    "error": "error",
}

# === Flask app ===
app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="/static")
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.getenv("OPENCLAW_SECRET") or "openclaw-dashboard-dev-secret"

# Session config
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=is_production_mode(),
)


def get_office_name_from_identity():
    """Read office display name from OpenClaw workspace IDENTITY.md (Name field)."""
    if not os.path.isfile(IDENTITY_FILE):
        return None
    try:
        with open(IDENTITY_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        m = re.search(r"-\s*\*\*Name:\*\*\s*(.+)", content)
        if m:
            name = m.group(1).strip().replace("\r", "").split("\n")[0].strip()
            return f"{name}的办公室" if name else None
    except Exception:
        pass
    return None


def normalize_agent_state(state):
    """Normalize agent state to canonical form."""
    s = (state or "").strip().lower()
    if s in VALID_AGENT_STATES:
        return s
    # Map common aliases
    alias_map = {
        "work": "writing",
        "working": "writing",
        "code": "writing",
        "coding": "writing",
        "run": "executing",
        "running": "executing",
        "search": "researching",
        "research": "researching",
        "wait": "idle",
        "waiting": "idle",
        "free": "idle",
        "fail": "error",
        "failed": "error",
    }
    return alias_map.get(s, "idle")


# === Cache for CLI results ===
_cache = {
    "agents": {"data": None, "ts": 0, "ttl": 3},
    "gateway": {"data": None, "ts": 0, "ttl": 5},
    "channels": {"data": None, "ts": 0, "ttl": 5},
}


def _get_cached(key, fetch_fn, ttl):
    """Get cached data or fetch if expired."""
    import time
    now = time.time()
    entry = _cache.get(key)
    if entry and entry["data"] is not None and (now - entry["ts"]) < ttl:
        return entry["data"]
    data = fetch_fn()
    _cache[key] = {"data": data, "ts": now, "ttl": ttl}
    return data


# === Routes ===

@app.route("/", methods=["GET"])
def index():
    """Serve the main dashboard UI."""
    with open(FRONTEND_INDEX_FILE, "r", encoding="utf-8") as f:
        html = f.read()
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "service": "openclaw-dashboard",
        "timestamp": datetime.now().isoformat(),
    })


@app.route("/api/agents", methods=["GET"])
def api_agents():
    """Get list of all agents with their status.

    Maps OpenClaw agent data to dashboard format:
    - id, name, model, workspace
    - state (normalized), area (mapped), detail, updated_at
    """
    try:
        agents = _get_cached("agents", get_agents_list, _cache["agents"]["ttl"])

        # Transform to dashboard format
        result = []
        for agent in agents:
            raw_state = agent.get("state", "idle")
            normalized_state = normalize_agent_state(raw_state)
            area = STATE_TO_AREA_MAP.get(normalized_state, "breakroom")

            result.append({
                "id": agent.get("id", "unknown"),
                "name": agent.get("name", agent.get("id", "Unknown")),
                "model": agent.get("model"),
                "workspace": agent.get("workspace"),
                "state": normalized_state,
                "area": area,
                "detail": agent.get("detail", ""),
                "updated_at": agent.get("updated_at"),
            })

        return jsonify({
            "ok": True,
            "agents": result,
            "count": len(result),
            "timestamp": datetime.now().isoformat(),
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "agents": [],
        }), 500


@app.route("/api/agents/<agent_id>", methods=["GET"])
def api_agent_detail(agent_id):
    """Get detailed info for a specific agent."""
    try:
        detail = get_agent_detail(agent_id)
        if detail:
            return jsonify({
                "ok": True,
                "agent": detail,
            })
        return jsonify({
            "ok": False,
            "error": f"Agent {agent_id} not found",
        }), 404
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/api/gateway-health", methods=["GET"])
def api_gateway_health():
    """Get gateway health status."""
    try:
        health = _get_cached("gateway", get_gateway_health, _cache["gateway"]["ttl"])
        return jsonify({
            "ok": True,
            "health": health,
            "timestamp": datetime.now().isoformat(),
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/api/channels", methods=["GET"])
def api_channels():
    """Get channel health status."""
    try:
        channels = _get_cached("channels", get_channel_health, _cache["channels"]["ttl"])
        return jsonify({
            "ok": True,
            "channels": channels,
            "timestamp": datetime.now().isoformat(),
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/api/system-status", methods=["GET"])
def api_system_status():
    """Get overall system status."""
    try:
        status = get_system_status()
        return jsonify({
            "ok": True,
            "status": status,
            "timestamp": datetime.now().isoformat(),
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/api/memo", methods=["GET"])
def api_memo():
    """Get yesterday's memo (昨日小记).

    Reads from memory/*.md, sanitizes PII, and returns formatted content.
    """
    try:
        yesterday_str = get_yesterday_date_str()
        yesterday_file = os.path.join(MEMORY_DIR, f"{yesterday_str}.md")

        target_file = None
        target_date = yesterday_str

        if os.path.exists(yesterday_file):
            target_file = yesterday_file
        else:
            # Find most recent memo (not today)
            if os.path.exists(MEMORY_DIR):
                files = [f for f in os.listdir(MEMORY_DIR)
                        if f.endswith(".md") and re.match(r"\d{4}-\d{2}-\d{2}\.md", f)]
                if files:
                    files.sort(reverse=True)
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    for f in files:
                        if f != f"{today_str}.md":
                            target_file = os.path.join(MEMORY_DIR, f)
                            target_date = f.replace(".md", "")
                            break

        if target_file and os.path.exists(target_file):
            memo_content = extract_memo_from_file(target_file)
            return jsonify({
                "success": True,
                "date": target_date,
                "memo": memo_content,
            })
        else:
            return jsonify({
                "success": True,
                "date": yesterday_str,
                "memo": "「昨日无事记录」\n\n若有恒，何必三更眠五更起；最无益，莫过一日曝十日寒。",
            })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
        }), 500


@app.route("/api/office-info", methods=["GET"])
def api_office_info():
    """Get office display info (name from IDENTITY.md)."""
    try:
        office_name = get_office_name_from_identity()
        return jsonify({
            "ok": True,
            "office_name": office_name,
            "workspace": OPENCLAW_WORKSPACE,
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
        }), 500


@app.route("/static/<path:filename>")
def serve_static(filename):
    """Serve static files from frontend directory."""
    return send_from_directory(app.static_folder, filename)


# === Main ===

if __name__ == "__main__":
    port = int(os.getenv("OPENCLAW_DASHBOARD_PORT") or "19001")
    debug = os.getenv("FLASK_DEBUG", "0").lower() in {"1", "true", "yes"}

    print(f"[OpenClaw Dashboard] Starting server on port {port}...")
    print(f"[OpenClaw Dashboard] Frontend: {FRONTEND_DIR}")
    print(f"[OpenClaw Dashboard] Memory: {MEMORY_DIR}")
    print(f"[OpenClaw Dashboard] Workspace: {OPENCLAW_WORKSPACE}")

    app.run(host="0.0.0.0", port=port, debug=debug)
