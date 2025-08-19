# -*- coding: utf-8 -*-
"""
Firehose health/latency monitor.
Tracks:
  - packets/sec
  - last packet timestamp
  - decode latency avg/p95
  - stall detection
  - backlog (fed by backlog_guard)
Publishes to CrashGuardian (if present) + service_status.
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Deque, Optional

from utils.logger import log_event
from utils.service_status import update_status

# Optional CrashGuardian (fail-soft import)
try:
    from utils.crash_guardian import CrashGuardian
except Exception:
    CrashGuardian = None  # type: ignore


@dataclass
class FirehoseStats:
    last_packet_ts: float = 0.0
    packets_total: int = 0
    packets_last_window: int = 0
    pps: float = 0.0  # packets per second (rolling)
    decode_lat_avg_ms: float = 0.0
    decode_lat_p95_ms: float = 0.0
    stalled: bool = False
    stall_seconds: float = 0.0
    backlog_seconds: float = 0.0  # from backlog_guard
    errors: int = 0

class FirehoseHealthMonitor:
    def __init__(
        self,
        stall_threshold_s: float = 5.0,
        window_seconds: int = 10,
        max_latency_samples: int = 500
    ):
        self.stall_threshold_s = stall_threshold_s
        self.window_seconds = window_seconds
        self.max_latency_samples = max_latency_samples

        self._stats = FirehoseStats()
        self._window_packets: Deque[float] = deque()
        self._latencies_ms: Deque[float] = deque(maxlen=max_latency_samples)
        self._running = False

        # NEW: Track last event heartbeat
        self._last_event_ts: float = 0.0

    # --------- called by packet_listener ---------
    def record_packet(self, decode_latency_ms: Optional[float] = None):
        now = time.time()

        if self._stats.packets_total == 0:
            log_event("✅ [Firehose] First packet received — Firehose is alive.")

        self._stats.packets_total += 1
        self._stats.last_packet_ts = now
        self._window_packets.append(now)
        if decode_latency_ms is not None:
            self._latencies_ms.append(float(decode_latency_ms))

    # --------- called by stream_router or watchdog ---------
    def record_event_heartbeat(self):
        """Call this when a decoded event is successfully routed."""
        self._last_event_ts = time.time()

    def record_error(self):
        self._stats.errors += 1

    def update_backlog_seconds(self, lag_s: float):
        self._stats.backlog_seconds = max(0.0, float(lag_s))

    # -----------------------------------------------------------------------
    def _recompute_metrics(self):
        now = time.time()

        # Trim old packets from sliding window
        while self._window_packets and (now - self._window_packets[0]) > self.window_seconds:
            self._window_packets.popleft()

        self._stats.packets_last_window = len(self._window_packets)
        self._stats.pps = self._stats.packets_last_window / float(self.window_seconds)

        # Stall detection: use the most recent activity from either packets or events
        last_activity_ts = max(self._stats.last_packet_ts, self._last_event_ts)
        if last_activity_ts == 0:
            self._stats.stalled = True
            self._stats.stall_seconds = 0.0
        else:
            self._stats.stall_seconds = max(0.0, now - last_activity_ts)
            self._stats.stalled = self._stats.stall_seconds >= self.stall_threshold_s

        # Decode latency stats
        if self._latencies_ms:
            arr = sorted(self._latencies_ms)
            self._stats.decode_lat_avg_ms = sum(arr) / len(arr)
            idx = max(0, int(0.95 * len(arr)) - 1)
            self._stats.decode_lat_p95_ms = arr[idx]
        else:
            self._stats.decode_lat_avg_ms = 0.0
            self._stats.decode_lat_p95_ms = 0.0

    def export_metrics(self) -> dict:
        """For CrashGuardian / priority scheduler."""
        self._recompute_metrics()
        return {"firehose." + k: v for k, v in asdict(self._stats).items()}

    async def run(self, poll_every: float = 1.0):
        if self._running:
            return
        self._running = True
        log_event("🩺 FirehoseHealthMonitor started.")
        while True:
            try:
                update_status("firehose_health_monitor")
                self._recompute_metrics()

                if CrashGuardian:
                    try:
                        CrashGuardian.instance().merge_metrics(self.export_metrics())
                    except Exception:
                        pass

                if self._stats.stalled:
                    logging.warning(
                        f"[FirehoseHealth] STALLED {self._stats.stall_seconds:.1f}s "
                        f"(pps={self._stats.pps:.2f}, backlog={self._stats.backlog_seconds:.1f}s)"
                    )
            except Exception as e:
                logging.exception(f"[FirehoseHealth] loop error: {e}")

            await asyncio.sleep(poll_every)


# Singleton (import and use directly)
firehose_health_monitor = FirehoseHealthMonitor()
