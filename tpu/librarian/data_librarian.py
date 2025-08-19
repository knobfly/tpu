#/librarian/data_librarian.py
# -----------------------------------------------------------------------------
# The Librarian: central, opinionated data organizer for Nyx.
#
# - Continuously ingests from JSONL log directories (incremental tail with offsets)
# - Optionally mirrors all live events from signal_bus (attach_bus)
# - Normalizes & indexes data by token, wallet, signal type
# - Exposes fast query APIs for Cortexes
# - No ai_brain import. Data flows up.
# -----------------------------------------------------------------------------

from __future__ import annotations

import asyncio
import gzip
import io
import json
import logging
import glob
import os
import re
import shutil
import time
import contextlib
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Any, Deque, Dict, List, Optional, Set, Tuple

import psutil
from core.live_config import config
from librarian.rules import telegram_auto_track
from utils.logger import log_event
from utils.service_status import update_status

# -----------------------------------
# CONFIG
# -----------------------------------

# Root runtime folders (absolute path)
RUNTIME_ROOT = Path("/home/ubuntu/nyx/runtime")
LOGS_ROOT    = Path("/home/ubuntu/nyx/runtime/logs")
LIBRARY_ROOT = Path("/home/ubuntu/nyx/runtime/library")

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")

GENRES = {
    "memes":    ["pepe", "wojak", "doge", "shib", "coq", "bonk", "meme"],
    "math":     ["arbitrage", "basis", "funding", "kelly", "pnl", "sharpe", "sortino", "variance", "alpha", "beta"],
    "profits":  ["win", "profit", "sell_win", "pnl_positive", "tp_hit"],
    "losses":   ["loss", "stop", "sell_loss", "rug", "pnl_negative"],
    "wallets":  ["wallet", "whale", "cluster", "cabal", "reputation", "banlist"],
    "listings": ["launch", "mint", "lp_add", "listing", "dex", "raydium", "orca", "pump"],
    "risk":     ["honeypot", "rug", "blacklist", "unlocked_lp", "scam"],
    "social":   ["telegram", "tweet", "x_post", "influencer", "sentiment"],
    "charts":   ["ohlcv", "pattern", "divergence", "volume_spike", "trend"],
}

# Known JSONL sources to tail & normalize
JSONL_SOURCES: Dict[str, Path] = {
    "insights":   RUNTIME_ROOT / "insights",
    "signals":    RUNTIME_ROOT / "signals",
    "trades":     RUNTIME_ROOT / "trades",
    "scoring":    RUNTIME_ROOT / "scoring",
    "wallets":    RUNTIME_ROOT / "wallets",
    "charts":     RUNTIME_ROOT / "charts",
    "firehose":   RUNTIME_ROOT / "firehose",
    "nft":        RUNTIME_ROOT / "nft",
    "strategy":   RUNTIME_ROOT / "strategy",
}

# Refresh intervals
DISK_SCAN_INTERVAL_SEC   = 5
STATUS_HEARTBEAT_SECONDS = 30

# Memory limits
MAX_EVENTS_PER_TYPE      = 5000     # global ring buffer per event type
MAX_TOKEN_EVENTS         = 2000     # per token
MAX_WALLET_EVENTS        = 2000     # per wallet

from glob import glob as _glob

def _safe_iter_jsonl(pathlike: str | Path):
    """
    Yield JSON objects from a .jsonl or .jsonl.gz file safely.
    """
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

def _iter_glob_jsonl(glob_pattern: str):
    """
    Yield jsonl events from all files matching the glob pattern, safely.
    """
    if not glob_pattern:
        return
    for p in _glob(glob_pattern):
        try:
            for x in _safe_iter_jsonl(p):
                yield x
        except Exception:
            continue


@dataclass
class TokenRecord:
    token: str
    last_ts: float = 0.0
    events: Deque[dict] = field(default_factory=lambda: deque(maxlen=MAX_TOKEN_EVENTS))
    tags: Set[str] = field(default_factory=set)
    scores: Deque[dict] = field(default_factory=lambda: deque(maxlen=256))  # keep last N scoring snapshots
    chart: Dict[str, Any] = field(default_factory=dict)   # last known chart metrics
    meta: Dict[str, Any] = field(default_factory=dict)    # last known token metadata
    scanners: Set[str] = field(default_factory=set)       # sources that touched this token





@dataclass
class WalletRecord:
    wallet: str
    last_ts: float = 0.0
    events: Deque[dict] = field(default_factory=lambda: deque(maxlen=MAX_WALLET_EVENTS))
    reputation: float = 0.0
    tags: Set[str] = field(default_factory=set)
    clusters: Set[str] = field(default_factory=set)  # cluster ids if you use them
    meta: Dict[str, Any] = field(default_factory=dict)


class DataLibrarian:
    """
    One librarian to rule them all. Central ingestion, normalization, and indexing.
    """

    def __init__(self):
        # Global event ring buffers per logical type
        self._events_by_type: Dict[str, Deque[dict]] = defaultdict(lambda: deque(maxlen=MAX_EVENTS_PER_TYPE))
        self.persistence_dir = "/home/ubuntu/nyx/runtime/library/"
        # Indices
        self.token_memory = {}
        self.wallet_memory = {}
        self.group_memory = {}
        self.trade_feedback = []
        self.strategy_memory = {}

        self._tokens: Dict[str, TokenRecord] = {}
        self._wallets: Dict[str, WalletRecord] = {}

        self._memory_store = {}
        self._access_log: Dict[str, dict] = {}
        self._memory_ttl: Dict[str, float] = {}
        self._registered_objects = {}
        self._memory_file = os.path.expanduser("/home/ubuntu/nyx/runtime/memory/librarian.json")
        self._memory_loaded = False
        self.counters = {"events_ingested": 0, "stream_events": 0}

        # File offsets to support incremental JSONL tailing
        self._file_offsets: Dict[Path, int] = {}

        # Attached signal bus
        self._bus = None

        # Concurrency
        self._lock = asyncio.Lock()

        # Status
        self._last_status_beat = 0.0

        # === Internal Persistent Memory ===
        self.seen_tokens: Dict[str, dict] = self._load_json("seen_tokens.json")
        self.seen_x_posts: Dict[str, dict] = self._load_json("seen_x_posts.json")
        self.seen_wallets: Dict[str, dict] = self._load_json("seen_wallets.json")
        self.token_profiles: Dict[str, dict] = self._load_json("token_profiles.json")
        self.seen_token_names: dict
        self.seen_x_posts_by_name: dict
        self._skip_no_contract_count = 0
        self._skip_last_warn_ts = 0.0
        self._skip_warn_every_n = 50      # only warn every N skips
        self._skip_warn_min_s = 60.0      # and at most once per 60s
        self._skip_sampled = 0
        self._skip_sample_cap = 5         # sample at most 5 per hour
        self._skip_sample_window_start = 0.0
        self._skipped_samples_path = "/home/ubuntu/nyx/runtime/monitor/skipped_stream_samples.jsonl"

# --- inside class DataLibrarian ---------------------------------------------

    def _ensure_maps(self):
        # idempotent initializers
        if not hasattr(self, "token_tags"):   self.token_tags = {}          # {token: set([...])}
        if not hasattr(self, "wallet_tags"):  self.wallet_tags = {}         # {wallet: set([...])}
        if not hasattr(self, "seen_tokens"):  self.seen_tokens = {}         # metadata cache
        if not hasattr(self, "seen_wallets"): self.seen_wallets = {}        # metadata cache
        if not hasattr(self, "seen_x_posts"): self.seen_x_posts = {}        # cross-link cache

    def _save_json(self, obj, filename: str):
        import json, os
        os.makedirs(self.runtime_dir, exist_ok=True)  # make sure you have self.runtime_dir set up
        path = os.path.join(self.runtime_dir, filename)
        try:
            with open(path, "w") as f:
                json.dump(obj, f, indent=2)
        except Exception:
            pass  # keep non-fatal

    def tag_token(self, token: str, tag: str):
        """
        Lightweight tagging used by many call sites.
        Persists to runtime json: token_tags.json
        """
        if not token or not tag:
            return
        self._ensure_maps()
        tags = self.token_tags.get(token)
        if tags is None:
            tags = set()
            self.token_tags[token] = tags
        tags.add(tag)
        # persist as list for json
        serializable = {k: sorted(list(v)) for k, v in self.token_tags.items()}
        self._save_json(serializable, "token_tags.json")

    def tag_wallet(self, wallet: str, tag: str):
        """
        Lightweight wallet tagging.
        Persists to runtime json: wallet_tags.json
        """
        if not wallet or not tag:
            return
        self._ensure_maps()
        tags = self.wallet_tags.get(wallet)
        if tags is None:
            tags = set()
            self.wallet_tags[wallet] = tags
        tags.add(tag)
        serializable = {k: sorted(list(v)) for k, v in self.wallet_tags.items()}
        self._save_json(serializable, "wallet_tags.json")

    def record_signal(self, payload: dict):
        """
        Append an event/trace row into a rolling jsonl file.
        """
        import json, os, time
        os.makedirs(self.runtime_dir, exist_ok=True)
        path = os.path.join(self.runtime_dir, "signals.jsonl")
        try:
            payload = dict(payload or {})
            payload.setdefault("ts", time.time())
            with open(path, "a") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ------------- Public API -------------

    async def start(self):
        """
        Start the background tasks that keep the librarian fresh.
        """
        log_event("📚 Data Librarian started.")
        await self._initial_scan()

        # background loops
        asyncio.create_task(self._disk_scan_loop())
        asyncio.create_task(self._status_loop())
        asyncio.create_task(self._library_maintenance_loop())

    def _list_append(self, key: str, entry):
        lst = self._memory_store.get(key)
        if not isinstance(lst, list):
            lst = [] if lst is None else [lst] if lst != {} else []
            self._memory_store[key] = lst
        lst.append(entry)


    def attach_bus(self, bus):
        """
        Attach signal_bus so we mirror its events too (optional).
        """
        self._bus = bus

        async def _mirror(payload):
            await self.record_event("bus_signal", payload)

        # Listen to all events by subscribing to a wildcard or register per topic externally.
        # We'll expose a helper in this librarian to register specific topics explicitly:
        #   librarian.subscribe_bus("wallet_event")
        self._bus_register_helper = _mirror


    @property
    def memory_store(self) -> dict:
        """Back-compat for modules that used librarian.memory_store"""
        return self._memory_store

    @memory_store.setter
    def memory_store(self, value: dict) -> None:
        # if someone assigns, merge instead of replacing reference
        if isinstance(value, dict):
            self._memory_store.clear()
            self._memory_store.update(value)

    async def subscribe_bus(self, topic: str):
        """
        Subscribe to a signal_bus topic and mirror those events internally.
        Call this AFTER attach_bus(bus).
        """
        if not self._bus:
            raise RuntimeError("signal_bus not attached. Call librarian.attach_bus(bus) first.")

        async def _mirror(payload):
            await self.record_event(topic, payload)

        self._bus.subscribe(topic, _mirror)  # bus can accept sync/async callbacks

    async def record_event(self, event_type: str, payload: dict):
        """
        Publicly callable for any module that wants to push data directly into the librarian
        (bypassing JSONL or when not using signal_bus).
        """
        ts = payload.get("timestamp") or payload.get("ts") or time.time()
        ev = {"ts": ts, "type": event_type, "payload": payload}

        async with self._lock:
            self._events_by_type[event_type].append(ev)
            # index by token/wallet if present
            token = _find_token(payload)
            wallet = _find_wallet(payload)

            if token:
                self._index_token_event(token, ev)
                self._counters["events_ingested"] += 1
                if event_type in ("solana_log", "stream_event", "logsSubscribe"):
                    self._counters["stream_events"] += 1
            if wallet:
                self._index_wallet_event(wallet, ev)
                self._counters["events_ingested"] += 1
                if event_type in ("solana_log", "stream_event", "logsSubscribe"):
                    self._counters["stream_events"] += 1

    def memory(self, key: str, default=None):
        """
        Unified memory access layer.
        Supports expiration, tagging, type-safety, hooks, and diagnostics.
        """
        # Safety checks
        assert isinstance(key, str) and key, "Invalid memory key"
        if key not in self._memory_store or self._is_expired(key):
            self._memory_store[key] = default

        # Access metadata
        self._access_log[key] = {
            "last_access": time.time(),
            "access_count": self._access_log.get(key, {}).get("access_count", 0) + 1,
        }

        # Hooks per key type
        if key == "token_memory":
            self._maybe_decay_token_scores()
        elif key == "wallet_memory":
            self._trigger_wallet_linking()

        # Notify subsystems if needed
        self._check_memory_alerts(key, self._memory_store[key])

        return self._memory_store[key]

    def get_memory(self, key: str, default=None):
        return self._memory_store.get(key, default)

    def set_memory(self, key: str, value):
        self._memory_store[key] = value

    def del_memory(self, key: str):
        if key in self._memory_store:
            del self._memory_store[key]

    def register(self, name: str, obj):
        self._registered_objects[name] = obj



    def load_all(self):
        if self._memory_loaded:
            return
        self._memory_loaded = True

        if os.path.exists(self._memory_file):
            try:
                with open(self._memory_file, "r") as f:
                    self._memory_store = json.load(f)
                logging.info("🧠 Librarian: Loaded runtime memory from disk.")
            except Exception as e:
                logging.warning(f"[Librarian] Failed to load memory: {e}")

        for name, obj in self._registered_objects.items():
            saved = self._memory_store.get(name)
            if saved and hasattr(obj, "load_memory"):
                try:
                    obj.load_memory(saved)
                    logging.info(f"🧠 {name} restored from memory.")
                except Exception as e:
                    logging.warning(f"[Librarian] Failed to restore {name}: {e}")

        for name, obj in self._registered_objects.items():
            if hasattr(obj, "save_memory"):
                try:
                    self._memory_store[name] = obj.save_memory()
                except Exception as e:
                    logging.warning(f"[Librarian] Failed to persist {name}: {e}")

        self.save_memory()

    def get_trending_token_candidates(
        self,
        *,
        limit: int = 50,
        window_minutes: int = 60,
        min_social_mentions: int = 3,
        min_wallet_buys: int = 2,
    ) -> list[str]:
        """
        Return a ranked list of trending token mints from recent on-disk signals.
        Signals fused from: social mentions, stream events, wallet buys, liquidity adds, and volume spikes.
        """
        now_ts = time.time()
        cutoff = now_ts - window_minutes * 60

        JSONL_GLOBS = {
            "social_logs":      "/home/ubuntu/nyx/runtime/memory/x/*.jsonl*",
            "stream_events":    "/home/ubuntu/nyx/runtime/library/feature_store/features_*.jsonl*",
            "trades_store":     "/home/ubuntu/nyx/runtime/library/feature_store/trades_*.jsonl*",
            "wallet_activity":  "/home/ubuntu/nyx/runtime/library/feature_store/wallet_*.jsonl*",
            "liquidity_events": "/home/ubuntu/nyx/runtime/library/feature_store/liquidity_*.jsonl*",
        }

        # Social logs
        social_counts: dict[str, int] = {}
        for ev in _iter_glob_jsonl(JSONL_GLOBS.get("social_logs", "")):
            try:
                ts = float(ev.get("_ts") or ev.get("ts") or 0)
                if ts < cutoff:
                    continue
                mint = str(ev.get("mint") or "").strip()
                if mint:
                    social_counts[mint] = social_counts.get(mint, 0) + 1
            except Exception:
                continue

        # Stream events
        stream_counts: dict[str, int] = {}
        for ev in _iter_glob_jsonl(JSONL_GLOBS.get("stream_events", "")):
            try:
                ts = float(ev.get("_ts") or ev.get("ts") or 0)
                if ts < cutoff:
                    continue
                mint = str(ev.get("mint") or "").strip()
                if mint:
                    stream_counts[mint] = stream_counts.get(mint, 0) + 1
            except Exception:
                continue

        # Trades
        wallet_buys: dict[str, int] = {}
        for ev in _iter_glob_jsonl(JSONL_GLOBS.get("trades_store", "")):
            try:
                ts = float(ev.get("_ts") or ev.get("ts") or 0)
                if ts < cutoff:
                    continue
                if str(ev.get("side")).lower() == "buy":
                    mint = str(ev.get("mint") or "").strip()
                    if mint:
                        wallet_buys[mint] = wallet_buys.get(mint, 0) + 1
            except Exception:
                continue

        # Wallet activity (buys/swaps in)
        for ev in _iter_glob_jsonl(JSONL_GLOBS.get("wallet_activity", "")):
            try:
                ts = float(ev.get("_ts") or ev.get("ts") or 0)
                if ts < cutoff:
                    continue
                kind = str(ev.get("type") or ev.get("side", "")).lower()
                if kind in ("buy", "swap_in", "trade_in"):
                    mint = str(ev.get("mint") or "").strip()
                    if mint:
                        wallet_buys[mint] = wallet_buys.get(mint, 0) + 1
            except Exception:
                continue

        # Liquidity adds
        liq_added: set[str] = set()
        for ev in _iter_glob_jsonl(JSONL_GLOBS.get("liquidity_events", "")):
            try:
                ts = float(ev.get("_ts") or ev.get("ts") or 0)
                if ts < cutoff:
                    continue
                if str(ev.get("type")).lower() in ("lp_add", "add_liquidity", "lp_addition"):
                    mint = str(ev.get("mint") or "").strip()
                    if mint:
                        liq_added.add(mint)
            except Exception:
                continue

        # Candidate set
        candidate_mints: set[str] = set()
        candidate_mints.update(social_counts.keys())
        candidate_mints.update(stream_counts.keys())
        candidate_mints.update(wallet_buys.keys())
        candidate_mints.update(liq_added)

        # Helpers (stubbed; swap to your actual implementations if needed)
        def get_ohlcv(mint: str, limit: int = 60):
            try:
                return self.get_ohlcv(mint, limit=limit)  # if your class exposes it
            except Exception:
                return []

        def volume_spike(candles, lookback=30, k=2.0) -> bool:
            if not candles or len(candles) < lookback + 1:
                return False
            vols = [float(c.get("volume", 0)) for c in candles[-(lookback+1):]]
            recent = vols[-1]
            base = sum(vols[:-1]) / max(1, len(vols[:-1]))
            return recent > k * base if base > 0 else False

        ranked: list[tuple[str, float]] = []
        for mint in candidate_mints:
            s_count = social_counts.get(mint, 0)
            w_count = wallet_buys.get(mint, 0)
            st_count = stream_counts.get(mint, 0)
            liq_boost = 1.0 if mint in liq_added else 0.0
            spike_boost = 1.0 if volume_spike(get_ohlcv(mint)) else 0.0

            score = (
                (1.0 if s_count >= min_social_mentions else 0.0)
                + (1.0 if w_count >= min_wallet_buys else 0.0)
                + (0.5 if st_count >= 5 else 0.0)
                + liq_boost
                + spike_boost
            )
            if score > 0.0:
                ranked.append((mint, score))

        ranked.sort(
            key=lambda t: (t[1],
                           wallet_buys.get(t[0], 0),
                           social_counts.get(t[0], 0),
                           stream_counts.get(t[0], 0)),
            reverse=True,
        )
        return [m for (m, _s) in ranked[:max(1, int(limit))]]

    def save_memory(self):
        """
        Persist in-memory state to disk and record a journal entry.
        Safe and idempotent. Uses atomic write.
        """
        try:
            entry = {
                "ts": datetime.utcnow().isoformat(),
                "summary": {
                    "keys": list(self._memory_store.keys()),
                    "counts": {
                        k: (len(v) if isinstance(v, (list, dict, set, tuple)) else 1)
                        for k, v in self._memory_store.items()
                    }
                }
            }

            try:
                if hasattr(self, "_list_append"):
                    self._list_append("events", entry)
                else:
                    buf = self._memory_store.setdefault("events", [])
                    if isinstance(buf, list):
                        buf.append(entry)
            except Exception:
                pass

            try:
                from librarian.rules import telegram_auto_track
                if hasattr(telegram_auto_track, "run_on_memory_entry"):
                    telegram_auto_track.run_on_memory_entry(entry)
            except Exception:
                pass

            tmp = f"{self._memory_file}.tmp"
            with open(tmp, "w") as f:
                json.dump(self._memory_store, f, indent=2, default=str)
            os.replace(tmp, self._memory_file)

            logging.debug("🧠 Librarian: Saved runtime memory.")
        except Exception as e:
            logging.warning(f"[Librarian] Failed to save memory: {e}")

    def remember(self, key: str, value):
        self._memory_store[key] = value
        self.save_memory()

    def recall(self, key: str, default=None):
        return self._memory_store.get(key, default)

    def remember_list(self, key: str, value):
        self._memory_store.setdefault(key, [])
        if value not in self._memory_store[key]:
            self._memory_store[key].append(value)
            self.save_memory()

    def _load_json(self, filename: str) -> dict:
        path = os.path.join(self.persistence_dir, filename)
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_json(self, data: dict, filename: str):
        path = os.path.join(self.persistence_dir, filename)
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logging.warning(f"[Librarian] Failed to save {filename}: {e}")

    def load_json_file(self, file_path: str, default: Any = None) -> Any:
        if not os.path.exists(file_path):
            logging.debug(f"[Librarian] JSON file not found: {file_path}")
            return default
        try:
            with open(file_path, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.warning(f"[Librarian] Failed to load JSON from {file_path}: {e}")
            return default

    def save_json_file(self, file_path: str, data: Any):
        try:
            with open(file_path, "w") as f:
                json.dump(data, f, indent=2)
            logging.debug(f"[Librarian] Saved JSON to: {file_path}")
        except Exception as e:
            logging.warning(f"[Librarian] Failed to save JSON to {file_path}: {e}")

    def memory_usage_summary(self) -> dict:
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        return {
            "rss_mb": round(mem_info.rss / (1024 ** 2), 2),
            "vms_mb": round(mem_info.vms / (1024 ** 2), 2),
            "percent_used": psutil.virtual_memory().percent,
            "total_mb": round(psutil.virtual_memory().total / (1024 ** 2), 2),
            "available_mb": round(psutil.virtual_memory().available / (1024 ** 2), 2),
        }

    def evolve_strategy(self, *, context: dict | None = None, outcome: dict | None = None, reinforce: bool = True) -> dict:
        """
        Adapter for callers like Maintenance.run_hourly_maintenance().
        Delegates to available strategy backends. Returns a stable dict.
        """
        import logging

        ctx = context or {}
        try:
            # 1) Preferred backend: strategy_memory.evolve_strategy(...)
            try:
                from strategy.strategy_memory import evolve_strategy as sm_evolve  # type: ignore
                res = sm_evolve(context=ctx, outcome=outcome, reinforce=reinforce)
                if isinstance(res, dict):
                    return res
            except Exception as e:
                logging.debug(f"[Librarian] strategy_memory.evolve_strategy unavailable: {e}")

            # 2) Fallback: strategy_memory.tune_strategy_context(...)
            try:
                from strategy.strategy_memory import (
                    tune_strategy_context as sm_tune_ctx,  # type: ignore
                )
                res = sm_tune_ctx(ctx)
                if isinstance(res, dict):
                    res.setdefault("notes", []).append("via strategy_memory.tune_strategy_context")
                    return res
            except Exception as e:
                logging.debug(f"[Librarian] strategy_memory.tune_strategy_context unavailable: {e}")

            # 3) Legacy fallback: ai_self_tuner.tune_strategy(...)
            try:
                from strategy.ai_self_tuner import tune_strategy as legacy_tune  # type: ignore
                res = legacy_tune(ctx)
                if isinstance(res, dict):
                    res.setdefault("notes", []).append("via ai_self_tuner.tune_strategy")
                    return res
            except Exception as e:
                logging.debug(f"[Librarian] ai_self_tuner.tune_strategy unavailable: {e}")

            # 4) No-op safe default
            logging.info("[Librarian] evolve_strategy: no strategy backend available; returning no-op.")
            return {
                "final_score": float(ctx.get("meta_score", 0) or 0),
                "aggression": "balanced",
                "exit_mode": "default",
                "notes": ["no-op"],
            }

        except Exception as e:
            logging.warning(f"[Librarian] evolve_strategy failed: {e}")
            return {
                "final_score": float(ctx.get("meta_score", 0) or 0),
                "aggression": "balanced",
                "exit_mode": "default",
                "notes": [f"error: {e}"],
            }

    # === Full Profile Enricher ===
    def _enrich_token_profile(self, contract: str):
        profile = self.token_profiles.get(contract, {})

        # Aggregate from all known sources
        token_meta = self.seen_tokens.get(contract, {})
        x_meta = self.seen_x_posts.get(contract, {})
        wallet_meta = self.seen_wallets.get(contract, {})

        profile.update(token_meta)
        profile.update(x_meta)
        profile.update(wallet_meta)
        profile["contract"] = contract

        self.token_profiles[contract] = profile
        self._save_json(self.token_profiles, "token_profiles.json")
        logging.info(f"[Librarian] Enriched token profile for {contract}")

    def decay_keywords(self, decay_rate: float = 0.9, min_weight: float = 0.1):
        """
        Gradually decays stored keyword weights to prevent stale bias.
        decay_rate: Multiplier applied to each keyword's weight (default 0.9 = 10% decay).
        min_weight: Keywords below this weight will be removed entirely.
        """
        try:
            keyword_store = getattr(self, "keyword_store", {})

            if not isinstance(keyword_store, dict):
                return

            to_delete = []
            for keyword, weight in keyword_store.items():
                try:
                    new_weight = weight * decay_rate
                    if new_weight < min_weight:
                        to_delete.append(keyword)
                    else:
                        keyword_store[keyword] = new_weight
                except Exception:
                    continue

            for keyword in to_delete:
                del keyword_store[keyword]

        except Exception as e:
            import logging
            logging.warning(f"[DataLibrarian] Keyword decay failed: {e}")

    def ingest_token_profile(self, profile: dict):
        """
        Merges a new token profile fragment into memory.

        Accepts fragments like:
        {
            "contract": str,
            "name": str,
            "symbol": str,
            "source": str,
            "timestamp": float,
            "wallets": [...],
            "x_mentions": [...],
            ...
        }
        """
        try:
            contract = profile.get("contract")
            if not contract:
                logging.warning("[Librarian] Ignored profile with no contract")
                return

            # Existing record or fresh
            existing = self.seen_tokens.get(contract, {})
            merged = {**existing, **profile}

            # Update timestamp and source
            merged["last_updated"] = time.time()
            merged["source"] = profile.get("source", merged.get("source", "unknown"))

            # Handle tag merging if present
            existing_tags = set(existing.get("tags", []))
            new_tags = set(profile.get("tags", []))
            merged["tags"] = list(existing_tags.union(new_tags))

            # Save and trigger enrichment
            self.seen_tokens[contract] = merged
            self._save_json(self.seen_tokens, "seen_tokens.json")
            self._enrich_token_profile(contract)

        except Exception as e:
            logging.warning(f"[Librarian] Failed to ingest token profile: {e}")

    def _enrich_token_profile(self, contract: str):
        """
        Reconstructs missing fields in a token profile from other known data.
        Fills in name, symbol, source, theme, tags, and wallet overlap.
        """
        try:
            token = self.seen_tokens.get(contract)
            if not token:
                return

            # Ensure name/symbol at minimum
            if not token.get("name"):
                token["name"] = self.token_name_map.get(contract) or "unknown"
            if not token.get("symbol"):
                token["symbol"] = token.get("name", "???")[:4].upper()

            # Infer theme from keywords
            name_lower = token.get("name", "").lower()
            matched = [
                theme for theme in self.theme_keywords
                if theme in name_lower
            ]
            token["theme"] = matched or []

            # Wallet overlap tags
            overlap_tags = []
            for addr in token.get("wallets", []):
                wallet_info = self.wallet_memory.get(addr, {})
                wallet_tags = wallet_info.get("tags", [])
                overlap_tags.extend(wallet_tags)

            token["wallet_tags"] = list(set(overlap_tags))

            # Save updated enriched record
            self.seen_tokens[contract] = token
            self._save_json(self.seen_tokens, "seen_tokens.json")

        except Exception as e:
            logging.warning(f"[Librarian] Failed to enrich token profile: {e}")

    def has_seen_token(self, contract: str) -> bool:
        """
        Returns True if the token contract has been seen before in the system.
        """
        return contract in self.seen_tokens

    def has_seen_x(self, handle_or_id: str) -> bool:
        """
        Returns True if this X (Twitter) account has been logged or tracked before.
        Accepts either the handle (@name) or numeric ID.
        """
        return handle_or_id in self.x_memory or handle_or_id.lower() in self.x_memory

    def register_wallet_intel(self, wallet: str, traits: Optional[Dict] = None):
        """
        Register behavioral traits or intelligence flags about a wallet.
        This could include labels like 'sniper', 'dumped quickly', 'joined cabal', etc.
        """
        if not wallet:
            return

        if wallet not in self.wallet_memory:
            self.wallet_memory[wallet] = {"traits": set(), "txns": [], "last_seen": time.time()}

        if traits:
            self.wallet_memory[wallet]["traits"].update(traits.get("traits", []))
            self.wallet_memory[wallet]["last_seen"] = time.time()

    def register_x_alpha(self, handle: str, *, token: Optional[str] = None, reason: Optional[str] = None):
        """
        Register an alpha signal or important context linked to an X (Twitter) handle.
        This marks the handle as influential or part of a cluster.
        """
        if not handle:
            return

        handle = handle.lower()
        if handle not in self.x_memory:
            self.x_memory[handle] = {"tokens": set(), "reasons": set(), "first_seen": time.time()}

        if token:
            self.x_memory[handle]["tokens"].add(token)

        if reason:
            self.x_memory[handle]["reasons"].add(reason)



    async def get_group_map(self, key: str):
        """
        Retrieve a stored group map by key.
        Returns a dict if found, or None if missing.
        """
        try:
            store = getattr(self, "memory_store", {})
            if isinstance(store, dict):
                value = store.get(key)
                if value and isinstance(value, dict):
                    return value
        except Exception as e:
            import logging
            logging.warning(f"[DataLibrarian] Failed to get group map for '{key}': {e}")
        return None

    def get_tokens_in_wallet(self, wallet_address: str):
        """
        Return a list of tokens currently associated with a wallet.
        """
        try:
            wallet_data = getattr(self, "wallet_store", {})
            if isinstance(wallet_data, dict):
                return wallet_data.get(wallet_address, [])
        except Exception as e:
            import logging
            logging.warning(f"[DataLibrarian] Failed to get tokens in wallet {wallet_address}: {e}")
        return []

    def clear_memory_logs(self, max_age_days: int = 7, max_token_count: int = 2000):
        trimmed = 0
        now = datetime.utcnow()
        cutoff = now - timedelta(days=max_age_days)

        token_mem = self.memory("token_memory", {})
        if not isinstance(token_mem, dict):
            raise TypeError("token_memory is not a dict")

        new_token_memory = {}
        for token, data in token_mem.items():
            last_seen = data.get("last_seen")
            if not last_seen:
                continue
            try:
                timestamp = datetime.fromisoformat(last_seen)
                if timestamp > cutoff:
                    new_token_memory[token] = data
                else:
                    trimmed += 1
            except Exception:
                new_token_memory[token] = data

        token_trimmed = dict(
            sorted(new_token_memory.items(), key=lambda x: x[1].get("last_seen", ""), reverse=True)[:max_token_count]
        )
        self.set_memory("token_memory", token_trimmed)

        strategy_mem = self.get_memory("strategy_memory", {})
        if isinstance(strategy_mem, dict) and len(strategy_mem) > 3000:
            self.set_memory("strategy_memory", dict(list(strategy_mem.items())[-2000:]))
            trimmed += 1

        logging.info(f"[Librarian] 🧠 Trimmed {trimmed} old token logs")
        return f"🧠 Trimmed {trimmed} old memory entries."

    def prune_memory(self, max_items_per_key: int = 500, max_age_days: int = 14) -> int:
        """
        Trim oversized lists and drop entries older than max_age_days.
        Returns number of entries pruned.
        """
        pruned = 0
        store = getattr(self, "_memory_store", {})
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)

        for key, val in list(store.items()):
            if isinstance(val, list):
                if len(val) > max_items_per_key:
                    del_count = len(val) - max_items_per_key
                    store[key] = val[-max_items_per_key:]
                    pruned += del_count

                new_list = []
                for item in store[key]:
                    ts = None
                    if isinstance(item, dict):
                        ts = item.get("ts") or item.get("timestamp")
                    try:
                        if ts:
                            dt = datetime.fromisoformat(str(ts).replace("Z",""))
                            if dt < cutoff:
                                pruned += 1
                                continue
                    except Exception:
                        pass
                    new_list.append(item)
                store[key] = new_list

            if isinstance(val, dict) and not val:
                continue

        self._memory_store = store
        return pruned


    def _is_expired(self, key: str) -> bool:
        ttl = self._memory_ttl.get(key)
        return ttl is not None and time.time() > ttl

    def _maybe_decay_token_scores(self):
        mem = self._memory_store.get("token_memory")
        if isinstance(mem, dict):
            for token, obj in mem.items():
                if isinstance(obj, dict):
                    score = obj.get("score", 0.0)
                    obj["score"] = round(score * 0.98, 6)

    def record_signal(self, signal: dict) -> None:
        """
        Record an incoming signal for learning or analysis (append-only JSONL).
        File: runtime/logs/signals/signals.jsonl
        """
        if not isinstance(signal, dict):
            return
        rec = dict(signal)
        rec.setdefault("_ts", time.time())
        out = Path(os.environ.get("NYX_ROOT", "/home/ubuntu/nyx")) / "runtime" / "logs" / "signals" / "signals.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            with out.open("a", encoding="utf-8") as f:
                import json as _json
                f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            # never raise from telemetry
            pass

    def _trigger_wallet_linking(self, wallet: str, *, reason: str, mint: str | None = None) -> None:
        path = self.RUNTIME / "logs" / "wallets" / "link_hints.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"_ts": time.time(), "wallet": wallet, "reason": reason}
        if mint: rec["mint"] = mint
        self._atomic_append_jsonl(path, rec)

    def _check_memory_alerts(self, key: str, mem):
        if key == "token_memory" and isinstance(mem, dict):
            if len(mem) > 5000:
                logging.warning(f"[Memory] ⚠️ token_memory is large: {len(mem)} tokens")

    async def get_recent_events(self, event_type: Optional[str] = None, limit: int = 100) -> List[dict]:
        async with self._lock:
            if event_type is None:
                merged = []
                for dq in self._events_by_type.values():
                    merged.extend(list(dq)[-limit:])
                merged.sort(key=lambda e: e["ts"])
                return merged[-limit:]
            return list(self._events_by_type[event_type])[-limit:]

    def _ensure_maps(self) -> None:
        if not hasattr(self, "seen_token_names"):
            self.seen_token_names = {}
        if not hasattr(self, "seen_x_posts_by_name"):
            self.seen_x_posts_by_name = {}

    def _extract_token_name(self, token_list: list[Any]) -> Optional[str]:
        """Try to pull a usable token name/symbol from mixed token payloads."""
        for t in token_list or []:
            if isinstance(t, dict):
                for k in ("symbol", "ticker", "name"):
                    v = t.get(k)
                    if v and isinstance(v, str) and v.strip():
                        return v.strip()
            elif isinstance(t, str):
                s = t.strip()
                # heuristic: short-ish strings are likely symbols
                if s and len(s) <= 24:
                    return s
        return None

    def _classify_identifier(self, event: dict) -> Optional[Tuple[str, str]]:
        """
        Return ('contract', <contract>) or ('token_name', <name>).
        Return None if neither is available.
        """
        contract = event.get("contract")
        if contract and isinstance(contract, str) and contract.strip():
            return ("contract", contract.strip())

        token_list = event.get("tokens", []) or []
        name = self._extract_token_name(token_list)
        if name:
            return ("token_name", name)

        return None


    def _first_base58(self, *fields) -> str | None:
        """
        Return the first base58-looking address (32..44 chars) found
        in any provided string fields.
        """
        for f in fields:
            if not f:
                continue
            if isinstance(f, (list, tuple)):
                # scan lists of strings dicts etc.
                for item in f:
                    if isinstance(item, str):
                        m = _BASE58_RE.search(item)
                        if m:
                            return m.group(0)
                    elif isinstance(item, dict):
                        # common keys people pass
                        cand = item.get("mint") or item.get("address") or item.get("token") or item.get("contract")
                        if isinstance(cand, str):
                            m = _BASE58_RE.search(cand)
                            if m:
                                return m.group(0)
            elif isinstance(f, dict):
                cand = f.get("mint") or f.get("address") or f.get("token") or f.get("contract")
                if isinstance(cand, str):
                    m = _BASE58_RE.search(cand)
                    if m:
                        return m.group(0)
            elif isinstance(f, str):
                m = _BASE58_RE.search(f)
                if m:
                    return m.group(0)
        return None

    def _pick_contract(self, event: dict) -> tuple[str | None, str | None]:
        """
        Best-effort extraction of (contract_mint, token_name_or_symbol).
        Rule order:
          1) Explicit fields on the event: contract/mint/token/program_id
          2) Any base58 address in tokens[], wallets[], logs, signature
          3) Name/symbol from event or tokens[]
        Returns (contract, token_name). Either may be None.
        """
        if not isinstance(event, dict):
            return None, None

        # direct fields first
        contract = (
            event.get("contract")
            or event.get("mint")
            or event.get("token")
            or event.get("program_id")
        )
        # normalize direct field if it isn't already base58-ish
        if not (isinstance(contract, str) and _BASE58_RE.fullmatch(contract)):
            contract = self._first_base58(
                contract,
                event.get("signature"),
                event.get("program_id"),
                event.get("tokens"),
                event.get("wallets"),
                event.get("logs"),
                event.get("raw"),
            )

        # name/symbol
        token_name = (
            event.get("token_name")
            or event.get("symbol")
            or event.get("name")
        )

        # try tokens[] objects for richer hints
        tokens = event.get("tokens") or []
        for t in tokens:
            if isinstance(t, dict):
                token_name = token_name or t.get("symbol") or t.get("name")
                if not contract:
                    contract = self._first_base58(t)

        return contract, token_name

    # === update your existing ingest_stream_event to use the picker ===
    async def ingest_stream_event(self, event: dict):
        """
        Catalog and classify streamed Solana log data.
        Tags tokens/wallets, stores signal metadata, X links, and raw trace.
        Requires either contract (mint) OR token_name to proceed.
        """
        try:
            contract, token_name = self._pick_contract(event)

            # hard gate: must have at least one identifier
            if not (contract or token_name):
                logging.warning("[Librarian] Skipped stream event with no contract or token name")
                return

            token_list  = event.get("tokens", []) or []
            wallet_list = event.get("wallets", []) or []
            wallet      = event.get("wallet")
            kind        = event.get("kind", "unknown")
            source      = event.get("source", "solana_stream")
            x_data      = event.get("x_meta")
            signature   = event.get("signature")
            program_id  = event.get("program_id")
            slot        = event.get("slot")
            timestamp   = event.get("timestamp") or time.time()
            logs        = event.get("logs")

            # === tag seen items ===
            for t in token_list:
                # accept strings and dicts
                if isinstance(t, str):
                    self.tag_token(t, "stream_seen")
                elif isinstance(t, dict):
                    mint = t.get("mint") or t.get("address") or t.get("token") or t.get("contract")
                    if mint:
                        self.tag_token(mint, "stream_seen")

            for w in wallet_list:
                if isinstance(w, str):
                    self.tag_wallet(w, "stream_seen")
                elif isinstance(w, dict):
                    addr = w.get("address") or w.get("wallet")
                    if addr:
                        self.tag_wallet(addr, "stream_seen")

            # === persist token contract metadata (index by contract if present, else by name) ===
            key = contract or token_name
            self.seen_tokens[key] = {
                "seen_at": time.time(),
                "kind": kind,
                "source": source,
                "wallet": wallet,
                "token_name": token_name,
                "contract": contract,
                "x_data": x_data,
                "raw": event,
            }
            self._save_json(self.seen_tokens, "seen_tokens.json")

            # === save originating wallet metadata ===
            if wallet:
                self.seen_wallets[wallet] = {
                    "contract": contract,
                    "token_name": token_name,
                    "first_seen": time.time(),
                    "event_type": kind,
                }
                self._save_json(self.seen_wallets, "seen_wallets.json")

            # === link X metadata and push keywords to meta store ===
            if x_data:
                self.seen_x_posts[key] = {
                    "timestamp": time.time(),
                    "keywords": x_data.get("keywords", []) or [],
                    "poster": x_data.get("poster"),
                    "x_text": x_data.get("text", ""),
                }
                self._save_json(self.seen_x_posts, "seen_x_posts.json")

                # also feed meta_keywords if available
                kw = [k for k in (x_data.get("keywords") or []) if isinstance(k, str)]
                if kw:
                    try:
                        from strategy.strategy_memory import update_meta_keywords
                        update_meta_keywords(token_address=(contract or token_name), keywords=kw)
                    except Exception:
                        pass

            # === record trace ===
            self.record_signal({
                "source": source,
                "signature": signature,
                "program_id": program_id,
                "slot": slot,
                "wallets": wallet_list,
                "tokens": token_list,
                "timestamp": timestamp,
                "logs": logs,
                "contract": contract,
                "token_name": token_name,
            })

            # === optional enrich (only if we have a contract) ===
            if contract:
                with contextlib.suppress(Exception):
                    self._enrich_token_profile(contract)

        except Exception as e:
            logging.warning(f"[Librarian] Error ingesting stream event: {e}")

    async def get_token(self, token: str) -> Optional[TokenRecord]:
        async with self._lock:
            return self._tokens.get(token)

    async def get_wallet(self, wallet: str) -> Optional[WalletRecord]:
        async with self._lock:
            return self._wallets.get(wallet)

    async def get_top_tokens(self, limit: int = 20, by: str = "activity") -> List[TokenRecord]:
        async with self._lock:
            if by == "activity":
                items = sorted(self._tokens.values(), key=lambda x: x.last_ts, reverse=True)
            elif by == "score":
                items = sorted(
                    self._tokens.values(),
                    key=lambda x: (x.scores[-1]["final_score"] if x.scores else 0),
                    reverse=True,
                )
            else:
                items = sorted(self._tokens.values(), key=lambda x: x.last_ts, reverse=True)
            return items[:limit]

    async def get_active_wallets(self, limit: int = 20) -> List[WalletRecord]:
        async with self._lock:
            items = sorted(self._wallets.values(), key=lambda x: x.last_ts, reverse=True)
            return items[:limit]

    async def get_signals_for_token(self, token: str, limit: int = 100) -> List[dict]:
        """Return mixed signals/events indexed for a given token."""
        async with self._lock:
            rec = self._tokens.get(token)
            if not rec:
                return []
            return list(rec.events)[-limit:]

    async def get_signals_for_wallet(self, wallet: str, limit: int = 100) -> List[dict]:
        async with self._lock:
            rec = self._wallets.get(wallet)
            if not rec:
                return []
            return list(rec.events)[-limit:]

    async def stats(self) -> dict:
        async with self._lock:
            return {
                "tokens": len(self._tokens),
                "wallets": len(self._wallets),
                "events_by_type": {k: len(v) for k, v in self._events_by_type.items()},
                "tracked_files": len(self._file_offsets),
                "last_status_beat": self._last_status_beat,
            }

    async def _initial_scan(self):
        for name, root in JSONL_SOURCES.items():
            if not root.exists():
                continue
            for fpath in sorted(root.rglob("*.jsonl")):
                self._file_offsets.setdefault(fpath, 0)
        await self._scan_all_files()

    async def _disk_scan_loop(self):
        while True:
            try:
                await self._scan_all_files()
            except Exception as e:
                logging.warning(f"[Librarian] Disk scan error: {e}")
            await asyncio.sleep(DISK_SCAN_INTERVAL_SEC)

    async def _status_loop(self):
        while True:
            try:
                update_status("data_librarian")
                self._last_status_beat = time.time()
            except Exception as e:
                logging.debug(f"[Librarian] status loop warning: {e}")
            await asyncio.sleep(STATUS_HEARTBEAT_SECONDS)

    async def _scan_all_files(self):
        for name, root in JSONL_SOURCES.items():
            if not root.exists():
                continue
            for fpath in sorted(root.rglob("*.jsonl")):
                await self._tail_jsonl_file(fpath, name)

    async def _tail_jsonl_file(self, fpath: Path, logical_source: str):
        """Read new lines since last offset, index events."""
        try:
            last_off = self._file_offsets.get(fpath, 0)
            with fpath.open("rb") as f:
                f.seek(last_off)
                while True:
                    line = f.readline()
                    if not line:
                        break
                    self._file_offsets[fpath] = f.tell()
                    try:
                        raw = json.loads(line.decode("utf-8"))
                    except Exception:
                        continue
                    await self._normalize_and_index(raw, logical_source)
        except FileNotFoundError:
            self._file_offsets.pop(fpath, None)
        except Exception as e:
            logging.warning(f"[Librarian] tail_jsonl_file error {fpath}: {e}")

    async def _normalize_and_index(self, raw: dict, logical_source: str):
        """
        Accept *anything* that looks like: {"ts": ..., "type": ..., "payload": {...}}
        If not, normalize best-effort.
        """
        if not isinstance(raw, dict):
            return

        ts = raw.get("ts") or raw.get("timestamp") or time.time()
        event_type = raw.get("type") or logical_source
        payload = raw.get("payload") or raw

        ev = {"ts": ts, "type": event_type, "payload": payload, "_src": logical_source}

        async with self._lock:
            self._events_by_type[event_type].append(ev)

            token = _find_token(payload)
            wallet = _find_wallet(payload)

            if event_type in ("scoring", "snipe_score", "trade_score"):
                self._handle_scoring_event(payload, ev, token)
                self._counters["events_ingested"] += 1
                if event_type in ("solana_log", "stream_event", "logsSubscribe"):
                    self._counters["stream_events"] += 1

            if event_type in ("trade", "buy", "sell", "auto_sell_result", "trade_result"):
                self._handle_trade_event(payload, ev, token, wallet)
                self._counters["events_ingested"] += 1
                if event_type in ("solana_log", "stream_event", "logsSubscribe"):
                    self._counters["stream_events"] += 1

            if event_type in ("wallet_event", "wallet_cluster", "wallet_overlap", "wallet_signal"):
                self._handle_wallet_event(payload, ev, token, wallet)
                self._counters["events_ingested"] += 1
                if event_type in ("solana_log", "stream_event", "logsSubscribe"):
                    self._counters["stream_events"] += 1

            if event_type in ("chart_pattern", "ohlcv_update", "trend_eval"):
                self._handle_chart_event(payload, ev, token)
                self._counters["events_ingested"] += 1
                if event_type in ("solana_log", "stream_event", "logsSubscribe"):
                    self._counters["stream_events"] += 1

            if token:
                self._index_token_event(token, ev)
            if wallet:
                self._index_wallet_event(wallet, ev)

    def get_token_summary(self, token: str) -> dict:
        """
        Return a lightweight summary of a token’s known memory profile.

        Includes tags, flags, score, and high-level traits for dashboards or quick checks.
        """
        token_data = self.token_memory.get(token, {})

        return {
            "token": token,
            "score": token_data.get("score", 0),
            "tags": list(token_data.get("tags", [])),
            "flags": list(token_data.get("flags", [])),
            "meta_theme": token_data.get("meta_theme", None),
            "created": token_data.get("created", None)
        }

    def _handle_scoring_event(self, payload: dict, ev: dict, token: Optional[str]):
        if not token:
            return
        rec = self._tokens.setdefault(token, TokenRecord(token=token))
        score_obj = {
            "ts": ev["ts"],
            "final_score": payload.get("final_score") or payload.get("score") or 0.0,
            "engine": payload.get("_scoring_engine"),
            "raw": payload,
        }
        rec.scores.append(score_obj)
        rec.last_ts = ev["ts"]
        src = payload.get("source") or ev.get("_src")
        if src:
            rec.scanners.add(src)

    def _handle_trade_event(self, payload: dict, ev: dict, token: Optional[str], wallet: Optional[str]):
        if token:
            rec = self._tokens.setdefault(token, TokenRecord(token=token))
            rec.tags.add("traded")
            rec.last_ts = ev["ts"]
        if wallet:
            wrec = self._wallets.setdefault(wallet, WalletRecord(wallet=wallet))
            wrec.events.append(ev)
            wrec.last_ts = ev["ts"]

    def _handle_wallet_event(self, payload: dict, ev: dict, token: Optional[str], wallet: Optional[str]):
        if wallet:
            wrec = self._wallets.setdefault(wallet, WalletRecord(wallet=wallet))
            wrec.events.append(ev)
            wrec.last_ts = ev["ts"]
        if token:
            trec = self._tokens.setdefault(token, TokenRecord(token=token))
            trec.last_ts = ev["ts"]
            src = payload.get("source") or ev.get("_src")
            if src:
                trec.scanners.add(src)

    def _handle_chart_event(self, payload: dict, ev: dict, token: Optional[str]):
        if not token:
            return
        rec = self._tokens.setdefault(token, TokenRecord(token=token))
        rec.chart = {
            "pattern": payload.get("pattern"),
            "confidence": payload.get("confidence"),
            "trend": payload.get("trend"),
            "timing": payload.get("timing"),
            "recent_price": payload.get("recent_price"),
            "volume": payload.get("volume"),
            "last_ts": ev["ts"],
        }
        rec.last_ts = ev["ts"]

    def trim_token_history(self, max_entries: int = 500, max_age_days: int = None):
        """
        Trim token history by max entries or age.
        - If max_entries is set, keep only that many recent events.
        - If max_age_days is set, remove events older than that.
        """
        try:
            history_store = getattr(self, "token_history_store", {})
            if not isinstance(history_store, dict):
                return

            now = datetime.utcnow().timestamp()
            max_age_seconds = max_age_days * 86400 if max_age_days else None

            for token, events in history_store.items():
                if max_age_seconds:
                    events = [e for e in events if isinstance(e, dict) and now - e.get("timestamp", now) <= max_age_seconds]

                if isinstance(events, list) and len(events) > max_entries:
                    events = events[-max_entries:]

                history_store[token] = events

        except Exception as e:
            import logging
            logging.warning(f"[DataLibrarian] Failed to trim token history: {e}")

    def _index_token_event(self, token: str, ev: dict):
        rec = self._tokens.setdefault(token, TokenRecord(token=token))
        rec.events.append(ev)
        rec.last_ts = max(rec.last_ts, ev["ts"])
        tag = ev["payload"].get("tag") or ev["payload"].get("result")
        if tag:
            rec.tags.add(str(tag))
        src = ev["payload"].get("source") or ev.get("_src")
        if src:
            rec.scanners.add(src)

        meta = ev["payload"].get("metadata") or ev["payload"].get("meta")
        if isinstance(meta, dict) and meta:
            rec.meta.update(meta)

    def _index_wallet_event(self, wallet: str, ev: dict):
        rec = self._wallets.setdefault(wallet, WalletRecord(wallet=wallet))
        rec.events.append(ev)
        rec.last_ts = max(rec.last_ts, ev["ts"])
        tag = ev["payload"].get("tag")
        if tag:
            rec.tags.add(str(tag))
        cluster_id = ev["payload"].get("cluster_id")
        if cluster_id:
            rec.clusters.add(str(cluster_id))
        meta = ev["payload"].get("metadata") or ev["payload"].get("meta")
        if isinstance(meta, dict) and meta:
            rec.meta.update(meta)

    def get_meta_keywords(self, limit: int = 20) -> List[str]:
        """
        Legacy shim for Telegram debug commands.
        Safely returns top keywords by count from memory.
        """
        raw = self._memory_store.get("keyword_memory", {})
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

    def persist_all(self):
        """
        Flush all in-memory data to disk (if needed).
        Extend this to persist caches, memory, or logs.
        """
        try:
            if self.token_memory:
                self.save_token_memory()

            if self.wallet_memory:
                self.save_wallet_memory()

            if self.strategy_memory:
                self.save_strategy_memory()

            log_event("[Librarian] 🧠 All memory persisted successfully.")
        except Exception as e:
            logging.warning(f"[Librarian] Failed to persist memory: {e}")

    async def ingest_chat_messages(self, messages: list[dict]) -> None:
        """
        Primary path for Telegram loader. Accepts a list of normalized chat messages.
        Deduplicates by (chat_id, message_id).
        """
        import gzip
        import json
        import os

        from utils.logger import log_event

        os.makedirs("/home/ubuntu/nyx/runtime/library/chats", exist_ok=True)
        path = "/home/ubuntu/nyx/runtime/library/chats/chat_messages.jsonl.gz"

        seen_keys = set()
        deduped = []
        for m in messages:
            key = (m.get("chat_id"), m.get("message_id"))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(m)

        if not deduped:
            return

        with gzip.open(path, "ab") as f:
            for m in deduped:
                f.write((json.dumps(m, ensure_ascii=False) + "\n").encode("utf-8"))

        bucket = self.runtime.get("chat_messages")
        if bucket is None:
            bucket = self.runtime["chat_messages"] = []
        bucket.extend(deduped)

        log_event(f"[Librarian] Ingested {len(deduped)} new chat messages → {path}")

    async def ingest_records(self, kind: str, records: list[dict]) -> None:
        """
        Generic bulk-ingest fallback. For this pipeline we handle kind == 'chat_message'.
        """
        if kind == "chat_message":
            await self.ingest_chat_messages(records)
            return
        raise RuntimeError(f"Unsupported ingest kind: {kind}")

    async def _library_maintenance_loop(self) -> None:
        """
        Background maintenance for the Librarian.
        - Rotates large *.jsonl.gz files
        - Compacts/dedupes chat_messages.jsonl.gz
        - Rebuilds a light in-memory index
        - Writes a health snapshot file
        - Prunes temp/old files
        """
        base_dir = Path("/home/ubuntu/nyx/runtime/library")
        chats_dir = base_dir / "chats"
        base_dir.mkdir(parents=True, exist_ok=True)
        chats_dir.mkdir(parents=True, exist_ok=True)

        # tunables
        MAX_FILE_MB = 256         # rotate when a file exceeds this size
        KEEP_ROTATIONS = 5        # keep this many rotated copies per file
        COMPACT_EVERY_SEC = 3 * 3600   # compact at most once per 3 hours
        LIGHT_MAINT_EVERY_SEC = 600    # rotation/health every 10 minutes
        RETAIN_DAYS = 90          # drop chat lines older than this (if timestamps present)

        if not hasattr(self, "_maint_lock"):
            self._maint_lock = asyncio.Lock()
        if not hasattr(self, "_last_compact_ts"):
            self._last_compact_ts = 0.0
        if not hasattr(self, "_last_light_ts"):
            self._last_light_ts = 0.0

        try:
            n = librarian.prune_memory()
            if n:
                log_event(f"[Maintenance] Pruned {n} memory entries")
        except Exception as e:
            logging.warning(f"[Maintenance] Error during upkeep: {e}")

        async def _rotate_if_large(path: Path):
            try:
                if not path.exists():
                    return
                size_mb = path.stat().st_size / (1024 * 1024)
                if size_mb < MAX_FILE_MB:
                    return
                ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
                rotated = path.with_name(f"{path.stem}.{ts}{path.suffix}")
                shutil.move(str(path), str(rotated))
                family = sorted(path.parent.glob(f"{path.stem}.*{path.suffix}"), reverse=True)
                for old in family[KEEP_ROTATIONS:]:
                    try:
                        old.unlink(missing_ok=True)
                    except Exception:
                        pass
                logging.info(f"[Librarian] Rotated {path.name} -> {rotated.name} ({size_mb:.1f}MB)")
            except Exception as e:
                logging.warning(f"[Librarian] rotation failed for {path}: {e}")

        def _iter_jsonl_gz(path: Path):
            if not path.exists():
                return
            with gzip.open(path, "rb") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        yield json.loads(line)
                    except Exception:
                        try:
                            yield json.loads(line.decode("utf-8"))
                        except Exception:
                            continue

        async def _compact_chat_messages():
            """Dedupe by (chat_id, message_id), drop very old lines, rewrite compacted file."""
            target = chats_dir / "chat_messages.jsonl.gz"
            if not target.exists():
                return
            tmp = target.with_suffix(".tmp")
            seen = set()
            cutoff = datetime.utcnow() - timedelta(days=RETAIN_DAYS)
            kept = 0
            dropped_dupe = 0
            dropped_old = 0

            try:
                with gzip.open(tmp, "wb") as out:
                    for obj in _iter_jsonl_gz(target):
                        cid = obj.get("chat_id")
                        mid = obj.get("message_id")
                        if cid is None or mid is None:
                            continue
                        key = (cid, mid)
                        if key in seen:
                            dropped_dupe += 1
                            continue
                        ts = obj.get("timestamp") or obj.get("created_at")
                        if ts:
                            try:
                                if isinstance(ts, (int, float)):
                                    dt = datetime.utcfromtimestamp(float(ts))
                                else:
                                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
                                if dt < cutoff:
                                    dropped_old += 1
                                    continue
                            except Exception:
                                pass
                        seen.add(key)
                        out.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                        kept += 1

                shutil.move(str(tmp), str(target))
                logging.info(f"[Librarian] Compacted chat_messages: kept={kept} dupe={dropped_dupe} old={dropped_old}")
            except Exception as e:
                try:
                    if tmp.exists():
                        tmp.unlink(missing_ok=True)
                except Exception:
                    pass
                logging.warning(f"[Librarian] compact failed: {e}")

            try:
                index = {}
                for obj in _iter_jsonl_gz(target):
                    cid = obj.get("chat_id")
                    mid = obj.get("message_id")
                    if cid is None or mid is None:
                        continue
                    cur = index.get(cid)
                    if cur is None or (isinstance(mid, int) and mid > cur.get("message_id", -1)):
                        index[cid] = {"message_id": mid, "timestamp": obj.get("timestamp")}
                bucket = self.runtime.get("chat_messages_index")
                if bucket is None:
                    self.runtime["chat_messages_index"] = index
                else:
                    bucket.clear()
                    bucket.update(index)
            except Exception as e:
                logging.warning(f"[Librarian] index rebuild failed: {e}")

        def _write_health_snapshot():
            try:
                snap = {
                    "ts": datetime.utcnow().isoformat(),
                    "chat_messages_size_mb": round(((chats_dir / "chat_messages.jsonl.gz").stat().st_size if (chats_dir / "chat_messages.jsonl.gz").exists() else 0) / (1024*1024), 2),
                    "rotations_keep": KEEP_ROTATIONS,
                    "retain_days": RETAIN_DAYS,
                }
                (base_dir / ".library_health.json").write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                logging.warning(f"[Librarian] health snapshot failed: {e}")

        def _prune_temps():
            try:
                for p in list(chats_dir.glob("*.tmp")) + list(base_dir.glob("*.tmp")):
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass
            except Exception:
                pass

        while True:
            try:
                async with self._maint_lock:
                    now = time.time()

                    if now - self._last_light_ts >= LIGHT_MAINT_EVERY_SEC:
                        await _rotate_if_large(chats_dir / "chat_messages.jsonl.gz")
                        for p in base_dir.rglob("*.jsonl.gz"):
                            if p.name == "chat_messages.jsonl.gz":
                                continue
                            await _rotate_if_large(p)
                        _write_health_snapshot()
                        _prune_temps()
                        self._last_light_ts = now

                    if now - self._last_compact_ts >= COMPACT_EVERY_SEC:
                        await _compact_chat_messages()
                        self._last_compact_ts = now

            except Exception as e:
                logging.warning(f"[Librarian] maintenance error: {e}")

            await asyncio.sleep(30)


    def _x_root(self):
        from pathlib import Path
        import os
        return Path(os.environ.get('NYX_ROOT', '/home/ubuntu/nyx'))

    def _x_read_json(self, path):
        try:
            import json
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return {} if str(path).endswith('.json') else None

    def _x_iter_jsonl(self, path):
        items = []
        try:
            with path.open('r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        import json
                        items.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            return []
        return items

    # ---- Wallets & clusters (additional sources) ----
    def get_tracked_wallets(self) -> dict:
        p = self._x_root() / 'runtime' / 'data' / 'tracked_wallets.json'
        return self._x_read_json(p) or {}

    def get_wallet_entry_times(self) -> dict:
        p = self._x_root() / 'runtime' / 'data' / 'wallet_entry_times.json'
        return self._x_read_json(p) or {}

    def get_wallet_history(self) -> dict | list:
        p = self._x_root() / 'runtime' / 'data' / 'wallet_history'
        if p.exists() and p.is_dir():
            out = {}
            for f in p.glob('*.json'):
                out[f.stem] = self._x_read_json(f)
            return out
        return {}

    def get_cabal_clusters(self) -> dict:
        p = self._x_root() / 'runtime' / 'cabal' / 'clusters.json'
        return self._x_read_json(p) or {}

    # ---- Risk & rules ----
    def get_honeypot_signatures(self) -> dict:
        p = self._x_root() / 'runtime' / 'data' / 'honeypot_signatures.json'
        return self._x_read_json(p) or {}

    def get_blacklisted_tokens(self) -> dict | list:
        p = self._x_root() / 'runtime' / 'memory' / 'blacklisted_tokens.json'
        data = self._x_read_json(p)
        return data or []

    # ---- Strategy & reinforcement memory ----
    def get_strategy_snapshots(self) -> dict:
        p = self._x_root() / 'runtime' / 'data' / 'strategy_snapshots'
        out = {}
        if p.exists() and p.is_dir():
            for f in p.glob('*.json'):
                out[f.stem] = self._x_read_json(f)
        return out

    def get_reinforcement_state(self) -> dict:
        root = self._x_root() / 'runtime' / 'logs'
        return {
            'history': self._x_read_json(root / 'reinforcement_history.json'),
            'memory':  self._x_read_json(root / 'reinforcement_memory.json'),
            'weights': self._x_read_json(root / 'reinforcement_weights.json'),
        }

    def get_dev_reputation(self) -> dict:
        p = self._x_root() / 'runtime' / 'data' / 'dev_reputation.json'
        return self._x_read_json(p) or {}

    def get_causal_predictor_state(self) -> dict:
        p = self._x_root() / 'runtime' / 'data' / 'causal_predictor_state.json'
        return self._x_read_json(p) or {}

    def get_snipe_block(self) -> dict:
        p = self._x_root() / 'runtime' / 'data' / 'snipe_block.json'
        return self._x_read_json(p) or {}

    # ---- Social/X/TG ----
    def get_x_mentions(self) -> dict | list:
        p = self._x_root() / 'runtime' / 'logs' / 'x_mentions.json'
        data = self._x_read_json(p)
        return data if isinstance(data, (list, dict)) else []

    def get_x_signal_log(self) -> dict | list:
        p = self._x_root() / 'runtime' / 'logs' / 'x_signal_log.json'
        data = self._x_read_json(p)
        return data if isinstance(data, (list, dict)) else []

    def get_tg_join_attempts(self) -> dict | list:
        p = self._x_root() / 'runtime' / 'data' / 'tg_join_attempts.json'
        data = self._x_read_json(p)
        return data or []

    def get_tg_join_failures(self) -> dict | list:
        p = self._x_root() / 'runtime' / 'data' / 'tg_join_failures.json'
        data = self._x_read_json(p)
        return data or []

    # ---- Token / trade history (additional files) ----
    def get_token_history(self) -> dict:
        p = self._x_root() / 'runtime' / 'data' / 'token' / 'token_history.json'
        return self._x_read_json(p) or {}

    def get_trade_history_file(self) -> dict | list:
        p = self._x_root() / 'runtime' / 'data' / 'trade_history.json'
        data = self._x_read_json(p)
        return data or []

    def get_signal_logs(self) -> list[dict]:
        p = self._x_root() / 'runtime' / 'logs' / 'signal_logs.jsonl'
        return self._x_iter_jsonl(p)

    # ---- Learning / library ----
    def get_learning_snapshots(self) -> dict:
        base = self._x_root() / 'runtime' / 'learning'
        out = {}
        if base.exists():
            for f in base.rglob('*.json'):
                out[str(f.relative_to(base))] = self._x_read_json(f)
        return out

    def get_library_bandit(self) -> dict:
        base = self._x_root() / 'runtime' / 'library' / 'bandit'
        out = {}
        if base.exists():
            for f in base.rglob('*.json'):
                out[str(f.relative_to(base))] = self._x_read_json(f)
        return out

    # ---- Keyword/theme memory ----
    def get_theme_memory(self) -> dict:
        p = self._x_root() / 'runtime' / 'logs' / 'theme_memory.json'
        return self._x_read_json(p) or {}

    def get_keywords_file(self) -> dict:
        p = self._x_root() / 'runtime' / 'logs' / 'meta_keywords.json'
        return self._x_read_json(p) or {}

    # ---- Context enrichment (non-breaking) ----
    def enrich_context_with_extras(self, ctx: dict) -> dict:
        try:
            ctx = dict(ctx or {})
            ctx.setdefault('wallets', {}).update({
                'tracked': self.get_tracked_wallets(),
                'entry_times': self.get_wallet_entry_times(),
                'history': self.get_wallet_history(),
                'cabal_clusters': self.get_cabal_clusters(),
            })
            ctx.setdefault('risk', {}).update({
                'honeypot': self.get_honeypot_signatures(),
                'blacklisted_tokens': self.get_blacklisted_tokens(),
            })
            ctx.setdefault('alpha', {}).update({
                'strategy_snapshots': self.get_strategy_snapshots(),
                'reinforcement': self.get_reinforcement_state(),
                'dev_reputation': self.get_dev_reputation(),
                'causal_predictor': self.get_causal_predictor_state(),
                'snipe_block': self.get_snipe_block(),
                'theme_memory': self.get_theme_memory(),
                'library_bandit': self.get_library_bandit(),
            })
            ctx.setdefault('social', {}).update({
                'x_mentions': self.get_x_mentions(),
                'x_signal_log': self.get_x_signal_log(),
                'tg_join_attempts': self.get_tg_join_attempts(),
                'tg_join_failures': self.get_tg_join_failures(),
            })
            ctx.setdefault('trades', {}).update({
                'file_trade_history': self.get_trade_history_file(),
            })
            ctx.setdefault('keywords', {}).update(self.get_keywords_file())
            ctx.setdefault('learning', self.get_learning_snapshots())
        except Exception:
            pass
        return ctx

    # === Build context snapshot for a token ===
    async def build_context(self, token: str) -> dict:
        """
        Build a rich context dictionary about a token for scoring, evaluation, or analysis.

        Pulls from:
        - token memory
        - wallet traits
        - social memory
        - tag and flag systems
        - persisted recall cache
        """
        from utils.token_utils import normalize_token_address
        token = normalize_token_address(token)

        context = {
            "token": token,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": {},
            "chart": {},
            "wallets": {},
            "volume": {},
            "social": {},
            "nft": {},
            "price": 0.0,
            "tags": [],
            "risk_flags": [],
            "flags": [],
            "wallet_traits": [],
            "x_flags": [],
            "score": 0,
            "meta_theme": None,
            "created": None
        }

        try:
            # Recall persisted snapshot
            saved = recall(f"token:{token}", default={})

            # Merge stored values
            context["metadata"]     = saved.get("metadata", {})
            context["tags"]         = saved.get("tags", [])
            context["chart"]        = saved.get("chart_data", {})
            context["wallets"]      = saved.get("wallets", {})
            context["social"]       = saved.get("social", {})
            context["volume"]       = saved.get("volume", {})
            context["nft"]          = saved.get("nft", {})
            context["risk_flags"]   = saved.get("risk_flags", [])

            # Pull live memory enrichments
            token_data = self.token_memory.get(token, {})
            associated_wallets = token_data.get("wallets", [])
            associated_x        = token_data.get("x_mentions", [])

            wallet_traits = set()
            for w in associated_wallets:
                wallet_info = self.wallet_memory.get(w, {})
                wallet_traits.update(wallet_info.get("traits", []))

            x_flags = set()
            for x in associated_x:
                x_info = self.x_memory.get(x.lower(), {})
                x_flags.update(x_info.get("reasons", []))

            context["flags"]         = list(token_data.get("flags", []))
            context["wallet_traits"] = list(wallet_traits)
            context["x_flags"]       = list(x_flags)
            context["score"]         = token_data.get("score", 0)
            context["meta_theme"]    = token_data.get("meta_theme", None)
            context["created"]       = token_data.get("created", None)

            log_event(f"📚 Context built for token {token}")

        except Exception as e:
            logging.warning(f"[Librarian] Failed to build context for {token}: {e}")

        return self.enrich_context_with_extras(context)



def _lowerize(x):
    if isinstance(x, str): return x.lower()
    if isinstance(x, (list, tuple, set)): return [str(i).lower() for i in x]
    return str(x).lower()

def _extract_topics(payload: dict) -> set:
    text_bits = []
    for k, v in payload.items():
        if isinstance(v, (str, int, float)): text_bits.append(str(v))
        elif isinstance(v, (list, tuple, set)):
            text_bits.extend([str(i) for i in v])
        elif isinstance(v, dict):
            for vv in v.values():
                if isinstance(vv, (str, int, float)): text_bits.append(str(vv))
    blob = " ".join(_lowerize(text_bits))
    hits = set()
    for genre, keys in GENRES.items():
        for kw in keys:
            if kw in blob:
                hits.add(kw)
    return hits

def _classify_genre(payload: dict) -> str:
    topics = _extract_topics(payload)
    order = ["risk","profits","losses","listings","wallets","charts","social","math","memes"]
    for g in order:
        if any(kw in topics for kw in GENRES[g]):
            return g
    return "misc"

def _ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def _library_path_for(genre: str, ts: float) -> Path:
    dt = datetime.utcfromtimestamp(ts)
    base = LIBRARY_ROOT / genre / f"{dt.year:04d}" / f"{dt.month:02d}"
    _ensure_dir(base)
    return base / f"{dt.day:02d}.jsonl"

async def archive_to_library(self, ev: dict):
    """
    Normalize, classify, write to /runtime/library/<genre>/YYYY/MM/DD.jsonl
    Also updates in-memory indices for fast queries.
    """
    try:
        ts = ev.get("ts") or ev.get("timestamp") or datetime.utcnow().timestamp()
        etype = ev.get("type") or ev.get("payload", {}).get("type") or "event"
        payload = ev.get("payload") or ev

        token = payload.get("token") or payload.get("mint") or payload.get("token_address")
        wallet = payload.get("wallet") or payload.get("wallet_address") or payload.get("owner")

        genre = _classify_genre(payload)
        topics = sorted(list(_extract_topics(payload)))

        line = {
            "ts": ts,
            "type": etype,
            "genre": genre,
            "topics": topics,
            "token": token,
            "wallet": wallet,
            "payload": payload,
        }

        fpath = _library_path_for(genre, ts)
        with open(fpath, "a") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

        lib_ix = self._memory_store.setdefault("_library_index", {
            "by_genre": {},
            "by_topic": {},
            "by_token": {},
            "by_wallet": {},
            "wallet_class": {},
        })

        arr = lib_ix["by_genre"].setdefault(genre, [])
        arr.append({"ts": ts, "type": etype, "token": token, "wallet": wallet, "topics": topics})
        if len(arr) > 5000: del arr[: len(arr) - 5000]

        # topics
        for t in topics:
            ta = lib_ix["by_topic"].setdefault(t, [])
            ta.append({"ts": ts, "type": etype, "token": token, "wallet": wallet, "genre": genre})
            if len(ta) > 5000: del ta[: len(ta) - 5000]

        # token
        if token:
            tt = lib_ix["by_token"].setdefault(token, [])
            tt.append({"ts": ts, "type": etype, "genre": genre, "topics": topics, "wallet": wallet})
            if len(tt) > 5000: del tt[: len(tt) - 5000]

        # wallet
        if wallet:
            ww = lib_ix["by_wallet"].setdefault(wallet, [])
            ww.append({"ts": ts, "type": etype, "genre": genre, "topics": topics, "token": token})
            if len(ww) > 5000: del ww[: len(ww) - 5000]

        # Optional: quick heuristics for wallet class (you can override elsewhere)
        if genre == "profits" and wallet:
            if lib_ix["wallet_class"].get(wallet) != "bad":
                lib_ix["wallet_class"][wallet] = "good"
        if genre == "losses" and wallet:
            if lib_ix["wallet_class"].get(wallet) != "good":
                lib_ix["wallet_class"][wallet] = "bad"

        # Persist a light heartbeat periodically
        if int(ts) % 300 == 0:
            self.save_memory()

    except Exception as e:
        import logging
        logging.warning(f"[Librarian] archive_to_library error: {e}")

def query_by_genre(self, genre: str, limit: int = 200) -> list:
    idx = self._memory_store.get("_library_index", {}).get("by_genre", {}).get(genre, [])
    return idx[-limit:]

def query_by_topic(self, topic: str, limit: int = 200) -> list:
    idx = self._memory_store.get("_library_index", {}).get("by_topic", {}).get(topic, [])
    return idx[-limit:]

def get_wallet_class(self, wallet: str) -> str:
    return self._memory_store.get("_library_index", {}).get("wallet_class", {}).get(wallet, "")

# ------------- helpers --------------

def _find_token(payload: dict) -> Optional[str]:
    for key in ("token", "token_address", "mint", "address"):
        v = payload.get(key)
        if isinstance(v, str) and len(v) > 20:  # cheap-ish solana-ish heuristic
            return v
    return None

def _find_wallet(payload: dict) -> Optional[str]:
    for key in ("wallet", "wallet_address", "owner", "from", "to"):
        v = payload.get(key)
        if isinstance(v, str) and len(v) > 20:
            return v
    return None


# === Build context snapshot for a token ===
async def build_context(self, token: str) -> dict:
    """
    Build a rich context dictionary about a token for scoring, evaluation, or analysis.

    Pulls from:
    - token memory
    - wallet traits
    - social memory
    - tag and flag systems
    - persisted recall cache
    """
    from utils.token_utils import normalize_token_address
    token = normalize_token_address(token)

    context = {
        "token": token,
        "timestamp": datetime.utcnow().isoformat(),
        "metadata": {},
        "chart": {},
        "wallets": {},
        "volume": {},
        "social": {},
        "nft": {},
        "price": 0.0,
        "tags": [],
        "risk_flags": [],
        "flags": [],
        "wallet_traits": [],
        "x_flags": [],
        "score": 0,
        "meta_theme": None,
        "created": None
    }

    try:
        # Recall persisted snapshot
        saved = recall(f"token:{token}", default={})

        # Merge stored values
        context["metadata"]     = saved.get("metadata", {})
        context["tags"]         = saved.get("tags", [])
        context["chart"]        = saved.get("chart_data", {})
        context["wallets"]      = saved.get("wallets", {})
        context["social"]       = saved.get("social", {})
        context["volume"]       = saved.get("volume", {})
        context["nft"]          = saved.get("nft", {})
        context["risk_flags"]   = saved.get("risk_flags", [])

        # Pull live memory enrichments
        token_data = self.token_memory.get(token, {})
        associated_wallets = token_data.get("wallets", [])
        associated_x        = token_data.get("x_mentions", [])

        wallet_traits = set()
        for w in associated_wallets:
            wallet_info = self.wallet_memory.get(w, {})
            wallet_traits.update(wallet_info.get("traits", []))

        x_flags = set()
        for x in associated_x:
            x_info = self.x_memory.get(x.lower(), {})
            x_flags.update(x_info.get("reasons", []))

        context["flags"]         = list(token_data.get("flags", []))
        context["wallet_traits"] = list(wallet_traits)
        context["x_flags"]       = list(x_flags)
        context["score"]         = token_data.get("score", 0)
        context["meta_theme"]    = token_data.get("meta_theme", None)
        context["created"]       = token_data.get("created", None)

        log_event(f"📚 Context built for token {token}")

    except Exception as e:
        logging.warning(f"[Librarian] Failed to build context for {token}: {e}")

    return self.enrich_context_with_extras(context)


def get_memory(self, key: str, default=None):
    """
    Proxy to persistent memory recall.
    """
    return mm_get_memory(key, default)

def set_memory(self, key: str, value: Any):
    """
    Proxy to persistent memory setter.
    """
    mm_set_memory(key, value)

def _safe_read_json_dict(path: str) -> dict:
    """
    Reads a JSON file that should contain an object and returns a dict.
    Handles cases where the file contains a JSON-encoded *string* of JSON,
    or any other invalid content.
    """
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

# -----------------------------------------------------------------------------
# Singleton & bootstrap
# -----------------------------------------------------------------------------
librarian = DataLibrarian()

async def run_librarian():
    await librarian.start()
