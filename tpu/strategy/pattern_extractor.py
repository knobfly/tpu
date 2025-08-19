import json
import os
from collections import Counter, defaultdict

OUTCOME_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/token_outcomes.json"

def load_token_outcome_data():
    if not os.path.exists(OUTCOME_FILE):
        return {}
    try:
        with open(OUTCOME_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def extract_failure_patterns():
    """
    Scans token outcome memory for patterns in failed trades:
    - Creator address reuse
    - Common fee types or scores
    - Trigger keywords
    - Repeated LP behavior
    """
    data = load_token_outcome_data()
    creator_failures = Counter()
    trigger_failures = Counter()
    fee_ranges = Counter()
    lp_behavior = Counter()

    for token, entries in data.items():
        for entry in entries:
            outcome = entry.get("outcome", "")
            if outcome not in ["rug", "loss", "dead"]:
                continue

            meta = entry.get("meta", {})
            creator = meta.get("creator", "").lower()
            if creator:
                creator_failures[creator] += 1

            # Optional metadata fields
            total_fee = meta.get("fees", {}).get("total_tax", 0)
            if total_fee:
                rounded = round(float(total_fee), -1)  # e.g., 20% → 20
                fee_ranges[rounded] += 1

            for reason in meta.get("reasoning", []):
                trigger_failures[reason] += 1

            lp_status = meta.get("lp_status", "")
            if lp_status:
                lp_behavior[lp_status] += 1

    return {
        "top_creators": creator_failures.most_common(10),
        "trigger_words": trigger_failures.most_common(10),
        "fee_ranges": fee_ranges.most_common(10),
        "lp_behavior": lp_behavior.most_common(10)
    }
