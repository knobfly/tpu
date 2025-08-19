# core/token_ledger.py
from __future__ import annotations
import os, json, threading, time
from typing import Dict, Any, Optional, List
from datetime import datetime

_LEDGER_PATH = os.path.expanduser("~/nyx/runtime/data/token_ledger.json")
os.makedirs(os.path.dirname(_LEDGER_PATH), exist_ok=True)
_lock = threading.RLock()
_state: Dict[str, Dict[str, Any]] = {}   # mint -> state blob

def _now() -> str:
    return datetime.utcnow().isoformat()

def _load():
    global _state
    try:
        if os.path.exists(_LEDGER_PATH):
            with open(_LEDGER_PATH, "r", encoding="utf-8") as f:
                _state = json.load(f) or {}
    except Exception:
        _state = {}

def _save():
    tmp = _LEDGER_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_state, f, indent=2)
    os.replace(tmp, _LEDGER_PATH)

_load()

def upsert_event(
    mint: str,
    *,
    source: str,
    payload: Dict[str, Any],
    ts_iso: Optional[str] = None
) -> None:
    """
    Merge normalized event data into the token record and append timeline.
    Caller guarantees `mint` is the canonical key for this token.
    """
    if not mint:
        return
    with _lock:
        rec = _state.setdefault(mint, {
            "mint": mint,
            "symbol": None,
            "first_seen": _now(),
            "last_seen": None,
            "meta": {},
            "social": {},
            "chart": {},
            "txn": {},
            "risk": {},
            "scores": {},        # sub-scores from cortices
            "final": {},         # final aggregate decision
            "timeline": []       # append-only events
        })

        # generic enrichment
        rec["last_seen"] = ts_iso or _now()
        # keep best-known symbol if provided
        sym = payload.get("symbol")
        if sym and not rec.get("symbol"):
            rec["symbol"] = sym

        # allow each source to feed its bucket (minimal convention)
        bucket = payload.pop("_bucket", None)  # e.g. "meta"|"social"|"txn"...
        if bucket and isinstance(rec.get(bucket), dict):
            rec[bucket].update(payload)
        else:
            # if caller didn’t specify, just merge into top-level
            for k, v in payload.items():
                if isinstance(v, dict) and isinstance(rec.get(k), dict):
                    rec[k].update(v)
                else:
                    rec[k] = v

        rec["timeline"].append({
            "ts": ts_iso or _now(),
            "source": source,
            "data": payload
        })
        # trim timeline a bit
        if len(rec["timeline"]) > 500:
            rec["timeline"] = rec["timeline"][-500:]

        _save()

def set_scores(mint: str, *, sub_scores: Dict[str, float], final: Dict[str, Any]) -> None:
    with _lock:
        rec = _state.setdefault(mint, {"mint": mint})
        rec["scores"] = sub_scores
        rec["final"] = final
        rec["last_scored_at"] = _now()
        _save()

def get_context(mint: str) -> Dict[str, Any]:
    with _lock:
        return json.loads(json.dumps(_state.get(mint, {})))  # deep copy

def all_tokens() -> List[str]:
    with _lock:
        return list(_state.keys())
