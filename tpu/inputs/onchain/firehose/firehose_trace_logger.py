# /firehose/firehose_trace_logger.py

import json
import logging
import os
import time

from utils.logger import log_event

TRACE_LOG = "/home/ubuntu/nyx/runtime/logs/firehose_traces.jsonl"
MAX_LOG_SIZE = 50 * 1024 * 1024  # 50 MB

def write_trace(event: dict, score: float = 0, action: str = "none"):
    """
    Write a trace entry for every firehose event processed.
    """
    try:
        entry = {
            "timestamp": time.time(),
            "event": event,
            "score": score,
            "action": action,
        }
        with open(TRACE_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")

        _truncate_trace_log()
    except Exception as e:
        logging.warning(f"[FirehoseTraceLogger] Failed to write trace: {e}")


def _truncate_trace_log():
    """
    Ensure trace log doesn't exceed MAX_LOG_SIZE.
    """
    try:
        if os.path.exists(TRACE_LOG) and os.path.getsize(TRACE_LOG) > MAX_LOG_SIZE:
            with open(TRACE_LOG, "r") as f:
                lines = f.readlines()[-1000:]  # keep last 1000
            with open(TRACE_LOG, "w") as f:
                f.writelines(lines)
            log_event("[FirehoseTraceLogger] Trace log truncated to last 1000 entries.")
    except Exception as e:
        logging.warning(f"[FirehoseTraceLogger] Failed truncation: {e}")
