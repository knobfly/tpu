import asyncio
import logging
from datetime import datetime
from typing import Any, Dict

# === Shared memory structure for Nyx runtime ===
_shared_memory: Dict[str, Any] = {
    "tokens": {},
    "wallets": {},
    "signals": {},
    "updated_at": datetime.utcnow().isoformat()
}

def get(key: str, default=None):
    return _shared_memory.get(key, default)

def set(key: str, value: Any):
    _shared_memory[key] = value
    _shared_memory["updated_at"] = datetime.utcnow().isoformat()

def update(section: str, key: str, value: Any):
    if section not in _shared_memory:
        _shared_memory[section] = {}
    _shared_memory[section][key] = value
    _shared_memory["updated_at"] = datetime.utcnow().isoformat()

def dump() -> Dict[str, Any]:
    return _shared_memory.copy()

def reset():
    global _shared_memory
    _shared_memory = {
        "tokens": {},
        "wallets": {},
        "signals": {},
        "updated_at": datetime.utcnow().isoformat()
    }
    logging.info("[SharedRuntime] Memory reset.")

shared_memory = _shared_memory
