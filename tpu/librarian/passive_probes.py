# librarian/passive_probes.py

import logging
from datetime import datetime
from typing import Any, Dict, Iterable

from memory.token_memory_index import token_memory_index
from scoring.snipe_score_engine import evaluate_snipe
from strategy.strategy_memory import tag_token_result


def _coerce_mapping() -> Dict[str, Any]:
    """
    Return a mapping of token -> entry, regardless of which storage
    the TokenMemoryIndex currently uses.
    """
    # Newer structure
    d = getattr(token_memory_index, "_data", None)
    if isinstance(d, dict):
        return d
    # Older structure (had .index)
    d = getattr(token_memory_index, "index", None)
    if isinstance(d, dict):
        return d
    # Fallback: nothing usable
    return {}


def _latest_ts_from_entry(entry: Any) -> float:
    """
    Try to extract a 'latest' UNIX timestamp from a token's entry.
    Supports multiple shapes:
      - list[dict]: look for max(e['timestamp'])
      - dict with common time fields: last_seen/last_update/timestamp/ts
      - dict with 'timestamps': take the max
      - otherwise 0
    """
    def _parse_iso(s: str) -> float:
        try:
            return datetime.fromisoformat(s).timestamp()
        except Exception:
            return 0.0

    # list of dicts (legacy “entries”)
    if isinstance(entry, list):
        ts_vals = []
        for e in entry:
            if isinstance(e, dict):
                v = e.get("timestamp")
                if isinstance(v, (int, float)):
                    ts_vals.append(float(v))
                elif isinstance(v, str):
                    ts_vals.append(_parse_iso(v))
        return max(ts_vals) if ts_vals else 0.0

    # dict-based
    if isinstance(entry, dict):
        # direct numeric or iso strings
        for key in ("last_seen", "last_update", "timestamp", "ts"):
            v = entry.get(key)
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                val = _parse_iso(v)
                if val:
                    return val

        # array of timestamps
        if isinstance(entry.get("timestamps"), Iterable):
            ts_vals = []
            for v in entry["timestamps"]:
                if isinstance(v, (int, float)):
                    ts_vals.append(float(v))
                elif isinstance(v, str):
                    ts_vals.append(_parse_iso(v))
            return max(ts_vals) if ts_vals else 0.0

    return 0.0


async def probe_idle_tokens(hours: int = 12):
    """
    Passively probe tokens not updated recently and re-tag them if worthy.
    Compatible with both old (.index list-of-dicts) and new (_data dict) stores.
    """
    try:
        cutoff = datetime.utcnow().timestamp() - (hours * 3600)
        mapping = _coerce_mapping()

        idle_tokens = []
        for token, entry in mapping.items():
            latest_ts = _latest_ts_from_entry(entry)
            if latest_ts and latest_ts < cutoff:
                idle_tokens.append(token)

        if idle_tokens:
            logging.info(f"[Librarian] Probing {len(idle_tokens)} idle tokens...")

        for token in idle_tokens:
            try:
                score_result = await evaluate_snipe(token)
                final_score = float(score_result.get("final_score", 0.0))

                if final_score >= 0.75:
                    tag_token_result(token, "idle_alpha")
                    # Persist result into the new key/value store
                    token_memory_index.record(token, "idle_probe_score", final_score)
                    logging.info(f"[Librarian] 🧠 Re-tagged idle token {token} (score={final_score:.3f})")
            except Exception as e:
                logging.warning(f"[Librarian] Error re-evaluating idle token {token}: {e}")

    except Exception as e:
        logging.warning(f"[Librarian] probe_idle_tokens failed: {e}")
