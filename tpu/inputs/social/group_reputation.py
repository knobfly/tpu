# modules/group_reputation.py

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict

from librarian.data_librarian import librarian

REPUTATION_FILE = "/home/ubuntu/nyx/runtime/logs/group_reputation.json"
DEFAULT_RECORD = {"tokens": 0, "rug": 0, "moon": 0, "loss": 0, "profit": 0, "score": 0}
GROUP_REPUTATION_LOG = "/home/ubuntu/nyx/runtime/logs/group_quality.json"

def load_reputation() -> Dict[str, Any]:
    if not os.path.exists(REPUTATION_FILE):
        return {}
    try:
        with open(REPUTATION_FILE, "r") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                logging.warning(f"⚠️ Group reputation file is not a dict. Resetting.")
                return {}
            return data
    except Exception as e:
        logging.error(f"❌ Failed to load group reputation: {e}")
        return {}

def save_reputation(data: Dict[str, Any]):
    try:
        with open(REPUTATION_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.error(f"❌ Failed to save group reputation: {e}")

def update_group_score(group_name: str, outcome: str):
    """
    Updates a Telegram group's reputation based on trade outcome.
    Outcome can be: 'rug', 'moon', 'profit', 'loss', 'dead'
    """
    try:
        data = load_reputation()
        record = data.get(group_name, DEFAULT_RECORD.copy())

        record["tokens"] += 1
        if outcome in record:
            record[outcome] += 1

        impact = {
            "rug": -5,
            "moon": 4,
            "profit": 2,
            "loss": -2,
            "dead": -3
        }

        record["score"] += impact.get(outcome, 0)
        data[group_name] = record
        save_reputation(data)

        logging.info(f"📊 Updated group score: {group_name} → {record['score']} ({outcome})")
    except Exception as e:
        logging.warning(f"⚠️ Group rep update failed for {group_name}: {e}")

def get_wallet_group_mentions(wallet: str) -> list:
    """
    Returns a list of group names where the wallet was mentioned.
    Used to detect social behavior clusters and influence patterns.
    """
    try:
        mention_logs = librarian.load_json_file("/home/ubuntu/nyx/runtime/memory/mentions.json") or []
    except Exception:
        mention_logs = []

    mentioned_groups = set()
    for mention in mention_logs:
        if isinstance(mention, dict) and mention.get("wallet") == wallet:
            group = mention.get("group")
            if group:
                mentioned_groups.add(group)

    return sorted(mentioned_groups)

def get_group_score(group_name: str) -> int:
    data = load_reputation()
    record = data.get(group_name, {})
    return record.get("score", 0)

def is_group_flagged(group_name: str) -> bool:
    """
    Returns True if a group is flagged as high-risk.
    """
    return get_group_score(group_name) <= -10

def load_group_scores() -> Dict[str, Dict]:
    if not os.path.exists(GROUP_REPUTATION_LOG):
        return {}
    try:
        with open(GROUP_REPUTATION_LOG, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def get_group_quality_score(group_id: str, decay_days: int = 3) -> float:
    """
    Returns the reputation score for a Telegram group.
    Scores decay slightly over time.
    """
    data = load_group_scores()
    if group_id not in data:
        return 0.0

    entry = data[group_id]
    base_score = float(entry.get("score", 0))
    last_updated = entry.get("last_updated")

    if last_updated:
        try:
            ts = datetime.fromisoformat(last_updated)
            days_ago = (datetime.utcnow() - ts).days
            decay_factor = max(0.1, 1 - (days_ago / decay_days))
            return round(base_score * decay_factor, 2)
        except Exception:
            pass

    return round(base_score, 2)
