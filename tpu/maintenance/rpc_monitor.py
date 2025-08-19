# /rpc_monitor.py

import asyncio
import time

from core.telegram_interface import send_telegram_alert
from utils.logger import log_event
from utils.rpc_loader import _cooldown_rpcs, _rpc_pool, cleanup_cooldowns

CHECK_INTERVAL = 90  # seconds
ALERT_COOLDOWN = 300  # seconds between alert messages

last_alert_time = 0

async def rpc_monitor_loop():
    global last_alert_time

    log_event("📡 RPC Monitor loop started")

    while True:
        cleanup_cooldowns()
        cooldown_count = len(_cooldown_rpcs)
        total_rpcs = len(_rpc_pool)

        if cooldown_count >= total_rpcs and total_rpcs > 0:
            now = time.time()
            if now - last_alert_time > ALERT_COOLDOWN:
                last_alert_time = now
                message = (
                    f"🚨 *ALL RPCs in cooldown!*\n"
                    f"Cooldown Count: {cooldown_count}/{total_rpcs}\n"
                    f"❌ Nyx may not function properly until an RPC recovers."
                )
                await send_telegram_alert(message)
                log_event("🚨 All RPCs are in cooldown — alert sent!")
            else:
                log_event("⚠️ All RPCs down, alert throttled.")
        else:
            log_event(f"🛰️ RPCs OK — {total_rpcs - cooldown_count}/{total_rpcs} available")

        await asyncio.sleep(CHECK_INTERVAL)

