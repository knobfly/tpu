# librarian_scan.py
# Scanning and indexing routines split from data_librarian.py

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

class LibrarianScan:
    def __init__(self, librarian):
        self.librarian = librarian

    async def initial_scan(self):
        for name, root in self.librarian.JSONL_SOURCES.items():
            if not root.exists():
                continue
            for fpath in sorted(root.rglob("*.jsonl")):
                self.librarian._file_offsets.setdefault(fpath, 0)
        await self.scan_all_files()

    async def disk_scan_loop(self):
        while True:
            try:
                await self.scan_all_files()
            except Exception as e:
                logging.warning(f"[Librarian] Disk scan error: {e}")
            await asyncio.sleep(self.librarian.DISK_SCAN_INTERVAL_SEC)

    async def status_loop(self):
        while True:
            try:
                self.librarian.update_status("data_librarian")
                self.librarian._last_status_beat = time.time()
            except Exception as e:
                logging.debug(f"[Librarian] status loop warning: {e}")
            await asyncio.sleep(self.librarian.STATUS_HEARTBEAT_SECONDS)

    async def scan_all_files(self):
        for name, root in self.librarian.JSONL_SOURCES.items():
            if not root.exists():
                continue
            for fpath in sorted(root.rglob("*.jsonl")):
                await self.tail_jsonl_file(fpath, name)

    async def tail_jsonl_file(self, fpath: Path, logical_source: str):
        try:
            last_off = self.librarian._file_offsets.get(fpath, 0)
            with fpath.open("rb") as f:
                f.seek(last_off)
                while True:
                    line = f.readline()
                    if not line:
                        break
                    self.librarian._file_offsets[fpath] = f.tell()
                    try:
                        raw = json.loads(line.decode("utf-8"))
                    except Exception:
                        continue
                    await self.normalize_and_index(raw, logical_source)
        except FileNotFoundError:
            self.librarian._file_offsets.pop(fpath, None)
        except Exception as e:
            logging.warning(f"[Librarian] tail_jsonl_file error {fpath}: {e}")

    async def normalize_and_index(self, raw: Dict[str, Any], logical_source: str):
        if not isinstance(raw, dict):
            return
        ts = raw.get("ts") or raw.get("timestamp") or time.time()
        event_type = raw.get("type") or logical_source
        payload = raw.get("payload") or raw
        ev = {"ts": ts, "type": event_type, "payload": payload, "_src": logical_source}
        async with self.librarian._lock:
            self.librarian._events_by_type[event_type].append(ev)
            token = self.librarian._find_token(payload)
            wallet = self.librarian._find_wallet(payload)
            # Delegate event handling to index module
            if event_type in ("scoring", "snipe_score", "trade_score"):
                self.librarian.index.handle_scoring_event(payload, ev, token)
                self.librarian._counters["events_ingested"] += 1
                if event_type in ("solana_log", "stream_event", "logsSubscribe"):
                    self.librarian._counters["stream_events"] += 1
            if event_type in ("trade", "buy", "sell", "auto_sell_result", "trade_result"):
                self.librarian.index.handle_trade_event(payload, ev, token, wallet)
                self.librarian._counters["events_ingested"] += 1
                if event_type in ("solana_log", "stream_event", "logsSubscribe"):
                    self.librarian._counters["stream_events"] += 1
            if event_type in ("wallet_event", "wallet_cluster", "wallet_overlap", "wallet_signal"):
                self.librarian.index.handle_wallet_event(payload, ev, token, wallet)
                self.librarian._counters["events_ingested"] += 1
                if event_type in ("solana_log", "stream_event", "logsSubscribe"):
                    self.librarian._counters["stream_events"] += 1
            if event_type in ("chart_pattern", "ohlcv_update", "trend_eval"):
                self.librarian.index.handle_chart_event(payload, ev, token)
                self.librarian._counters["events_ingested"] += 1
                if event_type in ("solana_log", "stream_event", "logsSubscribe"):
                    self.librarian._counters["stream_events"] += 1
            if token:
                self.librarian.index.index_token_event(token, ev)
            if wallet:
                self.librarian.index.index_wallet_event(wallet, ev)

    def trim_token_history(self, max_entries: int = 500, max_age_days: int = None):
        try:
            history_store = getattr(self.librarian, "token_history_store", {})
            if not isinstance(history_store, dict):
                return
            now = datetime.utcnow().timestamp()
            max_age_seconds = max_age_days * 86400 if max_age_days else None
            for token, events in history_store.items():
                if max_age_seconds:
                    events = [e for e in events if isinstance(e, dict) and now - e.get("timestamp", now) <= max_age_seconds]
                if isinstance(events, list) and len(events) > max_entries:
                    events = events[-max_entries:]
                history_store[token] = events
        except Exception as e:
            logging.warning(f"[Librarian] Failed to trim token history: {e}")
