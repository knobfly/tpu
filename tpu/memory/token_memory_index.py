# memory/token_memory_index.py
from __future__ import annotations

import json
import logging
import os
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# Optional helpers (use when available)
try:
    from memory.shared_runtime import shared_memory  # simple in-proc kv
except Exception:
    shared_memory = {}

try:
    from utils.time_utils import now_ts
except Exception:
    now_ts = lambda: datetime.utcnow().isoformat()

try:
    from utils.file_utils import safe_load_json, safe_save_json, safe_write_json
except Exception:
    # Fallbacks if utils.file_utils isn't present
    def safe_write_json(path: str, data: dict) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def safe_load_json(path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def safe_save_json(path: str, data: dict) -> None:
        safe_write_json(path, data)

# -------------------------
# Canonical paths (fixed)
# -------------------------
TOKEN_MEM_FILE = os.path.abspath(
    os.path.expanduser("/home/ubuntu/nyx/runtime/data/token/token_memory_index.json")
)
TOKEN_HISTORY_PATH = os.path.abspath(
    os.path.expanduser("/home/ubuntu/nyx/runtime/data/token/token_history.json")
)
MEMORY_DIR = os.path.abspath(
    os.path.expanduser("/home/ubuntu/nyx/runtime/memory/token_social")
)

for _dir in (os.path.dirname(TOKEN_MEM_FILE), os.path.dirname(TOKEN_HISTORY_PATH), MEMORY_DIR):
    os.makedirs(_dir, exist_ok=True)

# Limits
MAX_TOKEN_ENTRIES = 1000
MAX_TXN_HISTORY = 100             # per token
_SNIPER_MAX = 2000

# In-memory adjuncts
_chart_memory: Dict[str, Dict[str, Any]] = {}
_sniper_patterns: deque = deque(maxlen=_SNIPER_MAX)
score_memory_store: Dict[str, Dict[str, Any]] = {}
TOKEN_META_MEMORY: Dict[str, Dict[str, Any]] = {}
_token_pool_params: Dict[str, Dict[str, Any]] = {}
_token_pool_snapshots: Dict[str, Dict[str, Any]] = {}


# ============================================================
#                    TokenMemoryIndex (KV)
# ============================================================
class TokenMemoryIndex:
    """
    Persistent per-token memory. Two APIs:
      - record_outcome(token, result, category, profit)  # legacy: win/loss/profit
      - record_kv(token, key, value)                     # generic kv
    """
    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._loaded = False
        self._ensure_dir()
        self.load()
        self.index = self._data

    def _ensure_dir(self) -> None:
        os.makedirs(os.path.dirname(TOKEN_MEM_FILE), exist_ok=True)

    def load(self) -> None:
        if self._loaded:
            return
        try:
            if os.path.exists(TOKEN_MEM_FILE):
                with open(TOKEN_MEM_FILE, "r", encoding="utf-8") as f:
                    self._data = json.load(f) or {}
            else:
                self._data = {}
            self._loaded = True
        except Exception as e:
            logging.warning(f"[TokenMemoryIndex] load failed: {e}")
            self._data, self._loaded = {}, True

    @property
    def data(self):
        return self._data

    def save(self) -> None:
        try:
            self._ensure_dir()
            with open(TOKEN_MEM_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception as e:
            logging.error(f"[TokenMemoryIndex] save failed: {e}")

    # ---- legacy outcome API ----
    def record_outcome(self, token: str, result: str, category: str = "unknown", profit: float = 0.0) -> None:
        """
        result ∈ {"win","loss"}; accumulates wins/losses/profit per token.
        """
        self.load()
        bucket = self._data.setdefault(token, {"wins": 0, "losses": 0, "profit": 0.0, "category": category})
        if result == "win":
            bucket["wins"] = int(bucket.get("wins", 0)) + 1
        elif result == "loss":
            bucket["losses"] = int(bucket.get("losses", 0)) + 1
        bucket["profit"] = float(bucket.get("profit", 0.0)) + float(profit or 0.0)
        if "category" not in bucket or bucket["category"] == "unknown":
            bucket["category"] = category
        self.save()

    # Back-compat name used in your crash trace:
    def record(self, token: str, key: str, value: Any) -> None:
        """Generic KV setter (was: self.save() after setting)."""
        self.record_kv(token, key, value)

    # ---- generic KV API ----
    def record_kv(self, token: str, key: str, value: Any) -> None:
        self.load()
        bucket = self._data.setdefault(token, {})
        bucket[key] = value
        self.save()

    def get_stats(self, token: str) -> Dict[str, Any]:
        self.load()
        return self._data.get(token, {}).copy()

    # Optional TTL pruning hook if you store time-based lists inside _data
    def prune_old_entries(self, hours: int = 6) -> None:
        """
        If you store arrays with 'timestamp' fields inside _data[token][...],
        this will drop entries older than X hours. Safe no-op if absent.
        """
        try:
            cutoff = datetime.utcnow().timestamp() - (hours * 3600)
            for token, bucket in list(self._data.items()):
                if not isinstance(bucket, dict):
                    continue
                for k, v in list(bucket.items()):
                    if isinstance(v, list):
                        bucket[k] = [e for e in v if isinstance(e, dict) and e.get("timestamp", cutoff) >= cutoff]
            self.save()
        except Exception as e:
            logging.warning(f"[TokenMemoryIndex] prune_old_entries failed: {e}")


# ============================================================
#                    Metadata & Chart helpers
# ============================================================
def load_all_token_metadata() -> dict:
    path = os.path.abspath(os.path.expanduser("~/nyx/runtime/data/token_metadata.json"))
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def update_chart_memory(token: str, pattern: str, score: float, meta: Optional[dict] = None) -> None:
    """
    Keeps EXACT NAME for chart_cortex compatibility.
    """
    try:
        _chart_memory[token] = {
            "pattern": pattern,
            "score": round(float(score), 4),
            "meta": meta or {},
            "updated": datetime.utcnow().isoformat(),
        }
        logging.info(f"[ChartMemory] {token}: {pattern} | score={score:.2f}")
    except Exception as e:
        logging.warning(f"[ChartMemory] Failed update for {token}: {e}")

def get_chart_memory(token: str) -> dict:
    return _chart_memory.get(token, {}).copy()


# ============================================================
#                    Sniper pattern memory
# ============================================================
def record_sniper_pattern(token: str, pattern: str, score: float, meta: Optional[dict] = None, ts: Optional[str] = None) -> None:
    try:
        _sniper_patterns.append({
            "token": token,
            "pattern": pattern,
            "score": float(score),
            "meta": meta or {},
            "timestamp": ts or datetime.utcnow().isoformat(),
        })
    except Exception as e:
        logging.warning(f"[TokenMemory] record_sniper_pattern failed for {token}: {e}")

def get_recent_sniper_patterns(since_minutes: int = 60, limit: int = 50) -> List[dict]:
    """
    EXACT NAME expected by ai_sniper_intuition.py.
    """
    try:
        cutoff = datetime.utcnow() - timedelta(minutes=since_minutes)
        out: List[dict] = []
        for item in reversed(_sniper_patterns):
            try:
                if datetime.fromisoformat(item["timestamp"]) >= cutoff:
                    out.append(item)
                    if len(out) >= limit:
                        break
            except Exception:
                continue
        return list(reversed(out))
    except Exception as e:
        logging.warning(f"[TokenMemory] get_recent_sniper_patterns failed: {e}")
        return []


# ============================================================
#                    Txn memory (rolling)
# ============================================================
def update_token_txn_memory(token: str, tx_data: dict) -> None:
    """
    Adds a new transaction record to the shared memory for this token.
    Ensures a rolling window of MAX_TXN_HISTORY entries.
    """
    try:
        if not token or not isinstance(tx_data, dict):
            return
        token = token.lower()
        tx = dict(tx_data)
        tx["timestamp"] = tx.get("timestamp", now_ts())
        mem_key = f"token_txns:{token}"
        txns: List[dict] = list(shared_memory.get(mem_key, []))
        txns.append(tx)
        if len(txns) > MAX_TXN_HISTORY:
            txns = txns[-MAX_TXN_HISTORY:]
        shared_memory[mem_key] = txns
    except Exception as e:
        logging.warning(f"[TokenMemoryIndex] Failed to update txn memory for {token}: {e}")

def get_recent_token_txns(token: str, limit: int = 25) -> List[dict]:
    try:
        token = token.lower()
        mem_key = f"token_txns:{token}"
        txns: List[dict] = list(shared_memory.get(mem_key, []))
        return txns[-limit:]
    except Exception as e:
        logging.warning(f"[TokenMemoryIndex] Failed to fetch txns for {token}: {e}")
        return []


# ============================================================
#                    Score & Meta memory
# ============================================================
def get_score_memory(token_address: str, mode: Optional[str] = None) -> Any:
    memory = score_memory_store.get(token_address, {})
    return memory.get(mode) if mode else memory

def update_score_memory(token_address: str, mode: str, data: dict) -> None:
    """
    Updates memory store for a given token and mode ('snipe' or 'trade').
    """
    store = score_memory_store.setdefault(token_address, {})
    store[mode] = data

def update_token_meta_memory(token: str, meta: Dict[str, Any]) -> None:
    try:
        bucket = TOKEN_META_MEMORY.setdefault(token, {})
        bucket.update(meta or {})
        logging.debug(f"[TokenMemory] Updated meta for {token}: {meta}")
    except Exception as e:
        logging.warning(f"[TokenMemory] Failed to update meta memory for {token}: {e}")


# ============================================================
#                    Social memory (per token)
# ============================================================
def _get_memory_path(token: str) -> str:
    return os.path.join(MEMORY_DIR, f"{token}.json")

def update_token_social_memory(token: str, source: str, score: int = 1, timestamp: Optional[str] = None) -> None:
    """
    Updates the social memory index for a token by source (e.g., telegram, x, influencer).
    Accumulates scores per source, appends timestamps.
    """
    path = _get_memory_path(token)
    now = timestamp or datetime.utcnow().isoformat()

    memory: dict
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                memory = json.load(f)
        except Exception:
            memory = {}
    else:
        memory = {}

    if not memory:
        memory = {"token": token, "created": now, "sources": {}, "timestamps": []}

    # Track signal by source
    sources = memory.setdefault("sources", {})
    sources[source] = int(sources.get(source, 0)) + int(score or 0)

    # Track signal timestamps (cap)
    stamps = memory.setdefault("timestamps", [])
    stamps.append(now)
    if len(stamps) > 500:
        memory["timestamps"] = stamps[-500:]

    safe_write_json(path, memory)


# ============================================================
#                    Trimming / housekeeping
# ============================================================
def trim_token_memory() -> Dict[str, int]:
    """
    Trims the token history file to MAX_TOKEN_ENTRIES entries.
    Returns stats about the trim process.
    """
    if not os.path.exists(TOKEN_HISTORY_PATH):
        return {"trimmed": 0, "kept": 0}

    try:
        data = safe_load_json(TOKEN_HISTORY_PATH)
        if not isinstance(data, dict):
            logging.warning("[TokenMemoryTrim] Unexpected format in token_history.json")
            return {"trimmed": 0, "kept": 0}

        total = len(data)
        if total <= MAX_TOKEN_ENTRIES:
            return {"trimmed": 0, "kept": total}

        trimmed_data = dict(list(data.items())[-MAX_TOKEN_ENTRIES:])
        safe_save_json(TOKEN_HISTORY_PATH, trimmed_data)
        logging.info(f"[TokenMemoryTrim] Trimmed token memory from {total} to {MAX_TOKEN_ENTRIES} entries.")
        return {"trimmed": total - MAX_TOKEN_ENTRIES, "kept": MAX_TOKEN_ENTRIES}

    except Exception as e:
        logging.error(f"[TokenMemoryTrim] Failed to trim token memory: {e}")
        return {"trimmed": 0, "kept": 0}


# ============================================================
#                    Pool params / snapshots
# ============================================================
def update_pool_params(token: str, params: dict) -> None:
    if not token:
        return
    cur = _token_pool_params.get(token, {})
    cur.update(params or {})
    _token_pool_params[token] = cur

def get_pool_params(token: str) -> dict:
    return _token_pool_params.get(token, {}).copy()

def update_pool_snapshot(token: str, snap: dict) -> None:
    if not token:
        return
    _token_pool_snapshots[token] = {**(snap or {})}

def get_pool_snapshot(token: str) -> dict:
    return _token_pool_snapshots.get(token, {}).copy()


# ============================================================
#                    Singleton
# ============================================================
token_memory_index = TokenMemoryIndex()
