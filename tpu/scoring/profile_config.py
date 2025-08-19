# scoring/profile_config.py
import os

import yaml
from core.live_config import config

_CACHE = None

def load_profiles():
    global _CACHE
    if _CACHE:
        return _CACHE
    # allow override path via live_config
    path = (config.get("score_profiles_path")
            or "/home/ubuntu/nyx/config/score_profiles.yml")
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    _CACHE = data
    return data

def get_profile(mode: str, name: str) -> dict:
    data = load_profiles()
    return (data.get(mode, {}) or {}).get(name, {}) or {}

def get_defaults() -> dict:
    data = load_profiles()
    return data.get("defaults", {}) or {}
