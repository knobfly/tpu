# /memory/strategy_snapshot.py

import json
import os
from datetime import datetime

from librarian.data_librarian import librarian
from strategy.strategy_memory import get_strategy_performance, get_tagged_tokens

SNAPSHOT_DIR = "/home/ubuntu/nyx/runtime/data/strategy_snapshots"

def save_snapshot():
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    snapshot = {
        "timestamp": datetime.utcnow().isoformat(),
        "performance": get_strategy_performance(),
        "tagged_tokens": get_tagged_tokens(),
        "meta_keywords": librarian.get_meta_keywords()
    }
    fname = f"snapshot_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    with open(os.path.join(SNAPSHOT_DIR, fname), "w") as f:
        json.dump(snapshot, f, indent=2)
    return snapshot
