import json
import os
from collections import defaultdict
from datetime import datetime

from utils.embedding_utils import cosine_similarity, get_reasoning_embedding

REASONING_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/semantic_reasoning_log.json"
EMBED_CACHE_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/reasoning_embeddings.json"
MAX_HISTORY = 300

SIMILARITY_THRESHOLD = 0.85

def load_reasoning_log():
    if not os.path.exists(REASONING_FILE):
        return []
    try:
        with open(REASONING_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_reasoning_log(log):
    try:
        with open(REASONING_FILE, "w") as f:
            json.dump(log[-MAX_HISTORY:], f, indent=2)
    except:
        pass

def load_embedding_cache():
    if not os.path.exists(EMBED_CACHE_FILE):
        return {}
    try:
        with open(EMBED_CACHE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_embedding_cache(cache):
    try:
        with open(EMBED_CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except:
        pass

def store_reasoning_with_outcome(reasoning_tags: list, outcome: str):
    log = load_reasoning_log()
    cache = load_embedding_cache()

    for tag in reasoning_tags:
        if tag not in cache:
            cache[tag] = get_reasoning_embedding(tag)

    for tag in reasoning_tags:
        log.append({
            "tag": tag,
            "outcome": outcome,
            "timestamp": datetime.utcnow().isoformat()
        })

    save_embedding_cache(cache)
    save_reasoning_log(log)

def get_similar_reasoning_clusters():
    """
    Returns a dict of {cluster_tag: [variants]} using cosine similarity.
    """
    cache = load_embedding_cache()
    grouped = defaultdict(list)

    used = set()

    for tag, embed in cache.items():
        if tag in used:
            continue
        group = [tag]
        used.add(tag)
        for other, other_embed in cache.items():
            if other == tag or other in used:
                continue
            sim = cosine_similarity(embed, other_embed)
            if sim >= SIMILARITY_THRESHOLD:
                group.append(other)
                used.add(other)
        grouped[tag] = group

    return dict(grouped)
