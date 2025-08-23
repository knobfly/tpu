from __future__ import annotations
import json
import os
import threading
import time
from typing import Any, Dict, Iterable, List

_META_PATH = "/home/ubuntu/nyx/runtime/library/meta_keywords.json"
_LOCK = threading.Lock()
_MAX_PER_SCOPE = 5000  # keep it bounded

def _ensure_dir():
    os.makedirs(os.path.dirname(_META_PATH), exist_ok=True)

def _load() -> Dict[str, Any]:
    _ensure_dir()
    if not os.path.exists(_META_PATH):
        return {}
    try:
        with open(_META_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _save(data: Dict[str, Any]) -> None:
    tmp = _META_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"), indent=0)
    os.replace(tmp, _META_PATH)

def _norm(words: Iterable[str]) -> List[str]:
    out = []
    for w in words or []:
        if not w: 
            continue
        w = w.strip().lower()
        if len(w) < 2:  # drop 1-char noise
            continue
        out.append(w)
    return out

def add_keywords(*, scope: str, keywords: Iterable[str], source: str, ref: str | None = None, ts: float | None = None) -> None:
    """
    scope: token mint if known, else token symbol, else a stable channel/source key (e.g. 'tg:<chat_id>')
    keywords: list of strings to persist
    source: 'telegram' | 'x' | 'stream' | etc.
    ref: optional message/signature id
    """
    if not scope:
        return
    kws = _norm(keywords)
    if not kws:
        return

    ts = ts or time.time()
    with _LOCK:
        data = _load()
        bucket = data.setdefault(scope, {"items": [], "counts": {}, "updated_at": 0})
        counts = bucket["counts"]

        # append items + update counts
        for k in kws:
            counts[k] = int(counts.get(k, 0)) + 1
            bucket["items"].append({"k": k, "src": source, "ref": ref, "t": ts})

        # trim oldest if too big
        if len(bucket["items"]) > _MAX_PER_SCOPE:
            drop = len(bucket["items"]) - _MAX_PER_SCOPE
            # reduce counts accordingly
            for i in range(drop):
                it = bucket["items"][i]
                kk = it.get("k")
                if kk in counts:
                    counts[kk] -= 1
                    if counts[kk] <= 0:
                        counts.pop(kk, None)
            bucket["items"] = bucket["items"][drop:]

        bucket["updated_at"] = ts
        data[scope] = bucket
        _save(data)

def top_keywords(scope: str, n: int = 50) -> List[tuple[str, int]]:
    data = _load()
    counts = data.get(scope, {}).get("counts", {})
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:n]
