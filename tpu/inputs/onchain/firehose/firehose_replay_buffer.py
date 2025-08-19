# modules/firehose/firehose_replay_buffer.py

import asyncio
import logging
import time
from collections import deque

# Stores recent events for replay or reflection
REPLAY_WINDOW_SECONDS = 600  # 10 minutes
MAX_EVENTS = 1000

replay_buffer = deque()

def store_event(event: dict):
    try:
        event["timestamp"] = time.time()
        replay_buffer.append(event)

        while len(replay_buffer) > MAX_EVENTS:
            replay_buffer.popleft()

    except Exception as e:
        logging.warning(f"[ReplayBuffer] Failed to store event: {e}")


def get_recent_events(filter_fn=None) -> list:
    now = time.time()
    recent = []

    try:
        for event in list(replay_buffer):
            age = now - event.get("timestamp", 0)
            if age <= REPLAY_WINDOW_SECONDS:
                if filter_fn is None or filter_fn(event):
                    recent.append(event)
        return recent

    except Exception as e:
        logging.warning(f"[ReplayBuffer] Error fetching recent events: {e}")
        return []
