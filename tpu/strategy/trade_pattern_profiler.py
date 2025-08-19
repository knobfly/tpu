import json
import os
from typing import Any, Dict

PATTERN_FILE = "/home/ubuntu/nyx/runtime/memory/trade_patterns.json"

def load_patterns() -> Dict[str, Any]:
    if not os.path.exists(PATTERN_FILE):
        return {}
    try:
        with open(PATTERN_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_patterns(data: Dict[str, Any]):
    try:
        with open(PATTERN_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def log_trade_pattern(token_address: str, pattern: str):
    """
    Records a trade behavior pattern for the token.

    Valid patterns: 'early_exit', 'tp_hit', 'sl_hit', 'held_long', 'missed_rebound', etc.
    """
    patterns = load_patterns()
    record = patterns.get(token_address, {})

    if pattern not in record:
        record[pattern] = 0
    record[pattern] += 1

    patterns[token_address] = record
    save_patterns(patterns)

def get_trade_patterns(token_address: str) -> Dict[str, int]:
    """
    Returns the trade pattern stats for a token.
    """
    patterns = load_patterns()
    return patterns.get(token_address, {})
