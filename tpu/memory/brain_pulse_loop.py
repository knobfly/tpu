import asyncio

from memory.memory_decay_scheduler import run_memory_decay
from memory.memory_sync_service import run_memory_sync
from utils.logger import log_event
from utils.service_status import update_status


async def run_brain_pulse_loop(interval: int = 60):
    log_event("🧠 Brain Pulse loop initiated...")
    await asyncio.sleep(3)

    while True:
        update_status("brain_loop")
        log_event("🧠 Brain Pulse: syncing memory + scanning health")

        try:
            await run_memory_sync()
            run_memory_decay()
        except Exception as e:
            log_event(f"[BrainPulse] ⚠️ Error during pulse: {e}")

        await asyncio.sleep(interval)
