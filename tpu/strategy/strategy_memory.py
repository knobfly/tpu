# strategy/strategy_memory.py
from __future__ import annotations
import os, json, logging, time, re
from typing import Dict, List, Any, Optional, Union
from datetime import datetime

# --- Paths -------------------------------------------------------------------
MEMORY_FILE   = "/home/ubuntu/nyx/runtime/memory/strategy/strategy_memory.json"
TAG_FILE      = "/home/ubuntu/nyx/runtime/memory/strategy/strategy_tags.json"
SNAPSHOT_DIR  = "/home/ubuntu/nyx/runtime/memory/strategy/snapshots"
META_KEYWORD_FILE = "/home/ubuntu/nyx/runtime/logs/meta_keywords.json"

for p in [
    os.path.dirname(MEMORY_FILE),
    os.path.dirname(TAG_FILE),
    SNAPSHOT_DIR,
    os.path.dirname(META_KEYWORD_FILE),
]:
    os.makedirs(p, exist_ok=True)

# --- Strategy config ---------------------------------------------------------
STRATEGIES   = ["balanced", "passive", "aggro", "scalper", "meta_trend"]
DECAY_FACTOR = 0.94
MIN_THRESHOLD = 0.015

TAG_ALIASES = {
    "highscore": ["high-score", "high_score", "🔥high-score", "🔥 high-score"],
    "lowscore":  ["low-score", "low", "⚠️ low-score", "⚠️low-score"],
    "risky":     ["very-risky", "☠️ risky", "☠️very-risky", "rug", "ruggable"],
}

# --- In-memory state ---------------------------------------------------------
_strategy_results: Dict[str, List[float]] = {s: [] for s in STRATEGIES}
_overlap_triggers: Dict[str, Dict[str, Any]] = {}
_reverse_learning_log: List[Dict[str, Any]] = []
_memory: Dict[str, Any] = {"tokens": {}, "tags": {}}   # tokens -> per-token rolling signals, tags -> token tags
_strategy_adjustments: List[Dict[str, Any]] = []

# === Librarian hook (late import to avoid cycles) ============================
try:
    from librarian.data_librarian import librarian
except Exception:
    librarian = None

# =============================================================================
#                               TAGGING
# =============================================================================

def get_tagged_tokens() -> dict:
    return _memory.get("tags", {})

def get_tagged_tokens_report() -> str:
    tags = _memory.get("tags", {})
    return "\n".join([f"{t}: {', '.join(tags[t])}" for t in tags])

def normalize_tag(raw_tag: str) -> str:
    tag = re.sub(r"[^\w\s-]", "", (raw_tag or "").lower()).strip()
    for canonical, aliases in TAG_ALIASES.items():
        if tag == canonical or tag in [a.lower() for a in aliases]:
            return canonical
    return tag

def dedupe_tags(tags: list) -> list:
    seen, out = set(), []
    for tag in tags:
        n = normalize_tag(tag)
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out

def tag_token_result(token: str, tag: str, score: float = 0.0):
    tag = normalize_tag(tag)
    tags = _memory.setdefault("tags", {})
    entry = tags.setdefault(token, [])
    if tag not in entry:
        entry.append(tag)
    _memory.setdefault("tokens", {}).setdefault(token, {})["score"] = float(score)
    _persist_runtime_state()

def get_token_tag(token_address: str) -> str:
    try:
        if os.path.exists(TAG_FILE):
            with open(TAG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            return data.get(token_address, "unknown")
    except Exception:
        pass
    return "unknown"

def get_strategy_tags_for_wallet(wallet_address: str):
    """
    Return a list of strategy tags associated with the given wallet address.
    If no tags are found, return an empty list.
    """
    try:
        tags = STRATEGY_TAGS.get(wallet_address.lower())
        return tags if tags else []
    except NameError:
        # STRATEGY_TAGS not defined or structure missing
        return []

def is_blacklisted_token(token_address: str) -> bool:
    tag = get_token_tag(token_address)
    return tag in {"rug", "dead", "scam"}

# =============================================================================
#                           STRATEGY SIGNALS
# =============================================================================

def get_strategy_score(strategy: str) -> float:
    scores = _strategy_results.get(strategy, [])
    return (sum(scores) / len(scores)) if scores else 0.0

def log_strategy_result(strategy: str, score: float):
    bucket = _strategy_results.setdefault(strategy, [])
    bucket.append(float(score))
    if len(bucket) > 100:
        _strategy_results[strategy] = bucket[-100:]

def calculate_total_win_rate() -> float:
    wins = total = 0
    for bucket in _strategy_results.values():
        for s in bucket:
            wins += (1 if s > 0 else 0)
            total += 1
    return wins / total if total else 0.0

def update_strategy_signal(token: str, score: float, tuner: str = "trade"):
    """
    Rolling per-token signal (used by self-tuner / executor).
    """
    tok = _memory.setdefault("tokens", {}).setdefault(token, {
        "scores": [],
        "last_update": time.time(),
        "tuner": tuner,
        "last_trade_time": 0,
        "average_score": 0.0,
    })
    tok["scores"].append(float(score))
    if len(tok["scores"]) > 200:
        tok["scores"] = tok["scores"][-200:]
    tok["last_update"] = time.time()
    tok["tuner"] = tuner
    # maintain avg
    s = tok["scores"]
    tok["average_score"] = (sum(s) / len(s)) if s else 0.0
    _persist_runtime_state()

def get_highest_scoring_idle_token(strategy_data: dict, min_age_minutes: int = 15) -> Optional[str]:
    best_token, best_score = None, -1.0
    for token, data in (strategy_data or {}).items():
        avg = float(data.get("average_score", 0.0))
        last_trade = float(data.get("last_trade_time", 0.0))
        age_minutes = (time.time() - last_trade) / 60 if last_trade else 9e9
        if age_minutes >= min_age_minutes and avg > best_score:
            best_token, best_score = token, avg
    return best_token

def record_strategy_adjustment(strategy: str, reason: str, delta: float, meta: dict = None):
    try:
        entry = {
            "strategy": strategy,
            "reason": reason,
            "delta": float(delta),
            "meta": meta or {},
            "timestamp": datetime.utcnow().isoformat()
        }
        _strategy_adjustments.append(entry)
        logging.info(f"[StrategyMemory] {strategy} adjusted by {delta:.2f} ({reason})")
    except Exception as e:
        logging.warning(f"[StrategyMemory] Failed record_strategy_adjustment for {strategy}: {e}")

def get_strategy_adjustments(limit: int = 50):
    return _strategy_adjustments[-limit:]

def update_strategy_performance(strategy: str, win: bool = None, rug: bool = False):
    """
    Legacy: increments W/L/R counters in the persistent strategy memory file.
    """
    try:
        outcome = "win" if win else ("rug" if rug else "loss")
        record_result(strategy, outcome)
        logging.info(f"[StrategyMemory] Performance updated: {strategy} → {outcome}")
    except Exception as e:
        logging.warning(f"[StrategyMemory] Failed to update performance: {e}")

def reset_strategy_stats():
    try:
        global _strategy_results
        _strategy_results = {s: [] for s in STRATEGIES}
        logging.info("[StrategyMemory] Strategy stats reset.")
    except Exception as e:
        logging.warning(f"[StrategyMemory] Failed to reset strategy stats: {e}")

def get_strategy_performance() -> Dict[str, float]:
    try:
        return {s: get_strategy_score(s) for s in STRATEGIES}
    except Exception as e:
        logging.warning(f"[StrategyMemory] Failed to fetch performance: {e}")
        return {}

def get_recent_performance(window_min: int = 60):
    try:
        from strategy.recent_result_tracker import performance_window_summary
        stats = performance_window_summary(window_min)
        return {
            "win_rate": float(stats.get("win_rate", 0.0)),
            "pnl": float(stats.get("pnl", 0.0)),
            "streak": int(stats.get("streak", 0)),
        }
    except Exception:
        return {"win_rate": 0.0, "pnl": 0.0, "streak": 0}

def update_strategy_feedback(strategy: str, feedback: dict):
    try:
        score = float(feedback.get("final_score", 0.0))
        log_strategy_result(strategy, score)
        kws = feedback.get("keywords")
        if kws:
            update_meta_keywords(keywords=kws, source=f"feedback:{strategy}")
        logging.info(f"[StrategyMemory] Feedback recorded: {strategy} → {score}")
    except Exception as e:
        logging.warning(f"[StrategyMemory] Failed to record feedback: {e}")

# =============================================================================
#                     OVERLAP + REVERSE LEARNING
# =============================================================================

def register_overlap_trigger(token: str, reason: str):
    _overlap_triggers[token] = {"reason": reason, "ts": time.time()}

def get_recent_overlap_triggers(limit: int = 5) -> list:
    sorted_triggers = sorted(_overlap_triggers.items(), key=lambda x: x[1]["ts"], reverse=True)
    return [f"{k} ({v['reason']})" for k, v in sorted_triggers[:limit]]

def log_reverse_learning(token: str, feedback: str):
    _reverse_learning_log.append({"token": token, "feedback": feedback, "ts": time.time()})
    if len(_reverse_learning_log) > 50:
        _reverse_learning_log.pop(0)

# =============================================================================
#                        PERSISTENT STRATEGY RECORD
# =============================================================================

def _load_memory_file() -> Dict[str, dict]:
    if not os.path.exists(MEMORY_FILE):
        return {s: {"wins": 0, "losses": 0, "rugs": 0} for s in STRATEGIES}
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        # ensure all strategies exist
        for s in STRATEGIES:
            data.setdefault(s, {"wins": 0, "losses": 0, "rugs": 0})
        return data
    except Exception as e:
        logging.warning(f"Failed to load memory: {e}")
        return {s: {"wins": 0, "losses": 0, "rugs": 0} for s in STRATEGIES}

def _save_memory_file(data: Dict[str, dict]):
    try:
        tmp = MEMORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, MEMORY_FILE)
        logging.info("✅ Strategy memory updated.")
    except Exception as e:
        logging.error(f"❌ Failed to save memory: {e}")

def record_result(strategy: str, outcome: str):
    data = _load_memory_file()
    if strategy not in data:
        data[strategy] = {"wins": 0, "losses": 0, "rugs": 0}
    if outcome == "win":
        data[strategy]["wins"] += 1
    elif outcome == "loss":
        data[strategy]["losses"] += 1
    elif outcome == "rug":
        data[strategy]["rugs"] += 1
    else:
        logging.warning(f"Unknown outcome: {outcome}")
    logging.info(f"🧠 Recorded: {strategy} → {outcome}")
    _save_memory_file(data)

def get_strategy_report() -> str:
    data = _load_memory_file()
    return "\n".join([f"{k}: W={v['wins']} | L={v['losses']} | R={v['rugs']}" for k, v in data.items()]) or "No strategy data yet."

def audit_strategy_memory():
    data = _load_memory_file()
    if not data or not isinstance(data, dict):
        return "⚠️ No strategy memory found."
    lines = [f"{strategy}: W={stats.get('wins', 0)} | L={stats.get('losses', 0)} | R={stats.get('rugs', 0)}"
             for strategy, stats in data.items()]
    return "\n".join(lines)

# =============================================================================
#                      RUNTIME SNAPSHOTS (STRATEGY DATA)
# =============================================================================

def save_strategy_snapshot(strategy_data: dict, label: str = None):
    try:
        ts = int(time.time())
        filename = f"{label or 'snapshot'}_{ts}.json"
        path = os.path.join(SNAPSHOT_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(strategy_data, f, indent=2)
        logging.info(f"[StrategySnapshot] ✅ Saved snapshot to {path}")
    except Exception as e:
        logging.warning(f"[StrategySnapshot] ❌ Failed to save snapshot: {e}")

# =============================================================================
#                       META KEYWORDS (Unified Ledger)
# =============================================================================

# in-memory mirrors for meta keywords
_meta_keywords: Dict[str, Any] = {}      # global ledger keyed by keyword
token_meta_keywords: Dict[str, set] = {} # token -> set(keywords)

_GOOD_HINTS = {"pump", "moon", "ath", "bull", "meta", "trending", "volume", "liquidity", "community", "alpha"}
_BAD_HINTS  = {"rug", "scam", "honeypot", "dump", "rekt", "warning", "blacklist", "dead"}

def _now_iso() -> str:
    return datetime.utcnow().isoformat()

def _ensure_kw_store() -> dict:
    if not os.path.exists(META_KEYWORD_FILE):
        with open(META_KEYWORD_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)
    try:
        with open(META_KEYWORD_FILE, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
            return d if isinstance(d, dict) else {}
    except Exception as e:
        logging.warning(f"[MetaKeywords] load failed: {e}")
        return {}

def _save_kw_store(data: dict) -> None:
    try:
        tmp = META_KEYWORD_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, META_KEYWORD_FILE)
    except Exception as e:
        logging.warning(f"[MetaKeywords] save failed: {e}")

def load_meta_keywords() -> dict:
    global _meta_keywords, token_meta_keywords
    data = _ensure_kw_store()
    _meta_keywords = data.get("global", {})
    token_meta_keywords = {k: set(v) for k, v in data.get("meta_keywords", {}).items()}
    return data

def _dump_meta() -> dict:
    return {
        "global": _meta_keywords,
        "meta_keywords": {k: list(v) for k, v in token_meta_keywords.items()},
        "last_update": _now_iso(),
        "version": 1,
    }

def _normalize_keywords(kw: Union[str, List[str], None]) -> List[str]:
    if kw is None:
        return []
    if isinstance(kw, str):
        parts = [p.strip() for p in kw.replace(",", " ").split() if p.strip()]
    else:
        parts = [str(x).strip() for x in kw if str(x).strip()]
    out = [p.lower() for p in parts]
    # dedupe keep-order
    seen, deduped = set(), []
    for w in out:
        if w not in seen:
            seen.add(w)
            deduped.append(w)
    return deduped

def _bucket_for_keyword(k: str, sentiment: Optional[float]) -> str:
    if isinstance(sentiment, (int, float)):
        if sentiment >= 0.25:
            return "good"
        if sentiment <= -0.25:
            return "bad"
        return "neutral"
    kl = k.lower()
    if any(h in kl for h in _GOOD_HINTS):
        return "good"
    if any(h in kl for h in _BAD_HINTS):
        return "bad"
    return "neutral"

def update_meta_keywords(
    token_address: Optional[str] = None,
    keywords: Union[str, List[str], None] = None,
    sentiment: Optional[float] = None,
    source: str = "",
) -> None:
    global _meta_keywords, token_meta_keywords
    words = _normalize_keywords(keywords)
    if not words:
        return

    if not _meta_keywords and not token_meta_keywords:
        load_meta_keywords()

    ts = _now_iso()
    for w in words:
        bucket = _bucket_for_keyword(w, sentiment)
        rec = _meta_keywords.get(w) or {
            "count": 0, "good": 0, "bad": 0, "neutral": 0,
            "last_update": ts, "sources": {}, "bucket": "neutral"
        }
        rec["count"] = int(rec.get("count", 0)) + 1
        rec[bucket] = int(rec.get(bucket, 0)) + 1
        rec["last_update"] = ts
        if source:
            rec["sources"][source] = int(rec["sources"].get(source, 0)) + 1

        # dominant bucket
        g, b, n = rec.get("good", 0), rec.get("bad", 0), rec.get("neutral", 0)
        rec["bucket"] = "good" if g >= b and g >= n else ("bad" if b >= g and b >= n else "neutral")
        _meta_keywords[w] = rec

    if token_address:
        s = token_meta_keywords.get(token_address) or set()
        s.update(words)
        token_meta_keywords[token_address] = s

    _save_kw_store(_dump_meta())

def get_meta_keywords(
    token_address: Optional[str] = None,
    limit: int = 20,
    bucket: Optional[str] = None
) -> List[str] | Dict[str, Any]:
    if not _meta_keywords and not token_meta_keywords:
        load_meta_keywords()

    if token_address:
        s = token_meta_keywords.get(token_address) or set()
        out = sorted(s)
        return out[:max(1, int(limit))]

    items = []
    for k, rec in _meta_keywords.items():
        if bucket and rec.get("bucket") != bucket:
            continue
        items.append((k, int(rec.get("count", 0)), rec.get("bucket", "neutral")))
    items.sort(key=lambda x: x[1], reverse=True)
    top = items[:max(1, int(limit))]
    return {
        "keywords": [k for k, _, _ in top],
        "buckets": {k: b for k, _, b in top},
        "counts": {k: c for k, c, _ in top},
        "last_update": _now_iso(),
    }

# =============================================================================
#                         RECENT KEYWORDS (VIEW)
# =============================================================================

def get_recent_keywords(since: Optional[datetime] = None, n: int = 25) -> List[str]:
    """
    Pulls from your token metadata store (if present) and lists recent tags.
    """
    try:
        from memory.token_memory_index import load_all_token_metadata
    except Exception:
        return []

    recent: List[tuple[str, float]] = []
    for token, meta in (load_all_token_metadata() or {}).items():
        tags = meta.get("tags", [])
        last_seen = meta.get("last_seen", 0)
        if not isinstance(last_seen, (int, float)):
            continue
        if since and datetime.utcfromtimestamp(last_seen) < since:
            continue
        for tag in tags:
            recent.append((tag, float(last_seen)))

    recent.sort(key=lambda x: x[1], reverse=True)
    return [tag for tag, _ in recent[:max(1, int(n))]]

# =============================================================================
#                              PERSIST / RESTORE
# =============================================================================

def _persist_runtime_state():
    """
    Save the lightweight runtime state (signals/tags) to librarian, if available.
    """
    if librarian:
        try:
            state = {
                "strategy_results": {k: list(v) for k, v in _strategy_results.items()},
                "overlap_triggers": _overlap_triggers,
                "reverse_learning_log": _reverse_learning_log,
                "meta_keywords": {k: list(v) for k, v in token_meta_keywords.items()},
                "memory_tokens": _memory.get("tokens", {}),
                "memory_tags": _memory.get("tags", {}),
                "ts": _now_iso(),
            }
            librarian.set_memory("strategy_memory", state)
        except Exception:
            pass

def load_memory(data: Optional[dict] = None):
    """
    Restore runtime pieces (used at bootstrap). Safe if nothing is there.
    """
    global _strategy_results, _overlap_triggers, _reverse_learning_log, token_meta_keywords, _memory
    try:
        data = data or (librarian.memory("strategy_memory", {}) if librarian else {})
        _strategy_results = {k: list(v) for k, v in data.get("strategy_results", {}).items()} or {s: [] for s in STRATEGIES}
        _overlap_triggers = data.get("overlap_triggers", {}) or {}
        _reverse_learning_log = data.get("reverse_learning_log", []) or []
        token_meta_keywords = {k: set(v) for k, v in data.get("meta_keywords", {}).items()} or {}
        _memory["tokens"] = data.get("memory_tokens", {}) or {}
        _memory["tags"]   = data.get("memory_tags", {}) or {}
    except Exception as e:
        logging.warning(f"[StrategyMemory] Failed to restore memory: {e}")

# =============================================================================
#                              REPORTING
# =============================================================================

def load_strategy_memory() -> dict:
    """
    Read the persistent W/L/R file (not the runtime state).
    """
    return _load_memory_file()

def audit_strategy_memory() -> str:
    return get_strategy_report()

# =============================================================================
#                               EXPORTS
# =============================================================================

__all__ = [
    # meta keywords
    "update_meta_keywords", "get_meta_keywords", "load_meta_keywords", "META_KEYWORD_FILE",
    # strategy signals / reports
    "update_strategy_signal", "get_strategy_score", "log_strategy_result", "calculate_total_win_rate",
    "get_strategy_performance", "get_recent_performance", "update_strategy_feedback",
    "record_strategy_adjustment", "get_strategy_adjustments",
    "register_overlap_trigger", "get_recent_overlap_triggers", "log_reverse_learning",
    "get_highest_scoring_idle_token", "save_strategy_snapshot",
    "record_result", "get_strategy_report", "audit_strategy_memory",
    # tags
    "tag_token_result", "normalize_tag", "get_token_tag", "is_blacklisted_token",
    "get_tagged_tokens", "get_tagged_tokens_report", "get_strategy_tags_for_wallet",
    # recent keywords view
    "get_recent_keywords",
    # load/persist runtime
    "load_memory",
]
