#/data_sources/chart_data_loader.py
"""
Build real-time OHLCV bars from Nyx's firehose memory (no external APIs).
- Consumes shared_memory["recent_transactions"] populated by your firehose listener.
- Supports multi-interval aggregation (e.g., 1m / 5m / 15m / 1h).
- Keeps an in-memory rolling cache so repeated calls are cheap.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional

from memory.shared_runtime import get as shm_get

# ---------- Config ----------
DEFAULT_INTERVALS = ("1m", "5m", "15m")
MAX_BARS_PER_INTERVAL = 500  # rolling cache depth per interval

# ---------- Internal State ----------
_lock = asyncio.Lock()
_ohlcv_cache: Dict[str, Dict[str, List["Bar"]]] = {}  # token -> interval -> [Bar]


# ---------- Data Models ----------
@dataclass
class Bar:
    t: datetime  # start time (bucket)
    o: float
    h: float
    l: float
    c: float
    v: float
    trades: int

    def to_dict(self) -> dict:
        d = asdict(self)
        d["t"] = self.t.isoformat()
        return d


# ---------- Public API ----------
async def get_chart_data(
    token_address: str,
    interval: str = "1m",
    lookback_bars: int = 120,
    force_rebuild: bool = False,
) -> List[dict]:
    """
    Return the last `lookback_bars` OHLCV bars for a token at `interval`.
    Bars are built entirely from firehose recent_transactions.
    """
    async with _lock:
        if force_rebuild or token_address not in _ohlcv_cache or interval not in _ohlcv_cache.get(token_address, {}):
            await _rebuild_for_token(token_address, intervals=[interval])
        else:
            # Incrementally update using latest txs
            await _incremental_update(token_address, intervals=[interval])

        bars = _ohlcv_cache[token_address][interval][-lookback_bars:]
        return [b.to_dict() for b in bars]


async def get_multi_intervals(
    token_address: str,
    intervals: Iterable[str] = DEFAULT_INTERVALS,
    lookback_bars: int = 120,
    force_rebuild: bool = False,
) -> Dict[str, List[dict]]:
    """
    Return bars for multiple intervals at once.
    """
    async with _lock:
        if force_rebuild or token_address not in _ohlcv_cache:
            await _rebuild_for_token(token_address, intervals=intervals)
        else:
            await _incremental_update(token_address, intervals=intervals)

        out = {}
        for iv in intervals:
            bars = _ohlcv_cache[token_address].get(iv, [])[-lookback_bars:]
            out[iv] = [b.to_dict() for b in bars]
        return out


async def get_latest_bar(token_address: str, interval: str = "1m") -> Optional[dict]:
    async with _lock:
        if token_address not in _ohlcv_cache or interval not in _ohlcv_cache[token_address]:
            await _rebuild_for_token(token_address, intervals=[interval])
        else:
            await _incremental_update(token_address, intervals=[interval])

        bars = _ohlcv_cache[token_address][interval]
        return bars[-1].to_dict() if bars else None


async def reset_cache(token_address: Optional[str] = None):
    async with _lock:
        if token_address is None:
            _ohlcv_cache.clear()
        else:
            _ohlcv_cache.pop(token_address, None)


# ---------- Core Builders ----------
async def _rebuild_for_token(token: str, intervals: Iterable[str]):
    """Full rebuild for given token & intervals from recent_transactions."""
    txs = _get_firehose_txs(token)
    if not txs:
        _ensure_cache(token, intervals)
        return

    _ensure_cache(token, intervals)

    for iv in intervals:
        buckets = _bucketize(txs, iv)
        bars = [_build_bar(bucket_time, bucket_txs) for bucket_time, bucket_txs in buckets.items()]
        bars.sort(key=lambda b: b.t)
        _ohlcv_cache[token][iv] = bars[-MAX_BARS_PER_INTERVAL:]


async def _incremental_update(token: str, intervals: Iterable[str]):
    """
    Fast-path: take only *new* tx from firehose since last bar time
    and update the latest bar or append new bars.
    """
    txs = _get_firehose_txs(token)
    if not txs:
        _ensure_cache(token, intervals)
        return

    _ensure_cache(token, intervals)

    for iv in intervals:
        cache = _ohlcv_cache[token].get(iv, [])
        if not cache:
            # no bars exist, rebuild
            await _rebuild_for_token(token, [iv])
            continue

        last_bar_time = cache[-1].t
        bucket_size = _interval_to_timedelta(iv)

        # Include txs that fall in current (open) bucket or create new buckets forward
        new_txs = [tx for tx in txs if _bucket_start(tx["dt"], bucket_size) >= last_bar_time]

        if not new_txs:
            continue

        # Rebuild from last_bar_time forward
        # Combine existing cache up to last_bar_time, then rebuild from that point
        base_txs = [tx for tx in txs if _bucket_start(tx["dt"], bucket_size) < last_bar_time]
        forward_txs = [tx for tx in txs if _bucket_start(tx["dt"], bucket_size) >= last_bar_time]

        buckets = _bucketize(forward_txs, iv)
        new_bars = [_build_bar(bt, btx) for bt, btx in buckets.items()]
        new_bars.sort(key=lambda b: b.t)

        # Merge: drop any existing bars that are >= first new bar time
        first_new_time = new_bars[0].t if new_bars else None
        merged = [b for b in cache if first_new_time is None or b.t < first_new_time]
        merged.extend(new_bars)
        _ohlcv_cache[token][iv] = merged[-MAX_BARS_PER_INTERVAL:]


# ---------- Helpers ----------
def _get_firehose_txs(token: str) -> List[dict]:
    """
    Expects shared_memory["recent_transactions"] entries shaped like:
    {
      "token": <mint>,
      "timestamp": ISO8601 string or epoch seconds,
      "price": float,
      "amount": float (base units, e.g. token amount or SOL spent),
      ...
    }
    """
    raw = shm_get("recent_transactions", []) or []
    out = []
    for tx in raw:
        if tx.get("token") != token:
            continue
        ts = tx.get("timestamp")
        dt = _as_dt(ts)
        if not dt:
            continue
        price = tx.get("price")
        if price is None:
            continue
        out.append({
            "dt": dt,
            "price": float(price),
            "amount": float(tx.get("amount", 0.0)),
        })
    return out


def _ensure_cache(token: str, intervals: Iterable[str]):
    if token not in _ohlcv_cache:
        _ohlcv_cache[token] = {}
    for iv in intervals:
        _ohlcv_cache[token].setdefault(iv, [])


def _bucketize(txs: List[dict], interval: str) -> Dict[datetime, List[dict]]:
    bucket_size = _interval_to_timedelta(interval)
    buckets: Dict[datetime, List[dict]] = {}
    for tx in txs:
        bstart = _bucket_start(tx["dt"], bucket_size)
        if bstart not in buckets:
            buckets[bstart] = []
        buckets[bstart].append(tx)
    return buckets


def _build_bar(bucket_time: datetime, txs: List[dict]) -> Bar:
    prices = [t["price"] for t in txs]
    vol = sum(t.get("amount", 0.0) for t in txs)
    return Bar(
        t=bucket_time,
        o=prices[0],
        h=max(prices),
        l=min(prices),
        c=prices[-1],
        v=vol,
        trades=len(prices),
    )


def _interval_to_timedelta(interval: str) -> timedelta:
    """
    Supports: Xm, Xh
    """
    interval = interval.strip().lower()
    if interval.endswith("m"):
        return timedelta(minutes=int(interval[:-1]))
    if interval.endswith("h"):
        return timedelta(hours=int(interval[:-1]))
    raise ValueError(f"Unsupported interval: {interval}")


def _bucket_start(dt: datetime, bucket: timedelta) -> datetime:
    """
    Returns the floored datetime that represents the start of the bucket.
    """
    epoch = datetime(1970, 1, 1)
    seconds = int((dt - epoch).total_seconds())
    bucket_sec = int(bucket.total_seconds())
    floored = seconds - (seconds % bucket_sec)
    return epoch + timedelta(seconds=floored)


def _as_dt(ts) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        # assume epoch seconds
        return datetime.utcfromtimestamp(ts)
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            try:
                return datetime.utcfromtimestamp(float(ts))
            except Exception:
                return None
    if isinstance(ts, datetime):
        return ts
    return None
