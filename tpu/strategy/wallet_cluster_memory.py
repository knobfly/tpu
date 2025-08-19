import json
import os
from collections import defaultdict

WALLET_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/wallet_outcomes.json"
CLUSTER_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/wallet_clusters.json"
MAX_HISTORY = 100


def load_wallet_memory():
    if not os.path.exists(WALLET_FILE):
        return defaultdict(lambda: {"rug": 0, "dead": 0, "loss": 0, "profit": 0, "moon": 0})
    with open(WALLET_FILE, "r") as f:
        data = json.load(f)
        return defaultdict(lambda: {"rug": 0, "dead": 0, "loss": 0, "profit": 0, "moon": 0}, data)

def save_wallet_memory(data):
    with open(WALLET_FILE, "w") as f:
        json.dump(data, f, indent=2)

def update_wallet_outcome(wallet: str, outcome: str, tag=""):
    data = load_wallet_memory()
    wallet = wallet.lower().strip()
    if outcome in data[wallet]:
        data[wallet][outcome] += 1
    save_wallet_memory(data)
    return data[wallet]

def load_cluster_memory():
    if not os.path.exists(CLUSTER_FILE):
        return {}
    try:
        with open(CLUSTER_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_cluster_memory(data):
    try:
        with open(CLUSTER_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except:
        pass

def update_cluster_memory(cluster_id: str, token_address: str, outcome: str):
    """
    Updates memory for a cluster of wallets based on trade outcome.

    cluster_id: a unique ID representing a group of wallets
    outcome: 'profit', 'rug', 'loss', etc.
    """
    data = load_cluster_memory()

    if cluster_id not in data:
        data[cluster_id] = {
            "trades": [],
            "outcome_counts": defaultdict(int)
        }

    cluster = data[cluster_id]
    cluster["trades"].append({
        "token": token_address,
        "outcome": outcome
    })
    cluster["outcome_counts"][outcome] = cluster["outcome_counts"].get(outcome, 0) + 1

    # Limit memory size
    if len(cluster["trades"]) > MAX_HISTORY:
        cluster["trades"] = cluster["trades"][-MAX_HISTORY:]

    data[cluster_id] = cluster
    save_cluster_memory(data)

def summarize_cluster_outcomes(cluster_id: str):
    """
    Returns win/loss/rug ratio summary for a given cluster.
    """
    data = load_cluster_memory()
    cluster = data.get(cluster_id)
    if not cluster:
        return {}

    outcomes = cluster["outcome_counts"]
    total = sum(outcomes.values())
    summary = {
        k: round(v / total, 3) for k, v in outcomes.items()
    }
    summary["total_trades"] = total
    return summary
