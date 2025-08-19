import json
import logging
import os
from datetime import datetime

THEME_MEMORY_FILE = "/home/ubuntu/nyx/runtime/logs/theme_memory.json"


def _load_themes():
    if not os.path.exists(THEME_MEMORY_FILE):
        return {}
    try:
        with open(THEME_MEMORY_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"[TagThemeMemory] Failed to load: {e}")
        return {}


def _save_themes(data):
    try:
        with open(THEME_MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.warning(f"[TagThemeMemory] Failed to save: {e}")


def tag_token_theme(token: str, themes: list):
    """
    Tags a token with themes (like AI, meme, defi, etc.).
    """
    memory = _load_themes()
    memory[token] = {
        "themes": themes,
        "timestamp": datetime.utcnow().isoformat()
    }
    _save_themes(memory)


def get_token_themes(token: str):
    return _load_themes().get(token, {}).get("themes", [])


def list_all_themes():
    memory = _load_themes()
    all_themes = set()
    for entry in memory.values():
        all_themes.update(entry.get("themes", []))
    return list(all_themes)
