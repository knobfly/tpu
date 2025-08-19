# inputs/social/x_alpha/x_gate.py
from __future__ import annotations
import asyncio, os, time, contextlib

# Optional cross-process file lock (Linux/macOS). Falls back to in-proc only.
try:
    import fcntl
    HAVE_FCNTL = True
except Exception:
    HAVE_FCNTL = False

# In-proc async lock + simple spacing guard
_gate_lock = asyncio.Lock()
_last_call_ts = 0.0

# Defaults; you can override from config in your callers
MIN_SPACING_SEC = 5.0     # ensure at least this many seconds between X queries
MAX_HOLD_SEC    = 20.0    # safety cap so one job can’t starve the other

LOCKFILE_PATH = "/home/ubuntu/nyx/runtime/locks/x_api.lock"
os.makedirs(os.path.dirname(LOCKFILE_PATH), exist_ok=True)

@contextlib.asynccontextmanager
async def x_api_guard(min_spacing: float | None = None, cross_process: bool = True, who: str = "unknown"):
    """
    Async context manager that:
      - serializes X API access across tasks (and optionally processes),
      - enforces a minimum spacing between calls to avoid rate bursts.

    Usage:
        async with x_api_guard(who="x_feed_scanner"):
            ... do your X API calls here ...
    """
    global _last_call_ts
    spacing = float(min_spacing if min_spacing is not None else MIN_SPACING_SEC)

    # 1) Acquire in-proc lock
    await _gate_lock.acquire()
    file_lock = None
    try:
        # 2) Optionally acquire cross-proc file lock
        if cross_process and HAVE_FCNTL:
            file_lock = open(LOCKFILE_PATH, "a+")
            try:
                fcntl.flock(file_lock, fcntl.LOCK_EX)
            except Exception:
                # If file lock fails, we still have the in-proc lock
                file_lock = None

        # 3) Enforce spacing (simple sleep if last call was too recent)
        now = time.time()
        elapsed = now - _last_call_ts
        if elapsed < spacing:
            await asyncio.sleep(spacing - elapsed)

        # 4) Enter guarded section
        yield

        # 5) Stamp last_call_ts at exit
        _last_call_ts = time.time()

    finally:
        # Release file lock first
        if file_lock:
            try:
                fcntl.flock(file_lock, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                file_lock.close()
            except Exception:
                pass
        # Then in-proc lock
        try:
            _gate_lock.release()
        except Exception:
            pass
