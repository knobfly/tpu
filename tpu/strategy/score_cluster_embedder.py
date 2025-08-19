import json
import os
from collections import defaultdict

from utils.token_utils import is_blacklisted_token

MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/score_clusters.json"

def load_cluster_memory():
    if not os.path.exists(MEMORY_FILE):
        return defaultdict(list)
    try:
        with open(MEMORY_FILE, "r") as f:
            data = json.load(f)
            return defaultdict(list, data)
    except Exception:
        return defaultdict(list)

def save_cluster_memory(data):
    try:
        with open(MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def tag_meta_cluster(context: dict) -> str:
    """
    Creates a cluster tag based on metadata patterns.
    Example outputs: "bundle-whale", "low-tax-fresh", "unlocked-honeypot", etc.
    """
    tags = []

    if context.get("bundle_launch"):
        tags.append("bundle")
    if context.get("wallets", {}).get("whales_present"):
        tags.append("whale")
    if context.get("txn", {}).get("sniper_pressure", 0) > 0:
        tags.append("snipers")
    if context.get("lp_status") == "unlocked":
        tags.append("unlocked")
    if context.get("lp_status") == "locked":
        tags.append("locked")
    if context.get("age", 999) < 3:
        tags.append("fresh")
    if context.get("age", 0) > 30:
        tags.append("old")
    if context.get("fees", 0) <= 5:
        tags.append("low-tax")
    if is_blacklisted_token(context.get("token_address")):
        tags.append("blacklisted")

    return "-".join(tags) or "unclassified"

def embed_trade_context(token_address: str, final_score: float, verdict: str, context: dict):
    """
    Stores the final score, verdict, and tagged cluster to memory for long-term pattern learning.
    """
    memory = load_cluster_memory()
    cluster = tag_meta_cluster(context)
    entry = {
        "score": final_score,
        "action": verdict,
        "age": context.get("age"),
        "buyers": context.get("buyers", 0),
        "whales": context.get("wallets", {}).get("whales_present", False)
    }
    memory[cluster].append(entry)
    memory[cluster] = memory[cluster][-75:]  # keep last 75 examples per cluster
    save_cluster_memory(memory)
