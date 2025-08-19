import json
import os
from collections import Counter, defaultdict

from memory.token_memory_index import get_all_token_metadata
from strategy.reinforcement_tracker import load_reinforcement_log
from utils.embedding_utils import cosine_similarity, get_reasoning_embedding

KEYWORD_GROUPS_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/keyword_groups.json"
SIMILARITY_THRESHOLD = 0.85

def extract_all_keywords():
    tokens = get_all_token_metadata()
    seen = set()
    all_keywords = []

    for token in tokens:
        for field in ["name", "symbol", "creator", "description"]:
            val = token.get(field, "").lower()
            for word in val.split():
                word = word.strip("(),:.!?-").lower()
                if word and word not in seen:
                    seen.add(word)
                    all_keywords.append(word)

    logs = load_reinforcement_log()
    for log in logs:
        for reason in log.get("reasoning", []):
            words = reason.split()
            for word in words:
                word = word.strip("(),:.!?-").lower()
                if word and word not in seen:
                    seen.add(word)
                    all_keywords.append(word)

    return list(set(all_keywords))

def cluster_keywords(keywords, threshold=SIMILARITY_THRESHOLD):
    clusters = []
    used = set()

    for word in keywords:
        if word in used:
            continue
        group = [word]
        emb1 = get_reasoning_embedding(word)

        for other in keywords:
            if other == word or other in used:
                continue
            emb2 = get_reasoning_embedding(other)
            if cosine_similarity(emb1, emb2) >= threshold:
                group.append(other)
                used.add(other)

        used.add(word)
        clusters.append(sorted(group))

    return clusters

def build_keyword_map():
    keywords = extract_all_keywords()
    clusters = cluster_keywords(keywords)
    keyword_map = {}

    for idx, group in enumerate(clusters):
        label = group[0]
        for word in group:
            keyword_map[word] = label

    try:
        with open(KEYWORD_GROUPS_FILE, "w") as f:
            json.dump(keyword_map, f, indent=2)
    except:
        pass

    return keyword_map
