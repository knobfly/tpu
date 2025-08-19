# /inputs/onchain/firehose/firehose_watchdog.py

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from core.live_config import config
from inputs.onchain.firehose.firehose_health_monitor import firehose_health_monitor
from inputs.onchain.firehose.firehose_trace_logger import write_trace
from utils.logger import log_event
from utils.service_status import update_status

update_status("firehose_watchdog")

WATCHDOG_INTERVAL = 30        # seconds
ALERT_THRESHOLD = 90          # seconds without events triggers alert

_fallback_triggered = True
_onchain_listener_task = None

# === Compatibility Heartbeat Aliases ===
def update_event_heartbeat():
    """
    Compatibility heartbeat for legacy modules.
    Updates FirehoseHealthMonitor's last packet timestamp.
    """
    try:
        firehose_health_monitor.record_packet()
    except Exception as e:
        logging.warning(f"[FirehoseWatchdog] Failed to record heartbeat: {e}")

# Legacy alias for packet_listener
update_packet_heartbeat = update_event_heartbeat


def check_firehose_health() -> bool:
    """
    Uses FirehoseHealthMonitor metrics instead of maintaining a separate heartbeat.
    """
    metrics = firehose_health_monitor.export_metrics()
    last_ts = metrics.get("firehose.last_packet_ts", 0.0)
    return (time.time() - last_ts) <= ALERT_THRESHOLD


async def send_firehose_alert():
    """Send Telegram alert if firehose is stalled."""
    try:
        if not config.get("telegram_bot_token"):
            return
        from core.telegram_interface import send_telegram_alert
        await send_telegram_alert("⚠️ Firehose appears to be stalled. Attempting recovery with fallback...")
    except Exception as e:
        logging.warning(f"[FirehoseWatchdog] Failed to send alert: {e}")


async def trigger_onchain_fallback(wallet, telegram, trade_executor, auto_sell, logger, frenzy):
    global _onchain_listener_task
    if _onchain_listener_task and not _onchain_listener_task.done():
        logging.info("[FirehoseWatchdog] OnchainListener already running.")
        return

    from inputs.onchain.onchain_listener import OnchainListener
    listener = OnchainListener(wallet, telegram, trade_executor, auto_sell, logger, frenzy)
    _onchain_listener_task = asyncio.create_task(listener.start())
    logging.warning("[FirehoseWatchdog] 🛠️ Fallback OnchainListener started.")


async def watchdog_loop(wallet=None, telegram=None, trade_executor=None, auto_sell=None, logger=None, frenzy=None):
    """
    Loop to monitor firehose health. If firehose stalls, triggers Telegram alert
    and starts OnchainListener fallback after ALERT_THRESHOLD seconds.
    Automatically shuts down fallback if firehose recovers.
    """
    global _fallback_triggered, _onchain_listener_task

    BOOT_GRACE_PERIOD = 90  # seconds after Nyx boot before we start monitoring
    boot_time = time.time()

    log_event("🔍 Firehose watchdog loop started.")
    while True:
        try:
            # Wait until after grace period to begin monitoring
            if time.time() - boot_time < BOOT_GRACE_PERIOD:
                await asyncio.sleep(WATCHDOG_INTERVAL)
                continue

            is_healthy = check_firehose_health()

            if not is_healthy and not _fallback_triggered:
                logging.warning("[FirehoseWatchdog] Firehose appears stalled.")
                await send_firehose_alert()
                await asyncio.sleep(5)  # Grace pause before fallback
                await trigger_onchain_fallback(wallet, telegram, trade_executor, auto_sell, logger, frenzy)
                _fallback_triggered = True

            elif is_healthy and _fallback_triggered:
                logging.info("[FirehoseWatchdog] Firehose recovered — shutting down OnchainListener fallback.")
                if _onchain_listener_task and not _onchain_listener_task.done():
                    _onchain_listener_task.cancel()
                    try:
                        await _onchain_listener_task
                    except asyncio.CancelledError:
                        logging.info("[FirehoseWatchdog] OnchainListener fallback stopped.")
                _fallback_triggered = False

            await asyncio.sleep(WATCHDOG_INTERVAL)

        except Exception as e:
            logging.warning(f"[FirehoseWatchdog] Loop error: {e}")
            await asyncio.sleep(WATCHDOG_INTERVAL)

def is_firehose_alive(max_delay_sec: int = 30) -> bool:
    """
    Quick check for firehose activity, using FirehoseHealthMonitor last packet time.
    """
    metrics = firehose_health_monitor.export_metrics()
    last_ts = metrics.get("firehose.last_packet_ts", 0.0)
    return (time.time() - last_ts) <= max_delay_sec
