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
