# librarian_utils.py
import gzip
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


def get_token_summary(token_memory: Dict[str, Any], token: str) -> Dict[str, Any]:
    """
    Return a lightweight summary of a token’s known memory profile.
    Includes tags, flags, score, and high-level traits for dashboards or quick checks.
    """
    token_data = token_memory.get(token, {})
    if not token_data:
        return {
            "token": token, 
            "score": 0, 
            "tags": [], 
            "flags": [], 
            "meta_theme": None, 
            "created": None 
        }
    return {
        "token": token,
        "score": token_data.get("score", 0),
        "tags": list(token_data.get("tags", [])),
        "flags": list(token_data.get("flags", [])),
        "meta_theme": token_data.get("meta_theme", None),
        "created": token_data.get("created", None)
    }

def get_meta_keywords(memory_store: Dict[str, Any], limit: int = 20) -> List[str]:
    """
    Safely returns top keywords by count from memory.
    """
    raw = memory_store.get("keyword_memory", {})
    if not isinstance(raw, dict):
        return []
    sorted_keywords = []
    for key, val in raw.items():
        if isinstance(val, dict) and "count" in val:
            sorted_keywords.append((key, val["count"]))
        elif isinstance(val, int):
            sorted_keywords.append((key, val))
    sorted_keywords.sort(key=lambda x: x[1], reverse=True)
    return [k for k, _ in sorted_keywords[:limit]]

def persist_all(
    token_memory: Dict[str, Any],
    wallet_memory: Dict[str, Any],
    strategy_memory: Dict[str, Any],
    save_token_memory: Any,
    save_wallet_memory: Any,
    save_strategy_memory: Any,
    log_event: Any,
    logging: Any
) -> None:
    """
    Flush all in-memory data to disk (if needed).
    Extend this to persist caches, memory, or logs.
    """
    try:
        if token_memory:
            save_token_memory()
        if wallet_memory:
            save_wallet_memory()
        if strategy_memory:
            save_strategy_memory()
        log_event("[Librarian] 🧠 All memory persisted successfully.")
    except Exception as e:
        logging.warning(f"[Librarian] Failed to persist memory: {e}")

def find_token(payload: Dict[str, Any]) -> Optional[str]:
    for key in ("token", "token_address", "mint", "address"):
        v = payload.get(key)
        if isinstance(v, str) and len(v) > 20:
            return v
    return None

def find_wallet(payload: Dict[str, Any]) -> Optional[str]:
    for key in ("wallet", "wallet_address", "owner", "from", "to"):
        v = payload.get(key)
        if isinstance(v, str) and len(v) > 20:
            return v
    return None

def safe_read_json_dict(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, str):
            try:
                data2 = json.loads(data)
                if isinstance(data2, dict):
                    return data2
            except Exception:
                return {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def safe_iter_jsonl(pathlike: str | Path):
    p = Path(pathlike)
    if not p.exists() or p.is_dir():
        return
    try:
        if str(p).endswith(".gz"):
            with gzip.open(p, "rt", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except Exception:
                        continue
        else:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except Exception:
                        continue
    except Exception:
        return

def iter_glob_jsonl(glob_pattern: str):
    if not glob_pattern:
        return
    for p in Path().glob(glob_pattern):
        try:
            for x in safe_iter_jsonl(p):
                yield x
        except Exception:
            continue

def find_token(payload: Dict[str, Any]) -> Optional[str]:
    for key in ("token", "token_address", "mint", "address"):
        v = payload.get(key)
        if isinstance(v, str) and len(v) > 20:
            return v
    return None

def find_wallet(payload: Dict[str, Any]) -> Optional[str]:
    for key in ("wallet", "wallet_address", "owner", "from", "to"):
        v = payload.get(key)
        if isinstance(v, str) and len(v) > 20:
            return v
    return None
