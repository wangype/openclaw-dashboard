#!/usr/bin/env python3
"""Gemini Image API client for OpenClaw Dashboard.

Generates pixel-art style background images using Google's Gemini API.
"""

import os
import time
import uuid
import json
import threading
import requests

# Generation tasks storage (in-memory)
generation_tasks = {}
# Background directory
BACKGROUND_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'frontend', 'uploads', 'backgrounds'
)
# Config file for current background
CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'runtime_config.json'
)


def load_runtime_config():
    """Load runtime config from file."""
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_runtime_config(config):
    """Save runtime config to file."""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)


def get_current_background():
    """Get current active background ID."""
    config = load_runtime_config()
    return config.get('current_background')


def set_current_background(background_id):
    """Set current active background."""
    config = load_runtime_config()
    config['current_background'] = background_id
    save_runtime_config(config)


def list_backgrounds():
    """List all generated backgrounds."""
    os.makedirs(BACKGROUND_DIR, exist_ok=True)
    backgrounds = []

    # Read index file if exists
    index_file = os.path.join(BACKGROUND_DIR, 'index.json')
    index_data = {}
    if os.path.exists(index_file):
        try:
            with open(index_file, 'r', encoding='utf-8') as f:
                index_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            index_data = {}

    # Scan directory for images
    for filename in os.listdir(BACKGROUND_DIR):
        if filename.endswith(('.png', '.jpg', '.webp')) and filename != 'index.json':
            bg_id = filename.rsplit('.', 1)[0]
            bg_info = index_data.get(bg_id, {})
            backgrounds.append({
                'id': bg_id,
                'url': f'/uploads/backgrounds/{filename}',
                'prompt': bg_info.get('prompt', ''),
                'createdAt': bg_info.get('createdAt', ''),
            })

    # Sort by creation time
    backgrounds.sort(key=lambda x: x.get('createdAt', ''), reverse=True)

    current = get_current_background()
    return backgrounds, current


def save_background_index(bg_id, prompt):
    """Save background metadata to index."""
    index_file = os.path.join(BACKGROUND_DIR, 'index.json')
    index_data = {}
    if os.path.exists(index_file):
        try:
            with open(index_file, 'r', encoding='utf-8') as f:
                index_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            index_data = {}

    index_data[bg_id] = {
        'prompt': prompt,
        'createdAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }

    with open(index_file, 'w', encoding='utf-8') as f:
        json.dump(index_data, f, indent=2)


def generate_background(prompt, style="pixel-art", width=1280, height=720):
    """Call Gemini Image API to generate pixel art background.

    Returns task_id for async generation.
    """
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        # Return a task with immediate failure
        task_id = f"bg_{uuid.uuid4().hex[:8]}"
        generation_tasks[task_id] = {
            'status': 'failed',
            'error': 'GEMINI_API_KEY not configured',
            'prompt': prompt,
            'created_at': time.time(),
        }
        return task_id

    task_id = f"bg_{uuid.uuid4().hex[:8]}"
    generation_tasks[task_id] = {
        'status': 'generating',
        'progress': 0,
        'prompt': prompt,
        'created_at': time.time(),
    }

    def _run():
        try:
            # Build prompt for pixel art style
            full_prompt = (
                f"Pixel art style office background: {prompt}. "
                f"16-bit retro game aesthetic, top-down view, "
                f"pixelated, limited color palette, game tile background. "
                f"Resolution: {width}x{height}."
            )

            generation_tasks[task_id]['progress'] = 30

            # Call Gemini API via HTTP
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateImages?key={api_key}"

            payload = {
                "model": "gemini-2.0-flash-exp",
                "prompt": full_prompt,
                "config": {
                    "aspectRatio": f"{width}:{height}",
                    "numberOfImages": 1,
                }
            }

            headers = {
                'Content-Type': 'application/json',
            }

            response = requests.post(url, json=payload, headers=headers, timeout=120)
            response.raise_for_status()
            result = response.json()

            generation_tasks[task_id]['progress'] = 80

            # Extract and save image
            os.makedirs(BACKGROUND_DIR, exist_ok=True)
            filename = f"{task_id}.png"
            filepath = os.path.join(BACKGROUND_DIR, filename)

            # Get base64 image data
            if 'images' in result and len(result['images']) > 0:
                import base64
                image_data = result['images'][0].get('image') or result['images'][0].get('bytesBase64Encoded')
                if image_data:
                    with open(filepath, 'wb') as f:
                        f.write(base64.b64decode(image_data))

                    # Save to index
                    save_background_index(task_id, prompt)

                    generation_tasks[task_id].update({
                        'status': 'done',
                        'progress': 100,
                        'filename': filename,
                        'url': f'/uploads/backgrounds/{filename}',
                        'completed_at': time.time(),
                    })
                else:
                    raise ValueError("No image data in response")
            else:
                raise ValueError("No images in response")

        except Exception as e:
            generation_tasks[task_id].update({
                'status': 'failed',
                'error': str(e),
            })

    # Start background thread
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return task_id


def get_task_status(task_id):
    """Get generation task status."""
    return generation_tasks.get(task_id)
