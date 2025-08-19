# utils/rpc_loader.py
import asyncio
import json
import os
import random
import time

from utils.logger import log_event

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RPC_PATH = os.path.join(BASE_DIR, "..", "..", "nyx","inputs","onchain","rpcs", "rpc_endpoints.json")
RPC_PATH = os.path.normpath(RPC_PATH)  # type: ignore

ROTATE_MIN = 120  # seconds
ROTATE_MAX = 300  # seconds
FAILURE_THRESHOLD = 3
COOLDOWN_PERIOD = 600  # seconds (10 mins)

# === Runtime state ===
_rpc_pool = []
_rpc_failures = {}
_cooldown_rpcs = {}
_current_rpc = None

# Firehose marker
FIREHOSE_ACTIVE = True  # v2.0 defaults to Firehose streaming

def load_rpcs(chain="solana"):
    """
    Load RPC endpoints from rpc_endpoints.json for backup/fallback.
    """
    global _rpc_pool, _current_rpc
    try:
        with open(RPC_PATH, "r") as f:
            data = json.load(f)
        _rpc_pool = data.get(chain, [])
        if _rpc_pool:
            _current_rpc = _rpc_pool[0]  # ✅ Set fallback current RPC
        log_event(f"🔁 Loaded {len(_rpc_pool)} RPCs for {chain}")
    except Exception as e:
        log_event(f"❌ Failed to load RPCs: {e}")
        _rpc_pool = []
        _current_rpc = None

def get_random_rpc(chain="solana"):
    """
    Returns a healthy RPC endpoint or a random fallback.
    """
    available = [rpc for rpc in _rpc_pool if rpc not in _cooldown_rpcs]
    if not available:
        log_event("⚠️ No healthy RPCs available, using full pool as fallback.")
        available = _rpc_pool
    return random.choice(available) if available else None

def report_rpc_failure(rpc_url):
    """
    Marks an RPC endpoint as failing and places it on cooldown if threshold exceeded.
    """
    if not rpc_url:
        return
    _rpc_failures[rpc_url] = _rpc_failures.get(rpc_url, 0) + 1
    if _rpc_failures[rpc_url] >= FAILURE_THRESHOLD:
        _cooldown_rpcs[rpc_url] = time.time() + COOLDOWN_PERIOD
        log_event(f"🧊 RPC on cooldown: {rpc_url}")

def cleanup_cooldowns():
    """
    Removes RPCs from cooldown once their cooldown period expires.
    """
    now = time.time()
    to_remove = [rpc for rpc, expiry in _cooldown_rpcs.items() if now > expiry]
    for rpc in to_remove:
        del _cooldown_rpcs[rpc]
        log_event(f"✅ RPC cooldown expired: {rpc}")

def get_active_rpc():
    if not _current_rpc and _rpc_pool:
        return _rpc_pool[0]
    return _current_rpc


async def rpc_rotation_loop():
    """
    Rotates through RPCs as a backup if Firehose is down.
    """
    global _current_rpc
    while True:
        cleanup_cooldowns()

        if FIREHOSE_ACTIVE:
            # Firehose is primary — no need to rotate RPC frequently.
            _current_rpc = _rpc_pool[0] if _rpc_pool else None
            await asyncio.sleep(ROTATE_MAX)
            continue

        # Backup mode: rotate through RPCs
        _current_rpc = get_random_rpc()
        log_event(f"🔁 Rotating to new RPC: {_current_rpc}")
        await asyncio.sleep(random.randint(ROTATE_MIN, ROTATE_MAX))


# === Startup init ===
load_rpcs()
