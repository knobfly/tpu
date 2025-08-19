from datetime import datetime, timedelta

from memory.token_confidence_engine import decay_token_confidence
from memory.token_memory_index import token_memory_index
from utils.logger import log_event


def run_memory_decay(hours: int = 168):
    decay_token_confidence(hours=hours)
    token_memory_index.prune_old_entries(hours=hours)

    log_event(f"[MemoryDecay] 🧹 Decayed memory entries older than {hours} hours.")
