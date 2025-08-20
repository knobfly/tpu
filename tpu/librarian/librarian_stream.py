# librarian_stream.py
# Stream ingestion, event normalization, indexing, and helpers for DataLibrarian.

import asyncio
import json
import time
import logging
from collections import defaultdict, deque
from typing import Dict, Deque, Optional, List

MAX_EVENTS_PER_TYPE = 5000

def _find_token(payload: dict) -> Optional[str]:
	for key in ("token", "token_address", "mint", "address"):
		v = payload.get(key)
		if isinstance(v, str) and len(v) > 20:
			return v
	return None

def _find_wallet(payload: dict) -> Optional[str]:
	for key in ("wallet", "wallet_address", "owner", "from", "to"):
		v = payload.get(key)
		if isinstance(v, str) and len(v) > 20:
			return v
	return None

class LibrarianStream:
	def __init__(self):
		self._events_by_type: Dict[str, Deque[dict]] = defaultdict(lambda: deque(maxlen=MAX_EVENTS_PER_TYPE))
		self._lock = asyncio.Lock()
		self._tokens: Dict[str, any] = {}
		self._wallets: Dict[str, any] = {}
		self._counters = {"events_ingested": 0, "stream_events": 0}

	async def record_event(self, event_type: str, payload: dict):
		ts = payload.get("timestamp") or payload.get("ts") or time.time()
		ev = {"ts": ts, "type": event_type, "payload": payload}
		async with self._lock:
			self._events_by_type[event_type].append(ev)
			token = _find_token(payload)
			wallet = _find_wallet(payload)
			if token:
				self._counters["events_ingested"] += 1
				if event_type in ("solana_log", "stream_event", "logsSubscribe"):
					self._counters["stream_events"] += 1
			if wallet:
				self._counters["events_ingested"] += 1
				if event_type in ("solana_log", "stream_event", "logsSubscribe"):
					self._counters["stream_events"] += 1

	async def get_recent_events(self, event_type: Optional[str] = None, limit: int = 100) -> List[dict]:
		async with self._lock:
			if event_type is None:
				merged = []
				for dq in self._events_by_type.values():
					merged.extend(list(dq)[-limit:])
				merged.sort(key=lambda e: e["ts"])
				return merged[-limit:]
			return list(self._events_by_type[event_type])[-limit:]

