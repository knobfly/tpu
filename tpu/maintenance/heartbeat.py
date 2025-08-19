import asyncio
from datetime import datetime

from exec.open_position_tracker import open_position_tracker
from inputs.onchain.webhook_listener import WebhookListener
from memory.memory_decay_scheduler import memory_decay_scheduler
from special.ai_self_tuner import ai_self_tuner
from strategy.strategy_memory import decay_keywords
from utils.logger import log_event
from utils.service_status import update_status

HEARTBEAT_INTERVAL = 180  # every 3 minutes

# === Heartbeat Loop ===
async def start_heartbeat():
    log_event("💓 Heartbeat started")
    update_status("heartbeat")

    while True:
        try:
            timestamp = datetime.utcnow().isoformat()
            log_event(f"🔁 [Heartbeat] Tick at {timestamp}")

            # ✅ Check open token positions
            log_event("🔍 Checking tracked token positions...")
            await open_position_tracker.check_positions()

            # 🧠 Passive self-tuning for Nyx
            log_event("🛠️ Running passive AI self-tuner...")
            await ai_self_tuner.run_passive_tune()

            # 🧹 Memory decay scheduler (old keywords, strategies)
            log_event("🧼 Running memory decay pass...")
            await memory_decay_scheduler.run_memory_decay()
            # 🧠 Meta keyword decay (strategy pruning)
            try:
                decay_keywords()
                log_event("🧠 Keyword decay completed.")
            except Exception as e:
                log_event(f"⚠️ Keyword decay failed: {e}")

            # 🔗 Webhook listener check (if it drops or dies silently)
            log_event("🔗 Running webhook health check...")
            await WebhookListener.verify_alive()


        except Exception as e:
            log_event(f"❌ [HEARTBEAT] Runtime error: {e}")

        await asyncio.sleep(HEARTBEAT_INTERVAL)
