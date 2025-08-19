import json
import os
from collections import defaultdict

from utils.embedding_utils import cosine_similarity, get_reasoning_embedding

REASONING_LOG = "/home/ubuntu/nyx/runtime/memory/strategy/reasoning_weights.json"

def load_reasoning_weights():
    if not os.path.exists(REASONING_LOG):
        return {}
    try:
        with open(REASONING_LOG, "r") as f:
            return json.load(f)
    except:
        return {}

def cluster_similar_tags(tags: list, threshold=0.85) -> list:
    """
    Groups tags by cosine similarity of their embeddings.
    """
    clusters = []
    used = set()

    for tag in tags:
        if tag in used:
            continue
        group = [tag]
        emb1 = get_reasoning_embedding(tag)

        for other in tags:
            if other == tag or other in used:
                continue
            emb2 = get_reasoning_embedding(other)
            if cosine_similarity(emb1, emb2) >= threshold:
                group.append(other)
                used.add(other)

        used.add(tag)
        clusters.append(group)

    return clusters

def summarize_clusters():
    data = load_reasoning_weights()
    tag_list = list(data.keys())
    clusters = cluster_similar_tags(tag_list)

    summaries = []
    for group in clusters:
        win_tags = [tag for tag in group if data[tag].get("profit", 0) > data[tag].get("rug", 0)]
        if not win_tags:
            continue
        summary = f"Profitable patterns included: {', '.join(win_tags)}"
        summaries.append(summary)

    return summaries
