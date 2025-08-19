import json
import logging
import os
from datetime import datetime


def safe_write_json(path: str, data: dict) -> None:
    """
    Writes a JSON file atomically to prevent corruption.
    """
    try:
        temp_path = path + ".tmp"
        with open(temp_path, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(temp_path, path)
    except Exception as e:
        logging.error(f"[FileUtils] Failed to write {path}: {e}")

def safe_read_json(path: str, default: dict = None) -> dict:
    """
    Safely reads JSON from a file, returns default if failed.
    """
    try:
        if not os.path.exists(path):
            return default or {}
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"[FileUtils] Failed to read {path}: {e}")
        return default or {}

def safe_load_json(path: str, default=None):
    """
    Safely loads JSON from a file path. Returns default on failure.
    """
    if default is None:
        default = {}
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"[FileUtils] Failed to load {path}: {e}")
        return default

def safe_save_json(path: str, data):
    """
    Safely saves JSON to a file. Overwrites existing file.
    """
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.error(f"[FileUtils] Failed to save {path}: {e}")

def append_log_line(path: str, text: str) -> None:
    """
    Appends a single line to a file with timestamp.
    """
    try:
        with open(path, "a") as f:
            f.write(f"{datetime.utcnow().isoformat()} - {text}\n")
    except Exception as e:
        logging.warning(f"[FileUtils] Failed to append to {path}: {e}")
