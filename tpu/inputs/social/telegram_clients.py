# inputs/social/telegram_clients.py
import asyncio
import fcntl
import logging
import os
import sqlite3
from typing import Optional

from core.live_config import config
from telethon import TelegramClient

_user_client: Optional[TelegramClient] = None
_user_lock = asyncio.Lock()

def _user_session_path() -> str:
    p = (config.get("telegram_user", {}).get("session_path")
         or "/home/ubuntu/nyx/runtime/telegram/nyx_user.session")
    # ensure unique and consistent
    return p.replace(".session", "_user.session")

class _FileLock:
    def __init__(self, path): self._p = path; self._fh=None
    def __enter__(self):
        os.makedirs(os.path.dirname(self._p), exist_ok=True)
        self._fh = open(self._p, "a+"); fcntl.flock(self._fh, fcntl.LOCK_EX); return self
    def __exit__(self, *exc):
        try: fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            try: self._fh.close()
            except Exception: pass

async def ensure_user_client_started() -> TelegramClient:
    """Singleton Telethon user client. Starts once, with sqlite lock/retry."""
    global _user_client
    if _user_client and _user_client.is_connected():
        return _user_client

    async with _user_lock:
        if _user_client and _user_client.is_connected():
            return _user_client

        cfg = config.get("telegram_user") or {}
        api_id = cfg.get("api_id")
        api_hash = cfg.get("api_hash")
        if not (api_id and api_hash):
            raise RuntimeError("telegram_user api_id/api_hash missing")

        session_path = _user_session_path()
        lockfile = session_path + ".lock"

        with _FileLock(lockfile):
            cl = TelegramClient(session_path, api_id, api_hash)
            # retry on “database is locked”
            for attempt in range(6):
                try:
                    await cl.start()
                    break
                except sqlite3.OperationalError as e:
                    if "database is locked" in str(e).lower() and attempt < 5:
                        await asyncio.sleep(1 + 0.5*attempt)
                        continue
                    raise
            _user_client = cl
            return _user_client

async def shutdown_user_client():
    global _user_client
    if _user_client:
        try: await _user_client.disconnect()
        except Exception: pass
        _user_client = None
