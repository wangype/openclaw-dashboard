#!/usr/bin/env python3
"""OpenClaw CLI client for OpenClaw Dashboard backend.

Wraps openclaw CLI commands to fetch agent status, gateway health, and other data.
"""

import json
import subprocess


def run_openclaw_command(args, timeout=10):
    """Run an openclaw CLI command and parse JSON output.

    Args:
        args: Command arguments (e.g., ['agents', 'list', '--json'])
        timeout: Command timeout in seconds

    Returns:
        Parsed JSON dict or None if command fails
    """
    try:
        cmd = ['openclaw'] + args
        # Python 3.6 compatibility: use stdout/stderr instead of capture_output
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=timeout
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        return None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        print("run_openclaw_command failed:", e)
        return None


def get_agents_list():
    """Get list of all agents.

    Returns:
        List of agent dicts with id, name, status, etc.
    """
    result = run_openclaw_command(['agents', 'list', '--json'])
    if result and isinstance(result, list):
        return result
    return []


def get_gateway_health():
    """Get gateway health status.

    Returns:
        Dict with gateway status info (port, channels, connected, etc.)
    """
    # Try gateway health command first
    result = run_openclaw_command(['gateway', 'health', '--json'])
    if result:
        return {
            'ok': True,
            'data': result,
            'status': 'healthy' if result.get('ok') else 'unhealthy'
        }

    # Fallback: try system-presence call
    result = run_openclaw_command(['gateway', 'call', 'system-presence', '--json'])
    if result:
        return {
            'ok': True,
            'data': result,
            'status': 'healthy'
        }

    return {
        'ok': False,
        'data': None,
        'status': 'unreachable'
    }


def get_channel_health():
    """Get channel health status.

    Returns:
        List of channel health dicts
    """
    result = run_openclaw_command(['status', '--json'])
    if result and isinstance(result, list):
        return result
    return []


def get_agent_bindings():
    """Get agent routing bindings.

    Returns:
        Dict with binding rules
    """
    result = run_openclaw_command(['agents', 'bindings', '--json'])
    return result if result else {}


def get_system_status():
    """Get overall system status.

    Returns:
        Dict with system-wide status info
    """
    result = run_openclaw_command(['gateway', 'call', 'system-presence', '--json'])
    return result if result else {}


def get_agent_detail(agent_id):
    """Get detailed info for a specific agent.

    Args:
        agent_id: The agent ID to query

    Returns:
        Agent detail dict or None if not found
    """
    agents = get_agents_list()
    for agent in agents:
        if agent.get('id') == agent_id:
            return agent
    return None


def get_sessions_list(limit=20, agent_filter=None, active_minutes=None):
    """Get list of recent sessions.

    Calls: openclaw sessions --json --all-agents [--active N]

    Returns:
        Tuple of (sessions_list, total_count)
    """
    try:
        args = ['sessions', '--json', '--all-agents']
        if active_minutes:
            args.extend(['--active', str(active_minutes)])

        result = run_openclaw_command(args, timeout=15)
        if result and isinstance(result, dict) and 'sessions' in result:
            sessions = result.get('sessions', [])
            total = result.get('count', len(sessions))

            # Filter by agent if specified
            if agent_filter:
                sessions = [s for s in sessions if s.get('agentId') == agent_filter]

            # Apply limit
            sessions = sessions[:limit]
            return sessions, total
        return [], 0
    except Exception as e:
        print("get_sessions_list failed:", e)
        return [], 0


def get_agent_identity_map():
    """Get agentId -> {name, emoji} mapping for session data enrichment.

    Returns:
        Dict mapping agentId to identity info
    """
    agents = get_agents_list()
    return {
        a.get('id', 'unknown'): {
            'name': a.get('identityName') or a.get('name') or a.get('id', 'Unknown'),
            'emoji': a.get('identityEmoji') or '🤖',
            'model': a.get('model', ''),
        }
        for a in agents if a.get('id')
    }


def parse_session_key(key):
    """Parse session key to extract channel and kind.

    Key format: agent:{agentId}:{channel}:{kind}:{id}@domain
    Or: agent:{agentId}:{agentId}

    Returns:
        Dict with channel and kind
    """
    if not key:
        return {'channel': 'unknown', 'kind': 'unknown'}

    parts = key.split(':')
    if len(parts) >= 5:
        # agent:{agentId}:{channel}:{kind}:{id}@domain
        return {'channel': parts[2], 'kind': parts[3]}
    elif len(parts) >= 3:
        # agent:{agentId}:{agentId} or similar
        return {'channel': 'direct', 'kind': parts[2] if len(parts) > 2 else 'direct'}
    return {'channel': 'unknown', 'kind': 'unknown'}


def parse_progress_from_detail(detail):
    """Parse progress percentage from detail string.

    Matches patterns like [45%] or (78% complete) or 78%

    Returns:
        int 0-100 or None if no match
    """
    if not detail:
        return None

    import re
    # Try [NN%] pattern first
    match = re.search(r'\[(\d+)%\]', detail)
    if match:
        return min(100, max(0, int(match.group(1))))

    # Try (NN% complete) or (NN% finished) pattern
    match = re.search(r'\((\d+)%\s*(?:complete|finished)\)', detail, re.IGNORECASE)
    if match:
        return min(100, max(0, int(match.group(1))))

    # Try standalone NN% pattern
    match = re.search(r'(\d+)%', detail)
    if match:
        return min(100, max(0, int(match.group(1))))

    return None
