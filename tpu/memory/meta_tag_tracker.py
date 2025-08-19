import json
import logging
import os
from datetime import datetime, timedelta

META_TAG_FILE = "/home/ubuntu/nyx/runtime/logs/meta_tags.json"
_meta_boosts = {}  # { "tag": { "boost": float, "timestamp": str } }


def _load_tags():
    if not os.path.exists(META_TAG_FILE):
        return {}
    try:
        with open(META_TAG_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"[MetaTagTracker] Failed to load tags: {e}")
        return {}


def _save_tags(data):
    try:
        with open(META_TAG_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.warning(f"[MetaTagTracker] Failed to save tags: {e}")


def add_meta_tag(token_address: str, tag: str):
    """
    Adds a tag to a token's meta-tag list.
    """
    data = _load_tags()
    tags = set(data.get(token_address, []))
    tags.add(tag)
    data[token_address] = list(tags)
    _save_tags(data)


def get_meta_tags(token_address: str):
    """
    Returns all tags associated with a token.
    """
    data = _load_tags()
    return data.get(token_address, [])


def record_event(token_address: str, event: str):
    """
    Records an event as a timestamped meta-tag.
    """
    tag = f"{event}_{datetime.utcnow().isoformat()}"
    add_meta_tag(token_address, tag)

def search_tokens_by_tag(tag: str):
    tags = _load_tags()
    return [token for token, data in tags.items() if tag in data.get("tags", [])]

def get_meta_trend_boost(tag: str) -> float:
    """
    Returns a boost score (0.0 - 1.0) for a meta tag, default 0.0 if not found.
    """
    try:
        entry = _meta_boosts.get(tag)
        if not entry:
            return 0.0
        return float(entry.get("boost", 0.0))
    except Exception as e:
        logging.warning(f"[MetaTagTracker] Failed get_meta_trend_boost for {tag}: {e}")
        return 0.0


def set_meta_trend_boost(tag: str, boost: float):
    """
    Sets or updates the boost score for a meta tag.
    """
    try:
        _meta_boosts[tag] = {
            "boost": max(0.0, min(1.0, float(boost))),
            "timestamp": datetime.utcnow().isoformat()
        }
        logging.info(f"[MetaTagTracker] {tag} boost set to {_meta_boosts[tag]['boost']:.2f}")
    except Exception as e:
        logging.warning(f"[MetaTagTracker] Failed set_meta_trend_boost for {tag}: {e}")


def get_all_meta_boosts():
    """
    Returns a snapshot of all stored meta boosts.
    """
    return dict(_meta_boosts)
