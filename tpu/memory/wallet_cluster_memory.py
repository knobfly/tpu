import json
import logging
import os
from datetime import datetime

CLUSTER_FILE = "/home/ubuntu/nyx/runtime/logs/wallet_clusters.json"


# === Internal Load/Save ===
def _load_clusters():
    if not os.path.exists(CLUSTER_FILE):
        return {}
    try:
        with open(CLUSTER_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"[WalletClusterMemory] Failed to load: {e}")
        return {}


def _save_clusters(data):
    try:
        with open(CLUSTER_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.warning(f"[WalletClusterMemory] Failed to save: {e}")


# === Cluster Management ===
def add_wallet_to_cluster(cluster_name: str, wallet: str):
    """
    Add a wallet to a specific cluster.
    """
    clusters = _load_clusters()
    cluster = clusters.get(cluster_name, {"wallets": [], "last_update": None, "events": []})
    if wallet not in cluster["wallets"]:
        cluster["wallets"].append(wallet)
    cluster["last_update"] = datetime.utcnow().isoformat()
    clusters[cluster_name] = cluster
    _save_clusters(clusters)


def get_cluster_for_wallet(wallet: str):
    """
    Find which cluster (if any) a wallet belongs to.
    """
    clusters = _load_clusters()
    for cluster_name, cluster_data in clusters.items():
        if wallet in cluster_data.get("wallets", []):
            return cluster_name
    return None


def get_cluster(cluster_name: str):
    """
    Return the details for a given cluster name.
    """
    return _load_clusters().get(cluster_name, {})


def list_clusters():
    """
    List all cluster names.
    """
    return list(_load_clusters().keys())


def get_all_clusters():
    """
    Return the full cluster data dictionary.
    """
    return _load_clusters()


# === Event Tracking ===
def record_cluster_event(cluster_name: str, event: str):
    """
    Record an event (e.g., large buy) associated with a cluster.
    """
    clusters = _load_clusters()
    cluster = clusters.get(cluster_name, {"wallets": [], "last_update": None, "events": []})

    event_tag = f"{event}_{datetime.utcnow().isoformat()}"
    cluster.setdefault("events", []).append(event_tag)
    cluster["last_update"] = datetime.utcnow().isoformat()
    clusters[cluster_name] = cluster
    _save_clusters(clusters)
