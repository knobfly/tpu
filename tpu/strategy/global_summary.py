import json
import os
from collections import Counter

OUTCOME_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/token_outcomes.json"

def load_outcomes():
    if not os.path.exists(OUTCOME_FILE):
        return {}
    try:
        with open(OUTCOME_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def summarize_outcomes():
    """
    Generates global summary stats:
    - Total tokens evaluated
    - Outcome distribution
    - Creator reuse frequency
    """
    data = load_outcomes()
    outcome_counter = Counter()
    creator_counter = Counter()

    for token, logs in data.items():
        for log in logs:
            outcome = log.get("outcome")
            meta = log.get("meta", {})

            if outcome:
                outcome_counter[outcome] += 1

            creator = meta.get("creator", "").lower()
            if creator:
                creator_counter[creator] += 1

    top_creators = creator_counter.most_common(10)

    return {
        "total_tokens": len(data),
        "outcome_counts": dict(outcome_counter),
        "top_creators": top_creators
    }
