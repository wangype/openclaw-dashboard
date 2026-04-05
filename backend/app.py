#!/usr/bin/env python3
"""OpenClaw Dashboard - Backend API Server

A Flask-based backend that exposes OpenClaw agent status, gateway health,
and memo data through REST APIs for the pixel-art dashboard frontend.

Port: 19001
Data sources: openclaw CLI (subprocess), local files (memory/*.md)
"""

from flask import Flask, jsonify, send_from_directory, make_response, request, session
from datetime import datetime
import json
import os
import re

from security_utils import (
    is_production_mode, is_strong_secret, is_strong_drawer_pass,
    is_password_set, set_password, check_login, clear_login_failures,
    hash_password, load_auth_config
)
from memo_utils import get_yesterday_date_str, sanitize_content, extract_memo_from_file
from openclaw_client import (
    get_agents_list,
    get_gateway_health,
    get_channel_health,
    get_agent_detail,
    get_system_status,
    get_sessions_list,
    get_agent_identity_map,
    parse_session_key,
    parse_progress_from_detail
)
from gemini_client import (
    generate_background,
    get_task_status,
    list_backgrounds,
    set_current_background,
    load_runtime_config
)
from assets_client import (
    save_uploaded_file,
    list_assets,
    delete_asset,
    get_asset,
    ALLOWED_EXTENSIONS
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


def require_auth(f):
    """Decorator to require authentication for routes."""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if password is set - if not, allow access (for initial setup)
        if not is_password_set():
            return f(*args, **kwargs)
        # Check session auth
        if not session.get('authenticated'):
            return jsonify({'ok': False, 'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function


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
    "sessions": {"data": None, "ts": 0, "ttl": 30},
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
    - emoji, progress, activeSessions (Phase 2 enhancements)
    """
    try:
        agents = _get_cached("agents", get_agents_list, _cache["agents"]["ttl"])

        # Get session counts per agent for activeSessions
        sessions, _ = get_sessions_list(limit=200)
        session_counts = {}
        for s in sessions:
            aid = s.get('agentId')
            if aid:
                session_counts[aid] = session_counts.get(aid, 0) + 1

        # Get identity map for emoji
        identity_map = get_agent_identity_map()

        # Transform to dashboard format
        result = []
        for agent in agents:
            raw_state = agent.get("state", "idle")
            normalized_state = normalize_agent_state(raw_state)
            area = STATE_TO_AREA_MAP.get(normalized_state, "breakroom")

            agent_id = agent.get("id", "unknown")
            identity = identity_map.get(agent_id, {'name': agent.get("id", "Unknown"), 'emoji': '🤖'})

            detail = agent.get("detail", "")
            progress = parse_progress_from_detail(detail)

            result.append({
                "id": agent_id,
                "name": identity['name'],
                "emoji": identity['emoji'],
                "model": agent.get("model"),
                "workspace": agent.get("workspace"),
                "state": normalized_state,
                "area": area,
                "detail": detail,
                "progress": progress,
                "activeSessions": session_counts.get(agent_id, 0),
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


@app.route("/uploads/<path:filename>")
def serve_uploads(filename):
    """Serve uploaded files from backend/uploads directory."""
    uploads_dir = os.path.join(ROOT_DIR, 'backend', 'uploads')
    return send_from_directory(uploads_dir, filename)


# === Phase 2 API Routes ===


# === Phase 3: Authentication Routes ===

@app.route("/api/auth/setup", methods=["POST"])
def api_auth_setup():
    """Initial password setup.

    Only available when no password is set yet.
    Request: { "password": "..." }
    Response: { "ok": true }
    """
    if is_password_set():
        return jsonify({'ok': False, 'error': 'Password already configured'}), 400

    data = request.get_json() or {}
    password = data.get('password', '')

    if not password or len(password) < 6:
        return jsonify({'ok': False, 'error': 'Password must be at least 6 characters'}), 400

    set_password(password)

    # Auto-login after setup
    session['authenticated'] = True

    return jsonify({'ok': True})


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    """Login with password.

    Request: { "password": "..." }
    Response: { "ok": true } or { "ok": false, "error": "..." }
    """
    from flask import request
    data = request.get_json() or {}
    password = data.get('password', '')

    client_ip = request.remote_addr or 'unknown'
    success, error = check_login(password, client_ip)

    if success:
        session['authenticated'] = True
        return jsonify({'ok': True})
    else:
        return jsonify({'ok': False, 'error': error}), 401


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    """Logout and clear session.

    Response: { "ok": true }
    """
    session.pop('authenticated', None)
    clear_login_failures()
    return jsonify({'ok': True})


@app.route("/api/auth/status", methods=["GET"])
def api_auth_status():
    """Get authentication status.

    Response: { "ok": true, "authenticated": bool, "passwordSet": bool }
    """
    return jsonify({
        'ok': True,
        'authenticated': session.get('authenticated', False),
        'passwordSet': is_password_set()
    })


# === Phase 3: AI Image Generation Routes ===

@app.route("/api/assets/generate", methods=["POST"])
@require_auth
def api_assets_generate():
    """Submit a prompt to generate a pixel-art background.

    Request: { "prompt": "...", "style": "pixel-art" }
    Response: { "ok": true, "taskId": "bg_xxx", "status": "generating" }
    """
    data = request.get_json() or {}
    prompt = data.get('prompt', '')

    if not prompt or len(prompt) > 200:
        return jsonify({'ok': False, 'error': 'Prompt is required (max 200 chars)'}), 400

    style = data.get('style', 'pixel-art')

    task_id = generate_background(prompt, style)
    task = get_task_status(task_id)

    if task and task.get('status') == 'failed':
        return jsonify({
            'ok': False,
            'taskId': task_id,
            'status': 'failed',
            'error': task.get('error', 'Unknown error')
        }), 500

    return jsonify({'ok': True, 'taskId': task_id, 'status': 'generating'})


@app.route("/api/assets/generate/<task_id>", methods=["GET"])
@require_auth
def api_assets_generate_status(task_id):
    """Get generation task status.

    Response (generating): { "ok": true, "status": "generating", "progress": 40 }
    Response (done): { "ok": true, "status": "done", "imageUrl": "/uploads/..." }
    Response (failed): { "ok": false, "status": "failed", "error": "..." }
    """
    task = get_task_status(task_id)

    if not task:
        return jsonify({'ok': False, 'error': 'Task not found'}), 404

    if task['status'] == 'done':
        return jsonify({
            'ok': True,
            'status': 'done',
            'imageUrl': task.get('url'),
            'progress': 100,
        })
    elif task['status'] == 'failed':
        return jsonify({
            'ok': False,
            'status': 'failed',
            'error': task.get('error', 'Unknown error'),
        })
    else:
        return jsonify({
            'ok': True,
            'status': 'generating',
            'progress': task.get('progress', 0),
        })


@app.route("/api/assets/backgrounds", methods=["GET"])
@require_auth
def api_assets_backgrounds():
    """Get list of all generated backgrounds.

    Response: { "ok": true, "backgrounds": [...], "current": "bg_xxx" }
    """
    backgrounds, current = list_backgrounds()
    return jsonify({
        'ok': True,
        'backgrounds': backgrounds,
        'current': current,
    })


@app.route("/api/assets/backgrounds/<bg_id>/activate", methods=["POST"])
@require_auth
def api_assets_backgrounds_activate(bg_id):
    """Set a background as the current active one.

    Response: { "ok": true }
    """
    # Validate background exists
    backgrounds, _ = list_backgrounds()
    bg_ids = [bg['id'] for bg in backgrounds]

    if bg_id not in bg_ids:
        return jsonify({'ok': False, 'error': 'Background not found'}), 404

    set_current_background(bg_id)

    return jsonify({'ok': True})


# === Phase 3: Asset Management Routes ===

@app.route("/api/assets/upload", methods=["POST"])
@require_auth
def api_assets_upload():
    """Upload a new asset (character, decoration, etc.).

    Form data:
      - file: The file to upload
      - type: Asset type (character, decoration, background, etc.)

    Response: { "ok": true, "asset": {...} }
    """
    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'No file provided'}), 400

    file = request.files['file']
    asset_type = request.form.get('type', 'general')

    success, result = save_uploaded_file(file, asset_type)

    if success:
        return jsonify({'ok': True, 'asset': result})
    else:
        return jsonify({'ok': False, 'error': result}), 400


@app.route("/api/assets/list", methods=["GET"])
@require_auth
def api_assets_list():
    """List all uploaded assets.

    Query params:
      - type: Optional filter by asset type

    Response: { "ok": true, "assets": [...] }
    """
    asset_type = request.args.get('type')
    assets = list_assets(asset_type)
    return jsonify({'ok': True, 'assets': assets})


@app.route("/api/assets/<asset_id>", methods=["DELETE"])
@require_auth
def api_assets_delete(asset_id):
    """Delete an asset by ID.

    Response: { "ok": true }
    """
    success, message = delete_asset(asset_id)

    if success:
        return jsonify({'ok': True})
    else:
        return jsonify({'ok': False, 'error': message}), 404


# === Phase 2 API Routes ===

@app.route("/api/sessions", methods=["GET"])
def api_sessions():
    """Get list of recent sessions."""
    try:
        limit = int(request.args.get('limit', 20))
        agent_filter = request.args.get('agent')
        active_minutes = request.args.get('activeMinutes')
        if active_minutes:
            active_minutes = int(active_minutes)

        sessions, total = get_sessions_list(limit, agent_filter, active_minutes)

        # Data enrichment with agent identity info
        identity_map = get_agent_identity_map()
        result = []
        for s in sessions:
            agent_id = s.get('agentId', 'unknown')
            identity = identity_map.get(agent_id, {'name': agent_id, 'emoji': '🤖'})
            key_info = parse_session_key(s.get('key', ''))

            updated_at = s.get('updatedAt', 0)
            updated_at_iso = None
            if updated_at:
                try:
                    updated_at_iso = datetime.fromtimestamp(updated_at / 1000).isoformat()
                except (ValueError, OSError):
                    pass

            # Generate summary
            kind = key_info.get('kind', '')
            channel = key_info.get('channel', '')
            if kind == 'direct':
                summary = f"{channel} 对话"
            elif kind == 'subagent':
                summary = f"{agent_id} subagent 任务"
            else:
                summary = f"{channel} {kind}"

            result.append({
                'key': s.get('key', ''),
                'agentId': agent_id,
                'agentName': identity['name'],
                'agentEmoji': identity['emoji'],
                'channel': channel,
                'kind': kind,
                'model': s.get('model', ''),
                'updatedAt': updated_at,
                'updatedAtISO': updated_at_iso,
                'ageMs': s.get('ageMs', 0),
                'summary': summary,
            })

        return jsonify({
            'ok': True,
            'sessions': result,
            'total': total,
            'returned': len(result),
            'timestamp': datetime.now().isoformat(),
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route("/api/sessions/timeline", methods=["GET"])
def api_sessions_timeline():
    """Get timeline data for message flow display."""
    try:
        limit = int(request.args.get('limit', 10))
        hours = int(request.args.get('hours', 24))

        sessions, _ = get_sessions_list(limit, active_minutes=hours * 60 if hours else None)
        identity_map = get_agent_identity_map()
        now_ms = int(datetime.now().timestamp() * 1000)
        five_min_ms = 5 * 60 * 1000

        result = []
        for s in sessions:
            agent_id = s.get('agentId', 'unknown')
            identity = identity_map.get(agent_id, {'name': agent_id, 'emoji': '🤖'})
            key_info = parse_session_key(s.get('key', ''))
            updated_at = s.get('updatedAt', 0)

            time_str = '--:--'
            time_iso = None
            if updated_at:
                try:
                    ts = datetime.fromtimestamp(updated_at / 1000)
                    time_str = ts.strftime("%H:%M")
                    time_iso = ts.isoformat()
                except (ValueError, OSError):
                    pass

            kind = key_info.get('kind', '')
            channel = key_info.get('channel', '')
            if kind == 'direct':
                summary = f"{channel} 对话"
            elif kind == 'subagent':
                summary = f"{agent_id} subagent 任务"
            else:
                summary = f"{channel} {kind}"

            result.append({
                'time': time_str,
                'timeISO': time_iso,
                'agentId': agent_id,
                'agentName': identity['name'],
                'agentEmoji': identity['emoji'],
                'channel': channel,
                'kind': kind,
                'summary': summary,
                'isNew': (now_ms - updated_at) < five_min_ms if updated_at else False,
            })

        return jsonify({
            'ok': True,
            'timeline': result,
            'timestamp': datetime.now().isoformat(),
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route("/api/multi-agent-status", methods=["GET"])
def api_multi_agent_status():
    """Get multi-agent collaboration status with positions."""
    try:
        agents = _get_cached("agents", get_agents_list, _cache["agents"]["ttl"])
        identity_map = get_agent_identity_map()

        # Position mapping for areas
        AREA_POSITIONS = {
            'breakroom': [
                {'x': 620, 'y': 180}, {'x': 560, 'y': 220},
                {'x': 680, 'y': 210}, {'x': 540, 'y': 170},
                {'x': 700, 'y': 240}, {'x': 600, 'y': 250},
                {'x': 650, 'y': 160}, {'x': 580, 'y': 200}
            ],
            'writing': [
                {'x': 760, 'y': 320}, {'x': 830, 'y': 280},
                {'x': 690, 'y': 350}, {'x': 770, 'y': 260},
                {'x': 850, 'y': 340}, {'x': 720, 'y': 300},
                {'x': 800, 'y': 370}, {'x': 750, 'y': 240}
            ],
            'error': [
                {'x': 180, 'y': 260}, {'x': 120, 'y': 220},
                {'x': 240, 'y': 230}, {'x': 160, 'y': 200},
                {'x': 220, 'y': 270}, {'x': 140, 'y': 250},
                {'x': 200, 'y': 210}, {'x': 260, 'y': 260}
            ],
        }

        area_slots = {'breakroom': 0, 'writing': 0, 'error': 0}
        result = []
        active_count = 0
        idle_count = 0
        error_count = 0

        for agent in agents:
            raw_state = agent.get('state', 'idle')
            normalized = normalize_agent_state(raw_state)
            area = STATE_TO_AREA_MAP.get(normalized, 'breakroom')

            slot = area_slots.get(area, 0)
            area_slots[area] = slot + 1
            positions = AREA_POSITIONS.get(area, AREA_POSITIONS['breakroom'])
            pos = positions[slot % len(positions)]

            detail = agent.get('detail', '')
            progress = parse_progress_from_detail(detail)

            if normalized == 'idle':
                idle_count += 1
            elif normalized == 'error':
                error_count += 1
            else:
                active_count += 1

            identity = identity_map.get(agent.get('id', ''), {'name': agent.get('id', ''), 'emoji': '🤖'})

            result.append({
                'id': agent.get('id', ''),
                'name': identity['name'],
                'emoji': identity['emoji'],
                'state': normalized,
                'area': area,
                'detail': detail,
                'progress': progress,
                'slotIndex': slot,
                'position': pos,
            })

        return jsonify({
            'ok': True,
            'agents': result,
            'totalActive': active_count,
            'totalIdle': idle_count,
            'totalError': error_count,
            'timestamp': datetime.now().isoformat(),
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# === Main ===

if __name__ == "__main__":
    port = int(os.getenv("OPENCLAW_DASHBOARD_PORT") or "19001")
    debug = os.getenv("FLASK_DEBUG", "0").lower() in {"1", "true", "yes"}

    print(f"[OpenClaw Dashboard] Starting server on port {port}...")
    print(f"[OpenClaw Dashboard] Frontend: {FRONTEND_DIR}")
    print(f"[OpenClaw Dashboard] Memory: {MEMORY_DIR}")
    print(f"[OpenClaw Dashboard] Workspace: {OPENCLAW_WORKSPACE}")

    app.run(host="0.0.0.0", port=port, debug=debug)
