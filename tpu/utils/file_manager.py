import json
import logging
import os
from typing import Any


def save_json(file_path: str, data: Any):
    try:
        with open(file_path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.warning(f"[FileManager] Failed to save {file_path}: {e}")

def load_json(file_path: str) -> Any:
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"[FileManager] Failed to load {file_path}: {e}")
        return {}
