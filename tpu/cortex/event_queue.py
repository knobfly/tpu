
import asyncio
import logging

# Async queue for cortex/scoring pipeline
event_queue = asyncio.Queue(maxsize=5000)

async def enqueue_event_for_scoring(event: dict):
    try:
        await event_queue.put(event)
        logging.debug(f"[EventQueue] Enqueued tx {event.get('tx_hash')} for scoring")
    except asyncio.QueueFull:
        logging.warning("[EventQueue] Queue full, dropped event")
    except Exception as e:
        logging.error(f"[EventQueue] Failed to enqueue event: {e}")

async def dequeue_event():
    try:
        return await event_queue.get()
    except Exception as e:
        logging.error(f"[EventQueue] Failed to dequeue event: {e}")
        return None
