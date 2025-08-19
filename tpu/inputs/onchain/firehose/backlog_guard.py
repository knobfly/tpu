# -*- coding: utf-8 -*-
"""
Backlog guard: computes how far behind the stream is vs wall clock,
and feeds that lag into FirehoseHealthMonitor.
"""

import asyncio
import logging
import time
from typing import Optional

from inputs.onchain.firehose.firehose_health_monitor import firehose_health_monitor
from utils.logger import log_event
from utils.service_status import update_status

# You need a way to get the latest block/packet timestamp.
# If packet_listener tracks the last_onchain_ts, import it here:
try:
    from inputs.onchain.firehose.packet_listener import (
        get_last_onchain_ts,  # you add this helper there
    )
except Exception:
    get_last_onchain_ts = None  # type: ignore


async def run_backlog_guard(poll_every: float = 2.0, warn_if_lag_s: float = 3.0):
    log_event("🛡️ Firehose BacklogGuard started.")
    while True:
        try:
            update_status("firehose_backlog_guard")
            if get_last_onchain_ts:
                last_ts: Optional[float] = get_last_onchain_ts()
            else:
                last_ts = None

            if last_ts:
                lag = max(0.0, time.time() - last_ts)
                firehose_health_monitor.update_backlog_seconds(lag)
                if lag > warn_if_lag_s:
                    logging.warning(f"[BacklogGuard] Firehose lagging behind: {lag:.2f}s")
        except Exception as e:
            logging.warning(f"[BacklogGuard] error: {e}")

        await asyncio.sleep(poll_every)
