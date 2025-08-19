import asyncio  # Needed for rotation loop
import json
import os
import random
import time
from typing import Optional

import websockets

WS_PATH = "/home/ubuntu/nyx/config/websockets.json"
ROTATE_MIN = 120
ROTATE_MAX = 300
FAILURE_THRESHOLD = 3
COOLDOWN_TIME = 600

_ws_pool = []
_ws_failures = {}
_ws_cooldowns = {}
_current_ws = None


def load_websockets(chain="solana"):
    global _ws_pool, _current_ws
    try:
        with open(WS_PATH, "r") as f:
            data = json.load(f)
        _ws_pool = data.get(chain, [])
        print(f"🔁 Loaded {len(_ws_pool)} WebSocket URLs for {chain}")

        # Pick one immediately if available
        if _ws_pool:
            _current_ws = random.choice(_ws_pool)
            print(f"✅ Initial WebSocket set: {_current_ws}")
        else:
            _current_ws = None
            print("⚠️ No WebSockets found in pool.")
    except Exception as e:
        print(f"❌ Failed to load websockets.json: {e}")
        _ws_pool = []
        _current_ws = None


def get_active_ws() -> Optional[str]:
    global _current_ws
    if not _current_ws:
        print("⚠️ No active WebSocket set. Reloading pool...")
        load_websockets()
        if not _current_ws:
            print("⚠️ Still no active WebSocket — rotation loop may not be running yet.")
    return _current_ws


def report_ws_failure(ws_url: str):
    _ws_failures[ws_url] = _ws_failures.get(ws_url, 0) + 1
    if _ws_failures[ws_url] >= FAILURE_THRESHOLD:
        _ws_cooldowns[ws_url] = time.time() + COOLDOWN_TIME
        print(f"🧊 WebSocket cooldown: {ws_url}")


def cleanup_ws_cooldowns():
    now = time.time()
    expired = [ws for ws, t in _ws_cooldowns.items() if now > t]
    for ws in expired:
        del _ws_cooldowns[ws]
        print(f"✅ WebSocket recovered: {ws}")


async def websocket_rotation_loop():
    global _current_ws
    load_websockets()  # Also picks initial
    while True:
        cleanup_ws_cooldowns()
        candidates = [ws for ws in _ws_pool if ws not in _ws_cooldowns]
        if not candidates:
            print("⚠️ All WebSocket URLs in cooldown. Falling back to full pool.")
            candidates = _ws_pool.copy()

        if candidates:
            _current_ws = random.choice(candidates)
            print(f"🔁 Rotated to WebSocket: {_current_ws}")
        else:
            _current_ws = None
            print("❌ No WebSockets available to rotate.")

        await asyncio.sleep(random.randint(ROTATE_MIN, ROTATE_MAX))
