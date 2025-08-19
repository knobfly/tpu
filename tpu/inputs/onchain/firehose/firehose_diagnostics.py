# /firehose/firehose_diagnostics.py

import logging
import time

from cortex import event_queue
from inputs.onchain.firehose import firehose_replay_buffer, firehose_watchdog


def get_firehose_status() -> dict:
    try:
        now = time.time()

        packet_age = now - firehose_watchdog.last_packet_time
        event_age = now - firehose_watchdog.last_processed_event

        queue_size = event_queue.event_queue.qsize()
        buffer_size = len(firehose_replay_buffer.replay_buffer)

        status = {
            "queue_size": queue_size,
            "buffer_size": buffer_size,
            "time_since_packet": round(packet_age, 1),
            "time_since_event": round(event_age, 1),
            "is_healthy": (packet_age < 20 and event_age < 15 and queue_size < 4000),
        }

        return status

    except Exception as e:
        logging.error(f"[FirehoseDiagnostics] Failed to collect status: {e}")
        return {
            "queue_size": -1,
            "buffer_size": -1,
            "time_since_packet": -1,
            "time_since_event": -1,
            "is_healthy": False
        }
