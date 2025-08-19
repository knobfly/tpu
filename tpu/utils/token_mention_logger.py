# utils/token_mention_logger.py
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

MENTION_LOG = os.path.expanduser("/home/ubuntu/nyx/runtime/data/tg_token_mentions.jsonl")

def _append_jsonl(path: str, rec: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

def log_token_mention(
    *,
    group: str,
    message: str,
    symbol: Optional[str] = None,
    mint: Optional[str] = None,
    status: str = "observed",      # "observed" | "no_mint" | "invalid" | "blacklisted" | "scored_low" | "queued" | "bought"
    reason: Optional[str] = None,
    score: Optional[float] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    rec = {
        "ts": datetime.utcnow().isoformat(),
        "group": group or "",
        "symbol": symbol or "",
        "mint": mint or "",
        "status": status,
        "reason": reason or "",
        "score": score,
        "message": message or "",
        **(extra or {}),
    }
    _append_jsonl(MENTION_LOG, rec)
