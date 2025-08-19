# /wallet_cluster_analyzer.py
# ----------------------------------------------------------------------
# Combined runtime wallet cluster detection + persistent cluster storage.
# ----------------------------------------------------------------------

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, Set

from core.live_config import config
from librarian.data_librarian import librarian
from special.insight_logger import log_ai_insight, log_scanner_insight
from utils.logger import log_event
from utils.service_status import update_status

# === Runtime Parameters ===
CLUSTER_WINDOW_SECONDS = 6
CLUSTER_MIN_COUNT = 3

recent_buys = defaultdict(list)
cluster_cache = set()
TRUSTED_WALLETS = set(config.get("trusted_wallets", []))
cabal_clusters = []
_wallet_behavior_log: Dict[str, List[Dict]] = {}  # token -> [{wallet, action, time, outcome}]

# === Persistent Storage ===
ROOT = Path(__file__).resolve().parents[1]
MEM_DIR = ROOT / "/home/ubuntu/nyx/runtime"
MEM_DIR.mkdir(parents=True, exist_ok=True)
STATE_PATH = MEM_DIR / "wallet_clusters.json"
_LOCK = RLock()

_CLUSTERS: Dict[str, Dict[str, Any]] = {}
_ADDR_TO_CLUSTER: Dict[str, str] = {}

# ---------------- Internal Persistence ----------------
def _now() -> float:
    return time.time()

def _load_state() -> None:
    global _CLUSTERS, _ADDR_TO_CLUSTER
    if not STATE_PATH.exists():
        _CLUSTERS = {}
        _ADDR_TO_CLUSTER = {}
        return
    try:
        with STATE_PATH.open("r") as f:
            data = json.load(f)
        _CLUSTERS = data.get("clusters", {})
        _ADDR_TO_CLUSTER = data.get("addr_to_cluster", {})
    except Exception:
        _CLUSTERS = {}
        _ADDR_TO_CLUSTER = {}

def _save_state() -> None:
    tmp = {
        "clusters": _CLUSTERS,
        "addr_to_cluster": _ADDR_TO_CLUSTER,
        "saved_at": _now(),
    }
    with STATE_PATH.open("w") as f:
        json.dump(tmp, f, separators=(",", ":"), ensure_ascii=False)

def _ensure_loaded():
    if not _CLUSTERS and not _ADDR_TO_CLUSTER:
        _load_state()

def _normalize(addr: str) -> str:
    return addr.lower()

def _get_or_create_cluster_for(addr: str) -> str:
    addr = _normalize(addr)
    if addr in _ADDR_TO_CLUSTER:
        return _ADDR_TO_CLUSTER[addr]
    cluster_id = addr
    _CLUSTERS[cluster_id] = {
        "wallets": [addr],
        "created_at": _now(),
        "updated_at": _now(),
        "tags": {},
        "meta": {},
        "active": True,
    }
    _ADDR_TO_CLUSTER[addr] = cluster_id
    return cluster_id

def _merge_clusters(primary_id: str, other_id: str) -> str:
    if primary_id == other_id:
        return primary_id
    if other_id not in _CLUSTERS:
        return primary_id
    if primary_id not in _CLUSTERS:
        primary_id, other_id = other_id, primary_id

    p = _CLUSTERS[primary_id]
    o = _CLUSTERS[other_id]

    for w in o["wallets"]:
        if w not in p["wallets"]:
            p["wallets"].append(w)
            _ADDR_TO_CLUSTER[w] = primary_id
    for tag, cnt in o.get("tags", {}).items():
        p["tags"][tag] = p["tags"].get(tag, 0) + cnt
    p["meta"] = {**p.get("meta", {}), **o.get("meta", {})}
    p["active"] = bool(p.get("active", True) or o.get("active", True))
    p["updated_at"] = _now()

    del _CLUSTERS[other_id]
    return primary_id

# ---------------- Runtime Logic ----------------
def get_clusters(min_wallets: int = 3, max_gap_minutes: int = 30):
    """
    Group wallets based on shared token entries within a time window.
    """
    logs = librarian.load_json_file("/home/ubuntu/nyx/runtime/wallet_logs/entry_logs.json") or []
    token_entries = {}

    for entry in logs:
        token = entry.get("token")
        wallet = entry.get("wallet")
        ts_str = entry.get("timestamp")
        if not token or not wallet or not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str)
        except Exception:
            continue
        token_entries.setdefault(token, []).append((wallet, ts))

    clusters = []
    for token, entries in token_entries.items():
        entries.sort(key=lambda x: x[1])
        cluster = []
        last_ts = None
        for wallet, ts in entries:
            if not last_ts or (ts - last_ts).total_seconds() <= max_gap_minutes * 60:
                cluster.append((wallet, ts))
            else:
                if len(cluster) >= min_wallets:
                    clusters.append({
                        "token": token,
                        "wallets": [w for w, _ in cluster],
                        "timestamp": cluster[0][1].isoformat()
                    })
                cluster = [(wallet, ts)]
            last_ts = ts
        if len(cluster) >= min_wallets:
            clusters.append({
                "token": token,
                "wallets": [w for w, _ in cluster],
                "timestamp": cluster[0][1].isoformat()
            })
    return clusters

def reset_clusters():
    global cabal_clusters
    cabal_clusters = []

def analyze_wallet_activity(wallet_tx_map: dict, min_overlap: int = 3, time_window_sec: int = 15):
    """
    Detects groups of wallets buying the same token within the same short window.
    Returns list of clusters.
    """
    update_status("wallet_cluster_analyzer")
    token_time_map = defaultdict(list)

    for wallet, txns in wallet_tx_map.items():
        for tx in txns:
            if tx.get("side") != "buy" or not tx.get("token_address"):
                continue
            try:
                ts = datetime.fromisoformat(tx["timestamp"])
            except Exception:
                continue
            token_time_map[tx["token_address"]].append((wallet, ts))

    new_clusters = []
    for token, activity in token_time_map.items():
        activity.sort(key=lambda x: x[1])
        for i in range(len(activity)):
            cluster = [activity[i][0]]
            t0 = activity[i][1]
            for j in range(i + 1, len(activity)):
                t1 = activity[j][1]
                if (t1 - t0).total_seconds() <= time_window_sec:
                    cluster.append(activity[j][0])
                else:
                    break

            if len(set(cluster)) >= min_overlap:
                unique_cluster = sorted(set(cluster))
                if unique_cluster not in new_clusters:
                    new_clusters.append(unique_cluster)
                    cabal_clusters.append({
                        "token": token,
                        "wallets": unique_cluster,
                        "timestamp": t0.isoformat()
                    })
                    log_event(f"[Cabal] 🧠 New wallet cluster for {token}: {unique_cluster}")
                    # Learned signal
                    for w in unique_cluster:
                        librarian.learn_wallet_tag(w, "cabal_member")
                    librarian.tag_token(token, "wallet_cluster")

    return new_clusters


def record_wallet_buy(token: str, wallet: str):
    now = time.time()
    entries = recent_buys[token]
    entries.append((wallet, now))
    recent_buys[token] = [(w, t) for w, t in entries if now - t <= CLUSTER_WINDOW_SECONDS]

    matched = [w for w, _ in recent_buys[token] if w in TRUSTED_WALLETS]
    if len(matched) >= CLUSTER_MIN_COUNT and token not in cluster_cache:
        cluster_cache.add(token)

        log_ai_insight("wallet_cluster_detected", {
            "token": token,
            "wallets": matched,
            "count": len(matched)
        })

        log_scanner_insight(
            token=token,
            source="wallet_cluster",
            sentiment=0.9,
            volume=len(matched),
            result="wallet_cluster"
        )

        for wallet in matched:
            librarian.learn_wallet_tag(wallet, "trusted_cluster")

        librarian.tag_token(token, "wallet_cluster")

def get_cluster_metadata(token: str = None) -> dict:
    """
    Returns metadata about recent wallet clusters.
    If a token is specified, only returns info related to that token.
    """
    relevant_clusters = [
        cluster for cluster in cabal_clusters
        if token is None or cluster["token"] == token
    ]
    if not relevant_clusters:
        return {}
    latest = sorted(relevant_clusters, key=lambda x: x["timestamp"], reverse=True)[0]
    return {
        "token": latest["token"],
        "wallets": latest["wallets"],
        "cluster_size": len(latest["wallets"]),
        "timestamp": latest["timestamp"]
    }

def get_cluster_score(token: str) -> float:
    return 3.0 if token in cluster_cache else 0.0

def reset_cluster_cache():
    recent_buys.clear()
    cluster_cache.clear()
    logging.info("🔄 Wallet cluster cache cleared.")

# ---------------- Persistent API ----------------
def update_wallet_clusters(*args, **kwargs) -> str:
    with _LOCK:
        _ensure_loaded()
        if not args:
            raise ValueError("update_wallet_clusters: wallet_address required")
        wallet_address: str = _normalize(args[0])
        peers: List[str] = kwargs.pop("peers", []) or []
        tags: List[str] = kwargs.pop("tags", []) or []
        metadata: Dict[str, Any] = kwargs.pop("metadata", {}) or {}

        cluster_id = _get_or_create_cluster_for(wallet_address)
        for peer in [_normalize(p) for p in peers if isinstance(p, str)]:
            other_cluster = _ADDR_TO_CLUSTER.get(peer)
            if not other_cluster:
                _CLUSTERS[cluster_id]["wallets"].append(peer)
                _ADDR_TO_CLUSTER[peer] = cluster_id
            else:
                cluster_id = _merge_clusters(min(cluster_id, other_cluster),
                                             other_cluster if min(cluster_id, other_cluster) == cluster_id else cluster_id)
        for t in tags:
            if t:
                _CLUSTERS[cluster_id]["tags"][t] = _CLUSTERS[cluster_id]["tags"].get(t, 0) + 1
        if metadata:
            _CLUSTERS[cluster_id]["meta"] = {**_CLUSTERS[cluster_id].get("meta", {}), **metadata}
        _CLUSTERS[cluster_id]["updated_at"] = _now()
        _save_state()
        return cluster_id

def log_wallet_action(token: str, wallet: str, action: str, outcome: str = None):
    """Track what wallets are doing per token."""
    entry = {
        "wallet": wallet,
        "action": action,
        "outcome": outcome,
        "time": time.time()
    }
    _wallet_behavior_log.setdefault(token, []).append(entry)


def detect_wallet_traps(token: str) -> bool:
    """
    Flags 'wallet trap' tokens where:
      - Same wallets repeatedly buy/dump new tokens.
      - Contract-linked loops or proxy wallets.
      - Multiple buys by same wallet with rapid exits.
    """
    logs = _wallet_behavior_log.get(token, [])
    if not logs or len(logs) < 4:
        return False

    dumpers = [entry for entry in logs if entry["action"] == "sell"]
    buys = [entry for entry in logs if entry["action"] == "buy"]

    dumpers_by_wallet = {}
    for d in dumpers:
        dumpers_by_wallet.setdefault(d["wallet"], 0)
        dumpers_by_wallet[d["wallet"]] += 1

    high_freq_dumpers = [w for w, count in dumpers_by_wallet.items() if count >= 2]
    looped_buyer_seller = any(
        b["wallet"] in dumpers_by_wallet for b in buys
    )

    # Heuristics for wallet trap pattern
    if len(high_freq_dumpers) >= 2 and looped_buyer_seller:
        return True

    return False


def get_wallet_behavior_snapshot(token: str) -> List[Dict]:
    """Returns raw log of wallet actions for inspection."""
    return _wallet_behavior_log.get(token, [])

def get_persistent_clusters(active_only: bool = False) -> Dict[str, Dict[str, Any]]:
    with _LOCK:
        _ensure_loaded()
        if not active_only:
            return _CLUSTERS.copy()
        return {cid: c for cid, c in _CLUSTERS.items() if c.get("active", True)}

def get_persistent_cluster_metadata(wallet_address: str) -> Dict[str, Any]:
    with _LOCK:
        _ensure_loaded()
        addr = _normalize(wallet_address)
        cluster_id = _ADDR_TO_CLUSTER.get(addr)
        if not cluster_id or cluster_id not in _CLUSTERS:
            return {"cluster_id": None, "wallets": [], "size": 0, "tags": {}, "meta": {}, "active": False}
        c = _CLUSTERS[cluster_id]
        return {
            "cluster_id": cluster_id,
            "wallets": list(c.get("wallets", [])),
            "size": len(c.get("wallets", [])),
            "tags": dict(c.get("tags", {})),
            "meta": dict(c.get("meta", {})),
            "active": bool(c.get("active", True)),
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at"),
        }

def mark_cluster_active(cluster_id: str, active: bool) -> None:
    with _LOCK:
        _ensure_loaded()
        if cluster_id in _CLUSTERS:
            _CLUSTERS[cluster_id]["active"] = bool(active)
            _CLUSTERS[cluster_id]["updated_at"] = _now()
            _save_state()

def cluster_id_for(wallet_address: str) -> Optional[str]:
    with _LOCK:
        _ensure_loaded()
        return _ADDR_TO_CLUSTER.get(_normalize(wallet_address))

def wallets_in_same_cluster(wallet_address: str) -> List[str]:
    with _LOCK:
        _ensure_loaded()
        cid = _ADDR_TO_CLUSTER.get(_normalize(wallet_address))
        return list(_CLUSTERS.get(cid, {}).get("wallets", [])) if cid else []

__all__ = [
    "get_clusters",
    "reset_clusters",
    "analyze_wallet_activity",
    "record_wallet_buy",
    "get_cluster_metadata",
    "get_cluster_score",
    "reset_cluster_cache",
    "update_wallet_clusters",
    "get_persistent_clusters",
    "get_persistent_cluster_metadata",
    "mark_cluster_active",
    "cluster_id_for",
    "wallets_in_same_cluster",
]
