from librarian.data_librarian import librarian
from strategy.strategy_memory import update_meta_keywords
from utils.logger import log_event


def decay_keywords():
    meta = librarian.get_meta_keywords()
    decay_log = {}

    for k in list(meta.keys()):
        meta[k] *= 0.95
        if meta[k] < 0.05:
            decay_log[k] = "decayed"
            del meta[k]

    if decay_log:
        log_event(f"🧠 Meta keywords decayed: {list(decay_log.keys())}")

    # Apply update to strategy memory under the special system key
    update_meta_keywords("meta_decay", meta)
