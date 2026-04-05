#!/usr/bin/env python3
"""Asset management for OpenClaw Dashboard.

Handles file uploads, GIF to sprite sheet conversion, and asset listing.
"""

import os
import uuid
import json
import time
from PIL import Image

# Upload directory
UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'backend', 'uploads'
)
# Asset index file
INDEX_FILE = os.path.join(UPLOAD_DIR, 'assets_index.json')
# Allowed file types
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
# Maximum file size (10MB)
MAX_FILE_SIZE = 10 * 1024 * 1024
# Asset types mapping by extension
ASSET_TYPE_MAP = {
    '.png': 'image',
    '.jpg': 'image',
    '.jpeg': 'image',
    '.gif': 'image',
    '.webp': 'image',
}


def allowed_file(filename):
    """Check if file extension is allowed."""
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def get_asset_type(filename):
    """Get asset type from filename."""
    ext = os.path.splitext(filename)[1].lower()
    return ASSET_TYPE_MAP.get(ext, 'unknown')


def load_index():
    """Load asset index from file."""
    if not os.path.exists(INDEX_FILE):
        return {}
    try:
        with open(INDEX_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_index(index):
    """Save asset index to file."""
    os.makedirs(os.path.dirname(INDEX_FILE), exist_ok=True)
    with open(INDEX_FILE, 'w', encoding='utf-8') as f:
        json.dump(index, f, indent=2)


def convert_gif_to_spritesheet(gif_path, output_dir, frame_width=None, frame_height=None):
    """Convert GIF to sprite sheet (PNG grid).

    Returns path to generated sprite sheet or None on failure.
    """
    try:
        with Image.open(gif_path) as img:
            # Get frame count
            frames = []
            frame_idx = 0
            while True:
                try:
                    frame = img.copy()
                    # Convert to RGB if necessary (GIFs may have palette mode)
                    if frame.mode in ('RGBA', 'LA', 'P'):
                        # Create white background for transparency
                        background = Image.new('RGB', frame.size, (255, 255, 255))
                        if frame.mode == 'P':
                            frame = frame.convert('RGBA')
                        background.paste(frame, mask=frame.split()[-1] if frame.mode == 'RGBA' else None)
                        frame = background
                    elif frame.mode != 'RGB':
                        frame = frame.convert('RGB')
                    frames.append(frame)
                    frame_idx += 1
                    img.seek(frame_idx)
                except EOFError:
                    break

            if len(frames) == 0:
                return None

            # Get dimensions from first frame
            if frame_width is None:
                frame_width = frames[0].width
            if frame_height is None:
                frame_height = frames[0].height

            # Calculate grid dimensions (try to make it as square as possible)
            import math
            cols = math.ceil(math.sqrt(len(frames)))
            rows = math.ceil(len(frames) / cols)

            # Create sprite sheet
            sprite_sheet = Image.new('RGB', (cols * frame_width, rows * frame_height), (255, 255, 255))

            for idx, frame in enumerate(frames):
                # Resize frame if needed
                if frame.size != (frame_width, frame_height):
                    frame = frame.resize((frame_width, frame_height), Image.LANCZOS)

                col = idx % cols
                row = idx // cols
                sprite_sheet.paste(frame, (col * frame_width, row * frame_height))

            # Save sprite sheet
            base_name = os.path.splitext(os.path.basename(gif_path))[0]
            output_path = os.path.join(output_dir, f"{base_name}-spritesheet.png")
            sprite_sheet.save(output_path, 'PNG')

            return output_path

    except Exception as e:
        print(f"convert_gif_to_spritesheet failed: {e}")
        return None


def save_uploaded_file(file, asset_type='general'):
    """Save an uploaded file.

    Args:
        file: FileStorage object from request.files
        asset_type: Type of asset (character, decoration, background, etc.)

    Returns:
        Tuple of (success, result_dict_or_error_message)
    """
    if not file or not file.filename:
        return False, "No file provided"

    filename = file.filename
    if not allowed_file(filename):
        return False, f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"

    # Check file size
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    if file_size > MAX_FILE_SIZE:
        return False, f"File too large. Maximum size: {MAX_FILE_SIZE // (1024*1024)}MB"

    # Generate UUID filename
    ext = os.path.splitext(filename)[1].lower()
    unique_id = uuid.uuid4().hex[:12]
    new_filename = f"{unique_id}{ext}"

    # Ensure upload directory exists
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    filepath = os.path.join(UPLOAD_DIR, new_filename)

    # Save file
    try:
        file.save(filepath)
    except Exception as e:
        return False, f"Failed to save file: {e}"

    # Convert GIF to sprite sheet
    sprite_sheet_url = None
    if ext == '.gif':
        sprite_path = convert_gif_to_spritesheet(filepath, UPLOAD_DIR)
        if sprite_path:
            sprite_sheet_url = f'/uploads/{os.path.basename(sprite_path)}'

    # Add to index
    asset_id = f"asset_{unique_id}"
    index = load_index()
    index[asset_id] = {
        'id': asset_id,
        'original_filename': filename,
        'filename': new_filename,
        'url': f'/uploads/{new_filename}',
        'type': get_asset_type(filename),
        'assetType': asset_type,
        'spriteSheetUrl': sprite_sheet_url,
        'createdAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'size': os.path.getsize(filepath),
    }
    save_index(index)

    return True, index[asset_id]


def list_assets(asset_type=None):
    """List all uploaded assets.

    Args:
        asset_type: Optional filter by asset type

    Returns:
        List of asset dicts
    """
    index = load_index()
    assets = list(index.values())

    if asset_type:
        assets = [a for a in assets if a.get('assetType') == asset_type]

    # Sort by creation time (newest first)
    assets.sort(key=lambda x: x.get('createdAt', ''), reverse=True)

    return assets


def delete_asset(asset_id):
    """Delete an asset by ID.

    Returns:
        Tuple of (success, message)
    """
    index = load_index()

    if asset_id not in index:
        return False, "Asset not found"

    asset = index[asset_id]
    filename = asset.get('filename')
    sprite_sheet_url = asset.get('spriteSheetUrl')

    # Delete main file
    if filename:
        filepath = os.path.join(UPLOAD_DIR, filename)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception as e:
                return False, f"Failed to delete file: {e}"

    # Delete sprite sheet if exists
    if sprite_sheet_url:
        sprite_filename = os.path.basename(sprite_sheet_url)
        sprite_path = os.path.join(UPLOAD_DIR, sprite_filename)
        if os.path.exists(sprite_path):
            try:
                os.remove(sprite_path)
            except Exception:
                pass  # Don't fail if sprite sheet deletion fails

    # Remove from index
    del index[asset_id]
    save_index(index)

    return True, "Asset deleted"


def get_asset(asset_id):
    """Get a single asset by ID."""
    index = load_index()
    return index.get(asset_id)
