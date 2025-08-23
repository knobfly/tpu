from __future__ import annotations
import inspect
import asyncio
import contextlib
import json
import logging
import os
import random
import threading
import time
import traceback
from functools import partial
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

import psutil
from core.live_config import config
from utils.logger import log_event
from utils.service_status import update_status

try:
    from utils.telegram_utils import send_telegram_message as _tg_send
except Exception:
    _tg_send = None

try:
    import aiohttp
except Exception:
    aiohttp = None

__all__ = [
    "guardian",
    "crash_guardian",
    "register_module",
    "beat",
    "wrap_safe_loop",
    "start_crash_guardian",
    "get_guardian_status_snapshot",
]

# ---------------- Config ----------------
HEARTBEAT_DEFAULT_TIMEOUT = 90.0
GUARDIAN_LOOP_INTERVAL    = 2.0
SYSTEM_CHECK_INTERVAL     = 60.0
RPC_CHECK_INTERVAL        = 60.0
ALERT_THROTTLE_SECONDS    = 300.0
HIGH_CPU_THRESHOLD        = 90.0
HIGH_MEM_THRESHOLD        = 90.0
LOW_DISK_THRESHOLD        = 5.0
FD_SOFT_LIMIT_WARN_AT     = 0.85
MAX_LAST_ALERTS           = 200
MIN_RESTART_INTERVAL      = 10.0
STARTUP_GRACE_DEFAULT     = 30.0
BACKOFF_START_S           = 1.0
BACKOFF_MAX_S             = 60.0

FAILURE_LOG_PATH = "/home/ubuntu/nyx/runtime/monitor/failures.json"

SOL_PING_METHOD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getLatestBlockhash",
    "params": []
}

# ------------- bg event loop helper -------------
_bg_loop = None
_bg_lock = threading.Lock()

def _get_or_start_bg_loop():
    global _bg_loop
    with _bg_lock:
        if _bg_loop and _bg_loop.is_running():
            return _bg_loop
        _bg_loop = asyncio.new_event_loop()
        t = threading.Thread(target=_bg_loop.run_forever, name="crashguardian-bg", daemon=True)
        t.start()
        return _bg_loop

def launch_coro(coro):
    """Schedule coro on the current loop if running, else on a background loop."""
    try:
        loop = asyncio.get_running_loop()
        return loop.create_task(coro)
    except RuntimeError:
        loop = _get_or_start_bg_loop()
        return asyncio.run_coroutine_threadsafe(coro, loop)

# ---------------- utils ----------------
def _now() -> float:
    return time.time()

def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

@dataclass
class ModuleMeta:
    name: str
    start_fn: Callable[[], Awaitable[None]]
    hb_timeout: float = HEARTBEAT_DEFAULT_TIMEOUT
    restart: bool = True
    critical: bool = True
    startup_grace: float = STARTUP_GRACE_DEFAULT
    last_beat: float = field(default_factory=_now)
    ok: bool = True
    task: Optional[asyncio.Task] = None  # may be None if scheduled on bg loop
    last_restart: float = field(default_factory=_now)
    backoff_s: float = BACKOFF_START_S
    min_sleep: float = 0.0

class CrashGuardian:
    def __init__(self):
        # defensively initialize
        self._modules: Dict[str, ModuleMeta] = {}
        self._last_alert: Dict[str, float] = {}
        self._status_cache: Dict[str, dict] = {}
        self._alerts_ring = deque(maxlen=MAX_LAST_ALERTS)
        self._init_defaults()

        # system / rpc state
        self._system_last_check = 0.0
        self._rpc_last_check = 0.0
        self._cpu = 0.0
        self._mem = 0.0
        self._disk_free_pct = 100.0
        self._open_fd_pct = None
        self._rpc_ok = True
        self._rpc_latency_ms: Optional[float] = None
        self._websocket_ok = True
        self._firehose_ok = True

        self._last_loop_at = _now()
        self._started = False
        self._watchdog_task = None

    # ensure attributes always exist
    def _init_defaults(self):
        for name, default in (
            ("_modules", {}),
            ("_last_alert", {}),
            ("_status_cache", {}),
            ("_alerts_ring", deque(maxlen=MAX_LAST_ALERTS)),
            ("_cpu", 0.0),
            ("_mem", 0.0),
            ("_disk_free_pct", 0.0),
            ("_open_fd_pct", None),
            ("_rpc_ok", None),
            ("_rpc_latency_ms", None),
            ("_websocket_ok", None),
            ("_firehose_ok", None),
            ("_last_loop_at", None),
        ):
            if not hasattr(self, name):
                setattr(self, name, default)

    # ------------- Telegram -------------
    async def notify(self, text: str):
        if not text:
            return
        bot_token = config.get("telegram_token")
        chat_id = config.get("telegram_chat_id")
        if not bot_token or not chat_id:
            log_event(f"[CrashGuardian] {text}")
            return

        if _tg_send:
            try:
                await _tg_send(text)
                return
            except Exception as e:
                logging.warning(f"[CrashGuardian] Telegram send failed: {e}")

        if not aiohttp:
            log_event(f"[CrashGuardian] {text}")
            return

        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            async with aiohttp.ClientSession() as session:
                await session.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
        except Exception as e:
            logging.warning(f"[CrashGuardian] Telegram raw send failed: {e}")

    def _should_alert(self, key: str) -> bool:
        now = _now()
        last = self._last_alert.get(key, 0.0)
        if now - last >= ALERT_THROTTLE_SECONDS:
            self._last_alert[key] = now
            return True
        return False

    def _record_alert(self, msg: str):
        self._alerts_ring.append({"time": _now(), "msg": msg})

    # ------------- Control -------------
    def register_module(
        self,
        name: str,
        start_fn: Callable[[], Awaitable[None]],
        heartbeat_timeout: float = HEARTBEAT_DEFAULT_TIMEOUT,
        restart: bool = True,
        critical: bool = True,
        startup_grace: float = STARTUP_GRACE_DEFAULT,
        min_sleep: float = 0.0,
    ):
        self._init_defaults()
        self._modules[name] = ModuleMeta(
            name=name,
            start_fn=start_fn,
            hb_timeout=heartbeat_timeout,
            restart=restart,
            critical=critical,
            startup_grace=startup_grace,
            min_sleep=min_sleep,
        )

    def beat(self, name: str):
        self._init_defaults()
        meta = self._modules.get(name)
        if meta:
            meta.last_beat = _now()

    def touch_websocket(self):
        self._websocket_ok = True

    def touch_firehose(self):
        self._firehose_ok = True

    # ------------- Internals -------------
    def _start_task(self, meta: ModuleMeta):
        meta.last_beat = _now()
        meta.ok = True
        meta.last_restart = _now()
        meta.backoff_s = BACKOFF_START_S
        try:
            runner = wrap_safe_loop(meta.name, meta.start_fn, heartbeat_every=max(5.0, meta.hb_timeout / 2.0))
            t = launch_coro(runner())
            meta.task = t if hasattr(t, "cancel") else None
            logging.info(f"[CrashGuardian] started {meta.name}")
        except Exception as e:
            logging.warning(f"[CrashGuardian] Failed to create task for {meta.name}: {e}")
            meta.task = None
            meta.ok = False

    async def _restart_module(self, meta: ModuleMeta):
        name = meta.name
        try:
            if meta.task and not meta.task.done():
                meta.task.cancel()
                with contextlib.suppress(Exception):
                    await meta.task
            log_event(f"🔁 Restarting {name} ...")
            self._start_task(meta)
            meta.last_beat = _now()
            meta.last_restart = _now()
            meta.backoff_s = min(meta.backoff_s * 2.0, BACKOFF_MAX_S)
        except Exception as e:
            err = f"🔥 CrashGuardian failed to restart {name}: {e}"
            logging.error(traceback.format_exc())
            log_event(err)
            if self._should_alert(f"mod_{name}_restart_fail"):
                await self.notify(err)

    # ------------- Loops -------------
    async def start(self):
        if self._started:
            return
        self._started = True
        update_status("crash_guardian")
        log_event("🧠 CrashGuardian active")
        task_or_future = launch_coro(self._watchdog_loop())
        self._watchdog_task = task_or_future if hasattr(task_or_future, "cancel") else None

    async def stop(self):
        self._started = False
        t = self._watchdog_task
        if t and not t.done():
            t.cancel()
            with contextlib.suppress(Exception):
                await t

    async def _watchdog_loop(self):
        while True:
            self._last_loop_at = _now()
            await self._check_modules()
            await self._check_system()
            await self._check_rpc()
            await asyncio.sleep(GUARDIAN_LOOP_INTERVAL)

    async def _check_modules(self):
        now = _now()
        for name, meta in list(self._modules.items()):
            self._status_cache[name] = {
                "last_beat": meta.last_beat,
                "alive": (now - meta.last_beat) <= meta.hb_timeout,
                "restart": meta.restart,
                "critical": meta.critical,
            }

            if (now - meta.last_restart) < meta.startup_grace:
                continue

            task_done = (meta.task is None) or meta.task.done()
            stale = (now - meta.last_beat) > meta.hb_timeout

            if not task_done and not stale:
                continue

            if stale and meta.ok:
                meta.ok = False
                msg = f"⚠️ {name} unresponsive (> {meta.hb_timeout:.0f}s)"
                log_event(msg)
                if self._should_alert(f"mod_{name}_dead"):
                    self._record_alert(msg)
                    await self.notify(
                        f"⚠️ *Module stalled:* `{name}`\n"
                        f"⏱ Last beat: `{int(now - meta.last_beat)}s` ago\n"
                        f"🧰 Restarting: `{'yes' if meta.restart else 'no'}`"
                    )

            if meta.restart:
                base_delay = min(meta.backoff_s, BACKOFF_MAX_S) + random.uniform(0.0, 0.4)
                delay = max(MIN_RESTART_INTERVAL, base_delay, float(getattr(meta, "min_sleep", 0.0) or 0.0))
                if (now - meta.last_restart) < delay:
                    continue
                await self._restart_module(meta)

    async def _check_system(self):
        now = _now()
        if now - self._system_last_check < SYSTEM_CHECK_INTERVAL:
            return
        self._system_last_check = now
        try:
            self._cpu = psutil.cpu_percent(interval=None)
            self._mem = psutil.virtual_memory().percent
            disk = psutil.disk_usage("/")
            self._disk_free_pct = 100.0 - disk.percent

            try:
                proc = psutil.Process()
                soft, hard = proc.rlimit(psutil.RLIMIT_NOFILE)
                open_fds = proc.num_fds() if hasattr(proc, "num_fds") else None
                self._open_fd_pct = (open_fds / soft) if (open_fds and soft) else None
            except Exception:
                self._open_fd_pct = None

            warn = []
            if self._cpu >= HIGH_CPU_THRESHOLD:
                warn.append(f"CPU {self._cpu:.1f}%")
            if self._mem >= HIGH_MEM_THRESHOLD:
                warn.append(f"RAM {self._mem:.1f}%")
            if self._disk_free_pct <= LOW_DISK_THRESHOLD:
                warn.append(f"Disk free {self._disk_free_pct:.1f}%")
            if self._open_fd_pct and self._open_fd_pct >= FD_SOFT_LIMIT_WARN_AT:
                warn.append(f"FD usage {self._open_fd_pct*100:.1f}%")

            if warn and self._should_alert("sys_high_usage"):
                msg = "⚠️ *System pressure:* " + ", ".join(warn)
                self._record_alert(msg)
                await self.notify(msg)
        except Exception as e:
            logging.warning(f"[CrashGuardian] System check failed: {e}")

    async def _check_rpc(self):
        now = _now()
        if now - self._rpc_last_check < RPC_CHECK_INTERVAL:
            return
        self._rpc_last_check = now
        try:
            from utils.rpc_loader import get_active_rpc
        except Exception:
            return
        rpc_url = get_active_rpc()
        if not rpc_url or not aiohttp:
            return
        start = _now()
        ok = False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(rpc_url, json=SOL_PING_METHOD, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, dict) and "result" in data:
                            ok = True
        except Exception:
            ok = False
        self._rpc_latency_ms = (_now() - start) * 1000.0
        if ok and not self._rpc_ok:
            self._rpc_ok = True
            if self._should_alert("rpc_recovered"):
                await self.notify(f"✅ RPC recovered. Latency: `{self._rpc_latency_ms:.0f}ms`")
        elif not ok and (self._rpc_ok is True):
            self._rpc_ok = False
            if self._should_alert("rpc_down"):
                await self.notify("🚨 *RPC looks down or stalling*")
            log_event("⚠️ RPC check failed.")

    # ------------- Status -------------
    def status_snapshot(self, since: float | None = None) -> dict:
        self._init_defaults()
        now = _now()
        modules = {}
        for name, meta in list(self._modules.items()):
            lb = getattr(meta, "last_beat", now)
            lr = getattr(meta, "last_restart", now)
            hb_timeout = float(getattr(meta, "hb_timeout", HEARTBEAT_DEFAULT_TIMEOUT))
            alive = (now - lb) <= hb_timeout
            modules[name] = {
                "alive": alive,
                "last_beat_sec": round(now - lb, 1),
                "hb_timeout": hb_timeout,
                "restart": bool(getattr(meta, "restart", False)),
                "critical": bool(getattr(meta, "critical", False)),
                "backoff_s": float(getattr(meta, "backoff_s", 0.0) or 0.0),
                "since_restart_s": round(now - lr, 1),
            }

        sys_info = {
            "cpu_pct": round(float(getattr(self, "_cpu", 0.0) or 0.0), 1),
            "ram_pct": round(float(getattr(self, "_mem", 0.0) or 0.0), 1),
            "disk_free_pct": round(float(getattr(self, "_disk_free_pct", 0.0) or 0.0), 1),
            "open_fd_pct": (round(float(getattr(self, "_open_fd_pct", 0.0) or 0.0) * 100, 1)
                            if getattr(self, "_open_fd_pct", None) is not None else None),
        }
        ext = {
            "rpc_ok": getattr(self, "_rpc_ok", None),
            "rpc_latency_ms": round(float(getattr(self, "_rpc_latency_ms", 0.0) or 0.0), 1),
            "websocket_ok": getattr(self, "_websocket_ok", None),
            "firehose_ok": getattr(self, "_firehose_ok", None),
        }
        return {
            "time": _utc_iso(),
            "modules": modules,
            "system": sys_info,
            "external": ext,
            "guardian": {
                "loop_last_at": getattr(self, "_last_loop_at", None),
                "alerts_buffer_len": len(getattr(self, "_alerts_ring", []) or []),
            },
        }

    @classmethod
    def get_guardian_status_snapshot(cls, **kwargs) -> dict:
        # class shim so callers can do CrashGuardian.get_guardian_status_snapshot(since=...)
        return guardian.status_snapshot(**kwargs)

    # ------------- Fail log -------------
    def log_failure(self, module: str, reason: str = "unknown"):
        os.makedirs(os.path.dirname(FAILURE_LOG_PATH), exist_ok=True)
        entry = {"module": module, "reason": reason, "time": _utc_iso()}
        try:
            if os.path.exists(FAILURE_LOG_PATH):
                with open(FAILURE_LOG_PATH, "r") as f:
                    log = json.load(f)
            else:
                log = []
            log.append(entry)
            with open(FAILURE_LOG_PATH, "w") as f:
                json.dump(log[-500:], f, indent=2)
        except Exception as e:
            logging.warning(f"[CrashGuardian] Failed to log failure: {e}")

# ------------- module-level singleton & helpers -------------
guardian = CrashGuardian()
# keep legacy alias some code imports
crash_guardian = guardian

def register_module(
    name: str,
    start_fn: Callable[[], Awaitable[None]],
    heartbeat_timeout: float = HEARTBEAT_DEFAULT_TIMEOUT,
    restart: bool = True,
    critical: bool = True,
    startup_grace: float = STARTUP_GRACE_DEFAULT,
    min_sleep: float = 0.0,
):
    guardian.register_module(
        name=name,
        start_fn=start_fn,
        heartbeat_timeout=heartbeat_timeout,
        restart=restart,
        critical=critical,
        startup_grace=startup_grace,
        min_sleep=min_sleep,
    )

def beat(name: str):
    guardian.beat(name)

def start_crash_guardian():
    return guardian.start()


def get_guardian_status_snapshot(since: float | None = None) -> dict:
    return guardian.status_snapshot(since=since)

# --- replace existing wrap_safe_loop with this version
def wrap_safe_loop(
    name: str,
    fn: Callable[[], Awaitable[None] | None],
    *,
    heartbeat_every: float = 30.0,
    repeat: bool = False,
    repeat_interval: float | None = None,
    probe_every: float = 30.0,
):
    """
    Wraps a coroutine or sync function so CrashGuardian can manage it.

    Adds:
      - Heartbeat ticker (independent of module code)
      - Startup 'entered' log
      - Periodic PROBE that calls bound-instance methods if present:
          readiness() -> bool | (bool, str)
          liveness()  -> bool | (bool, str)
          metrics()   -> dict
        and logs a standardized health line every `probe_every` seconds.

    Behavior:
      - repeat=False: run once (legacy) — exceptions bubble for guardian to restart
      - repeat=True : loop forever — normal returns sleep `repeat_interval` (or heartbeat_every)
    """
    import inspect
    import asyncio
    import contextlib
    import logging
    import traceback

    async def _hb_pump():
        try:
            while True:
                beat(name)
                await asyncio.sleep(max(heartbeat_every, 1.0))
        except asyncio.CancelledError:
            raise

    # probe helpers -----------------------------------------------------------
    def _get_owner(callable_obj):
        # If fn is a bound method, return the instance (so we can call readiness/liveness/metrics)
        return getattr(callable_obj, "__self__", None)

    async def _safe_call(callable_or_none):
        try:
            if callable_or_none is None:
                return None
            res = callable_or_none()
            if inspect.iscoroutine(res):
                return await res
            return res
        except Exception as e:
            logging.warning("[Guardian] Probe call failed for %s: %s", name, e)
            return None

    async def _probe_loop(owner):
        if owner is None:
            # We still emit a heartbeat style health line so you know it's running
            try:
                while True:
                    logging.info("[Guardian] Health %s ready=? live=? metrics=?", name)
                    await asyncio.sleep(max(5.0, float(probe_every)))
            except asyncio.CancelledError:
                raise

        # owner has optional readiness/liveness/metrics
        rfn = getattr(owner, "readiness", None)
        lfn = getattr(owner, "liveness", None)
        mfn = getattr(owner, "metrics", None)

        try:
            while True:
                ready = await _safe_call(rfn)
                live  = await _safe_call(lfn)
                mets  = await _safe_call(mfn)

                def _norm_pair(val):
                    if isinstance(val, tuple) and len(val) >= 2:
                        return bool(val[0]), str(val[1])
                    if isinstance(val, bool):
                        return val, ""
                    return None, ""

                r_ok, r_reason = _norm_pair(ready)
                l_ok, l_reason = _norm_pair(live)

                # Health line
                logging.info("[Guardian] Health %s ready=%s live=%s metrics=%s",
                             name, r_ok if r_ok is not None else "?",
                             l_ok if l_ok is not None else "?",
                             mets if isinstance(mets, dict) else "?")

                # Warnings with reason if not OK
                if r_ok is False:
                    logging.warning("[Guardian] %s readiness: %s", name, r_reason or "not ready")
                if l_ok is False:
                    logging.warning("[Guardian] %s liveness: %s", name, l_reason or "not live")

                await asyncio.sleep(max(5.0, float(probe_every)))
        except asyncio.CancelledError:
            raise

    # execution helpers -------------------------------------------------------
    async def _run_once_async(callable_obj):
        # Callable may be:
        # - async def (coroutine function): call then await
        # - callable returning a coroutine: call, then await result
        # - sync function: run in thread executor
        if inspect.iscoroutinefunction(callable_obj):
            await callable_obj()
            return

        res = callable_obj()
        if inspect.iscoroutine(res):
            await res
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, res if callable(res) else (lambda: None))

    async def runner():
        logging.info("[Guardian] %s: starting (repeat=%s, hb=%.1fs, probe=%.1fs)",
                     name, repeat, heartbeat_every, probe_every)

        owner = _get_owner(fn)
        hb_task = asyncio.create_task(_hb_pump(), name=f"cg:hb:{name}")
        probe_task = asyncio.create_task(_probe_loop(owner), name=f"cg:probe:{name}")
        try:
            # "entered" confirmation
            logging.info("[Guardian] %s: entered start_fn", name)

            if not repeat:
                await _run_once_async(fn)
            else:
                interval = repeat_interval if (repeat_interval is not None) else heartbeat_every
                while True:
                    await _run_once_async(fn)
                    await asyncio.sleep(max(0.1, float(interval)))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.error("💥 %s crashed: %s", name, e)
            logging.error(traceback.format_exc())
            raise
        finally:
            for t in (probe_task, hb_task):
                try:
                    t.cancel()
                    with contextlib.suppress(Exception):
                        await t
                except Exception:
                    pass

    return runner

