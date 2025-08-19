# feature_store.py
import asyncio
import gzip
import inspect
import json
import logging
import os
import time
import contextlib
import glob
import math

from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from core.live_config import config, save_config
from librarian.data_librarian import librarian
from utils.logger import log_event
from utils.service_status import update_status

FEATURE_STORE_DEFAULTS = {
    "path": "/nyx/runtime/library/feature_store",
    "gzip": True,
    "max_days": 30,                # retain N days on disk
    "flush_every": 200,            # how many events before a forced flush
    "max_file_size": 20_000,       # max events per shard file
    "sync_interval_sec": 5,        # background sync interval
    "wal_path": "/nyx/runtime/library/feature_store/_wal.jsonl",  # write-ahead log for crash safety
    "rolling_window_sec": 86_400,  # 24h rolling stats
}

EVENT_KINDS = {
    "trade",            # executed buy/sell
    "signal",           # pre-trade score/signal
    "decision",         # final action decision (snipe/ignore/trade/hold/etc)
    "pnl_snapshot",     # realized/unrealized PnL snapshots
    "wallet",           # wallet level feature stats
    "token",            # token level feature stats
    "strategy_weight",  # bandit reward logging
    "sentiment",        # nlp / social
    "volume",           # liquidity / volume snapshots
    "cortex",           # cortex fused features
}

def _now_iso() -> str:
    return datetime.utcnow().isoformat()

def _epoch() -> float:
    return time.time()

def _date_key(ts: float) -> str:
    return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")

def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

class RollingStats:
    """
    Simple rolling stats kept in-memory for the last N seconds.
    """
    def __init__(self, window_sec: int):
        self.window = window_sec
        self.samples: List[Tuple[float, float]] = []  # (ts, value)
        self.sum = 0.0
        self.count = 0

    def add(self, value: float, ts: Optional[float] = None):
        ts = ts or _epoch()
        self.samples.append((ts, value))
        self.sum += value
        self.count += 1
        self._trim(ts)

    def _trim(self, now_ts: float):
        cutoff = now_ts - self.window
        while self.samples and self.samples[0][0] < cutoff:
            ts_old, val_old = self.samples.pop(0)
            self.sum -= val_old
            self.count -= 1
            if self.count < 0:  # just in case
                self.count = 0
                self.sum = 0.0

    def mean(self) -> float:
        return self.sum / self.count if self.count else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "mean": self.mean(),
            "window_sec": self.window
        }

class FeatureStore:
    """
    Disk-backed, append-only, JSONL(+gzip) feature/event store with:
    - Write-ahead log (WAL) for crash-safety
    - Background flusher
    - Auto-pruning by age
    - Rolling (24h) quick stats for fast access
    """

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = {**FEATURE_STORE_DEFAULTS, **cfg}
        self.path = self.cfg["path"]
        self.wal_path = self.cfg["wal_path"]
        self.compress = bool(self.cfg["gzip"])
        self.flush_every = int(self.cfg["flush_every"])
        self.max_file_size = int(self.cfg["max_file_size"])
        self.max_days = int(self.cfg["max_days"])
        self.sync_interval = int(self.cfg["sync_interval_sec"])
        self.rolling = RollingStats(self.cfg["rolling_window_sec"])

        _ensure_dir(self.path)
        _ensure_dir(os.path.dirname(self.wal_path))

        # In-memory buffers
        self._buffer: List[Dict[str, Any]] = []
        self._buffer_lock = asyncio.Lock()
        self._writing = asyncio.Lock()

        # Current open file state
        self._current_file_handle = None
        self._current_file_path = None
        self._current_file_count = 0
        self._current_date_key = None

        self._background_task: Optional[asyncio.Task] = None
        self._started = False

        # Minimal indices & stats
        self._last_events: Dict[str, List[Dict[str, Any]]] = {k: [] for k in EVENT_KINDS}
        self._last_events_cap = 500  # keep recent in memory for fast queries

        # Recover from WAL if needed
        self._recover_wal()

    # ---------- Public API ----------

    async def start(self):
        if self._started:
            return
        self._started = True
        self._background_task = asyncio.create_task(self._background_loop())
        log_event("🗃️ FeatureStore started.")

    async def stop(self):
        try:
            if self._background_task:
                self._background_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._background_task
        finally:
            await self.flush(force=True)
            self._close_current_file()
            log_event("🗃️ FeatureStore stopped and flushed.")

    async def record_event(
        self,
        kind: str,
        payload: Dict[str, Any],
        tags: Optional[List[str]] = None,
        ts: Optional[float] = None
    ):
        """
        Generic event recorder; use the specialized helpers below when you can.
        """
        update_status("feature_store")

        if kind not in EVENT_KINDS:
            logging.warning(f"[FeatureStore] Unknown kind '{kind}', accepting anyway.")

        ts = ts or _epoch()
        event = {
            "kind": kind,
            "ts": ts,
            "iso": datetime.utcfromtimestamp(ts).isoformat(),
            "payload": payload,
            "tags": tags or []
        }

        # In-memory rolling stats (e.g., reward / score if present)
        value = None
        if "score" in payload and isinstance(payload["score"], (int, float)):
            value = float(payload["score"])
        elif "reward" in payload and isinstance(payload["reward"], (int, float)):
            value = float(payload["reward"])
        if value is not None:
            self.rolling.add(value, ts=ts)

        # Add to last N cache
        recents = self._last_events.setdefault(kind, [])
        recents.append(event)
        if len(recents) > self._last_events_cap:
            recents.pop(0)

        # Write to WAL immediately (durable)
        self._append_to_wal(event)

        # Buffer for batch flush
        async with self._buffer_lock:
            self._buffer.append(event)
            if len(self._buffer) >= self.flush_every:
                await self.flush(force=False)

    async def record_trade(
        self,
        token: str,
        side: str,
        score: float,
        pnl: float,
        strategy: str,
        wallet: str,
        features: Dict[str, Any],
        outcome: str
    ):
        await self.record_event(
            "trade",
            {
                "token": token,
                "side": side,
                "score": score,
                "pnl": pnl,
                "strategy": strategy,
                "wallet": wallet,
                "outcome": outcome,
                "features": features
            }
        )

    async def record_signal(
        self,
        token: str,
        source: str,
        score: float,
        confidence: float,
        reasons: List[str],
        features: Dict[str, Any]
    ):
        await self.record_event(
            "signal",
            {
                "token": token,
                "source": source,
                "score": score,
                "confidence": confidence,
                "reasons": reasons,
                "features": features
            }
        )

    async def record_decision(
        self,
        token: str,
        action: str,
        final_score: float,
        strategy: str,
        model_version: str,
        meta: Dict[str, Any]
    ):
        await self.record_event(
            "decision",
            {
                "token": token,
                "action": action,
                "final_score": final_score,
                "strategy": strategy,
                "model_version": model_version,
                "meta": meta
            }
        )

    async def record_strategy_weight(
        self,
        strategy: str,
        weight: float,
        reward: Optional[float] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        await self.record_event(
            "strategy_weight",
            {
                "strategy": strategy,
                "weight": weight,
                "reward": reward,
                "context": context or {}
            }
        )

    async def record_pnl_snapshot(
        self,
        wallet: str,
        realized: float,
        unrealized: float,
        open_positions: int
    ):
        await self.record_event(
            "pnl_snapshot",
            {
                "wallet": wallet,
                "realized": realized,
                "unrealized": unrealized,
                "open_positions": open_positions
            }
        )

    async def flush(self, force: bool = False):
        """
        Flushes in-memory buffer to the current day shard file.
        """
        async with self._writing:
            async with self._buffer_lock:
                if not self._buffer and not force:
                    return

                events = self._buffer
                self._buffer = []

            if not events and not force:
                return

            day_key = _date_key(events[-1]["ts"]) if events else _date_key(_epoch())
            self._ensure_shard(day_key)

            for ev in events:
                self._write_event_to_shard(ev)

            self._current_file_handle.flush()
            os.fsync(self._current_file_handle.fileno())

            # truncate WAL since safely persisted
            self._truncate_wal()

    def normalize_reward(pnl_pct: float, slip_bps: float = 0.0, hold_sec: float = 0.0) -> float:
        """
        Convert trade outcome to a bandit reward in [-1, 1].
        - pnl_pct: realized or marked %PnL (e.g., +3.2 means +3.2%)
        - slip_bps: execution slippage in basis points
        - hold_sec: optional time preference (small bonus for faster good outcomes)
        """
        base = math.tanh(float(pnl_pct) / 6.0)           # ±6% ≈ saturates
        slip_pen = min(abs(float(slip_bps)) / 100.0, 1.0) * 0.15
        time_bonus = 0.0
        if pnl_pct > 0 and hold_sec > 0:
            time_bonus = min(0.05, 3600.0 / (float(hold_sec) + 3600.0) * 0.05)
        return max(-1.0, min(1.0, base - slip_pen + time_bonus))

    def get_last_events(self, kind: str, n: int = 50) -> List[Dict[str, Any]]:
        rec = self._last_events.get(kind, [])
        return rec[-n:]

    def get_recent_rewards_by_strategy(self, strategy: str, horizon_sec: int = 86_400) -> List[float]:
        """
        Returns recent rewards for a given strategy (arm) within horizon_sec.
        Uses in-memory cache *and* scans recent shards on disk for durability across restarts.
        """
        now_ts = _epoch()
        cutoff = now_ts - int(horizon_sec)
        rewards: List[float] = []

        # A) in-memory (fast path)
        for ev in self.get_last_events("strategy_weight", n=self._last_events_cap):
            try:
                if ev.get("ts", 0) >= cutoff and ev["payload"].get("strategy") == strategy:
                    r = ev["payload"].get("reward")
                    if isinstance(r, (int, float)):
                        rewards.append(float(r))
            except Exception:
                continue

        # B) disk-backed (resilient across restarts)
        for path in self._recent_shards(cutoff):
            for ev in self._iter_events_from_file(path):
                try:
                    if ev.get("ts", 0) < cutoff:
                        continue
                    if ev.get("kind") != "strategy_weight":
                        continue
                    pl = ev.get("payload") or {}
                    if pl.get("strategy") != strategy:
                        continue
                    r = pl.get("reward")
                    if isinstance(r, (int, float)):
                        rewards.append(float(r))
                except Exception:
                    continue

        # bound result size
        return rewards[-1000:]

    async def refresh_weights(self):
        """
        Adjust strategy weights based on recent reward data.
        Robust against odd shapes from get_rolling_stats().
        """
        try:
            stats = self.get_rolling_stats()

            # If someone made get_rolling_stats async later, handle both cases
            if inspect.isawaitable(stats):
                stats = await stats

            if stats is None:
                return

            normalized: Dict[str, Dict[str, float]] = {}

            # Case A: already a dict mapping -> dict
            if isinstance(stats, dict):
                for strat, data in stats.items():
                    # allow number-only data (treat as average_reward)
                    if isinstance(data, (int, float)):
                        normalized[str(strat)] = {"average_reward": float(data), "pnl": 0.0}
                    elif isinstance(data, dict):
                        normalized[str(strat)] = {
                            "average_reward": float((data.get("average_reward", 0) or 0)),
                            "pnl": float((data.get("pnl", 0) or 0)),
                        }
                    else:
                        # skip unknown shapes
                        continue

            # Case B: list/iterable of dicts like [{"strategy": "X", "average_reward": .., "pnl": ..}, ...]
            elif isinstance(stats, Iterable) and not isinstance(stats, (str, bytes)):
                for row in stats:
                    if not isinstance(row, dict):
                        continue
                    strat = row.get("strategy") or row.get("name") or row.get("id")
                    if not strat:
                        continue
                    normalized[str(strat)] = {
                        "average_reward": float((row.get("average_reward", 0) or 0)),
                        "pnl": float((row.get("pnl", 0) or 0)),
                    }

            # Case C: scalar → apply to a default bucket
            elif isinstance(stats, (int, float)):
                normalized["default"] = {"average_reward": float(stats), "pnl": 0.0}

            else:
                # Unknown shape; nothing to do
                return

            if not normalized:
                return

            # Heuristic weighting
            for strategy, data in normalized.items():
                avg_reward = data["average_reward"]
                pnl = data["pnl"]

                weight = 1.0
                if avg_reward > 0.5 or pnl > 0:
                    weight = 1.25
                elif avg_reward < -0.3 or pnl < -0.2:
                    weight = 0.75

                await self.record_strategy_weight(strategy=strategy, weight=weight, reward=avg_reward)

        except Exception as e:
            logging.warning(f"[FeatureStore] Failed to refresh weights: {e}")

    def get_rolling_stats(self) -> Dict[str, Any]:
        return self.rolling.to_dict()

    async def record_outcome(
        self,
        *,
        token: str,
        side: str,
        pnl_pct: float,
        slip_bps: float,
        hold_sec: float,
        arm: str,
        profile: str,
        score_at_entry: float,
        context: Optional[Dict[str, Any]] = None,
        ts: Optional[float] = None,
    ):
        """
        Convenience: write a 'trade' outcome line AND a 'strategy_weight' line
        with a normalized reward for the given arm (bandit).
        """
        ts = ts or _epoch()
        reward = normalize_reward(pnl_pct=pnl_pct, slip_bps=slip_bps, hold_sec=hold_sec)

        # trade event (outcome)
        await self.record_event(
            "trade",
            {
                "token": token,
                "side": side,
                "score": float(score_at_entry),
                "pnl_pct": float(pnl_pct),
                "slip_bps": float(slip_bps),
                "hold_sec": float(hold_sec),
                "strategy": arm,
                "profile": profile,
                "context": context or {},
                "outcome": "closed",
            },
            ts=ts,
        )

         # bandit reward
        await self.record_strategy_weight(
            strategy=arm,
            weight=1.0,  # weight is informational; learning uses reward
            reward=reward,
            context={
                "token": token,
                "profile": profile,
                "pnl_pct": pnl_pct,
                "slip_bps": slip_bps,
                "hold_sec": hold_sec,
                "score_at_entry": score_at_entry,
            },
        )

    def prune_old_files(self):
        """
        Deletes shards older than max_days.
        """
        try:
            max_age = datetime.utcnow() - timedelta(days=self.max_days)
            for fname in os.listdir(self.path):
                if not fname.endswith(".jsonl") and not fname.endswith(".jsonl.gz"):
                    continue
                try:
                    # shard format: kind_YYYY-MM-DD_xxx.jsonl[.gz]
                    # but we write one file per day (feature_YYYY-MM-DD_x.jsonl.gz)
                    parts = fname.split("_")
                    if len(parts) < 2:
                        continue
                    date_part = parts[1]
                    date_obj = datetime.strptime(date_part, "%Y-%m-%d")
                    if date_obj < max_age:
                        full = os.path.join(self.path, fname)
                        os.remove(full)
                        logging.info(f"[FeatureStore] Pruned old shard {fname}")
                except Exception:
                    continue
        except Exception as e:
            logging.warning(f"[FeatureStore] prune_old_files failed: {e}")

    # ---------- Internal ----------

    def _recover_wal(self):
        """
        Replays WAL into a new shard if crash happened mid-write.
        """
        if not os.path.exists(self.wal_path):
            return
        try:
            with open(self.wal_path, "r") as f:
                lines = f.readlines()
            if not lines:
                return

            log_event(f"[FeatureStore] WAL recovery: {len(lines)} pending events.")
            # write them straight to a shard
            day_key = _date_key(_epoch())
            self._ensure_shard(day_key)
            for line in lines:
                try:
                    ev = json.loads(line)
                    self._write_event_to_shard(ev)
                except Exception as e:
                    logging.warning(f"[FeatureStore] Bad WAL line skipped: {e}")

            self._current_file_handle.flush()
            os.fsync(self._current_file_handle.fileno())
            self._truncate_wal()
        except Exception as e:
            logging.error(f"[FeatureStore] WAL recovery failed: {e}")

    def _append_to_wal(self, event: Dict[str, Any]):
        try:
            with open(self.wal_path, "a") as f:
                f.write(json.dumps(event, separators=(",", ":")) + "\n")
        except Exception as e:
            logging.warning(f"[FeatureStore] WAL write failed: {e}")

    def _truncate_wal(self):
        try:
            with open(self.wal_path, "w") as f:
                f.truncate(0)
        except Exception as e:
            logging.warning(f"[FeatureStore] WAL truncate failed: {e}")

    def _ensure_shard(self, day_key: str):
        # Rotate file if date changed or too many records
        if (self._current_date_key != day_key) or (self._current_file_count >= self.max_file_size):
            self._close_current_file()
            fname = self._make_new_filename(day_key)
            mode = "ab" if self.compress else "a"
            if self.compress:
                self._current_file_handle = gzip.open(fname, mode)  # type: ignore
            else:
                self._current_file_handle = open(fname, mode)
            self._current_file_path = fname
            self._current_file_count = 0
            self._current_date_key = day_key

    def _close_current_file(self):
        try:
            if self._current_file_handle:
                self._current_file_handle.close()
        except Exception:
            pass
        finally:
            self._current_file_handle = None
            self._current_file_path = None
            self._current_file_count = 0

    def _make_new_filename(self, day_key: str) -> str:
        ts_suffix = int(_epoch())
        suffix = "jsonl.gz" if self.compress else "jsonl"
        return os.path.join(self.path, f"features_{day_key}_{ts_suffix}.{suffix}")

    def _write_event_to_shard(self, ev: Dict[str, Any]):
        try:
            if self.compress:
                line = (json.dumps(ev, separators=(",", ":")) + "\n").encode("utf-8")
                self._current_file_handle.write(line)  # type: ignore
            else:
                self._current_file_handle.write(json.dumps(ev, separators=(",", ":")) + "\n")  # type: ignore
            self._current_file_count += 1
        except Exception as e:
            logging.error(f"[FeatureStore] Shard write failed: {e}")

    async def _background_loop(self):
        """
        Periodic flush + prune.
        """
        while True:
            try:
                await asyncio.sleep(self.sync_interval)
                await self.flush(force=False)
                self.prune_old_files()
                self._write_health_report()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.warning(f"[FeatureStore] background loop error: {e}")

    def _recent_shards(self, since_ts: float) -> List[str]:
        """
        Return shard paths that *may* contain events newer than since_ts.
        We pick today's shard and a few previous ones to be safe.
        """
        try:
            all_files = sorted(glob.glob(os.path.join(self.path, "features_*.jsonl*")))
            # keep last ~6 shards (sufficient for your 24h horizon with rolling files)
            return all_files[-6:]
        except Exception:
            return []

    def _iter_events_from_file(self, path: str):
        """
        Yield decoded events from a given shard path (gz or plain).
        """
        try:
            if path.endswith(".gz"):
                fh = gzip.open(path, "rt", encoding="utf-8")
            else:
                fh = open(path, "r", encoding="utf-8")
            with fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except Exception:
                        continue
        except Exception:
            return

    def health_snapshot(self) -> Dict[str, Any]:
        try:
            last_sw = self.get_last_events("strategy_weight", n=50)
            last_trades = self.get_last_events("trade", n=50)
        except Exception:
            last_sw, last_trades = [], []
        return {
            "ts": _epoch(),
            "schema": 1,
            "rolling": self.rolling.to_dict(),
            "recent": {
                "strategy_weight": len(last_sw),
                "trade": len(last_trades),
            },
            "paths": {
                "root": self.path,
                "wal": self.wal_path,
                "current_file": self._current_file_path,
            },
        }

    def _write_health_report(self):
        try:
            os.makedirs("runtime/reports", exist_ok=True)
            with open("runtime/reports/feature_store_health.json", "w", encoding="utf-8") as f:
                json.dump(self.health_snapshot(), f, indent=2)
        except Exception:
            pass

# ---- Singleton wiring ----

_feature_store: Optional[FeatureStore] = None
_feature_lock = asyncio.Lock()

async def init_feature_store() -> FeatureStore:
    global _feature_store
    if _feature_store is None:
        async with _feature_lock:
            if _feature_store is None:
                fs_cfg = config.get("feature_store", {})
                _feature_store = FeatureStore(fs_cfg)
                await _feature_store.start()
                
                # Expose some helpers to librarian
                librarian.register("feature_store", {
                    "get_last_events": _feature_store.get_last_events,
                    "get_recent_rewards_by_strategy": _feature_store.get_recent_rewards_by_strategy,
                    "get_rolling_stats": _feature_store.get_rolling_stats,
                    "record_event": _feature_store.record_event,
                    "record_trade": _feature_store.record_trade,
                    "record_signal": _feature_store.record_signal,
                    "record_decision": _feature_store.record_decision,
                    "record_strategy_weight": _feature_store.record_strategy_weight,
                    "record_pnl_snapshot": _feature_store.record_pnl_snapshot,
                })
                log_event("🗃️ FeatureStore registered in librarian.")
    return _feature_store

def get_feature_store_sync() -> FeatureStore:
    if _feature_store is None:
        raise RuntimeError("FeatureStore not initialized. Call await init_feature_store() in startup.")
    return _feature_store
