# /auto_rug_blacklist.py

import asyncio
import json
import os
from datetime import datetime

from librarian.data_librarian import librarian
from special.insight_logger import log_scanner_insight
from strategy.strategy_memory import get_tagged_tokens
from utils.logger import log_event
from utils.service_status import update_status
from utils.universal_input_validator import coerce_to_dict

BLACKLIST_PATH = os.path.expanduser("/home/ubuntu/nyx/runtime/memory/blacklisted_tokens.json")
CHECK_INTERVAL = 60  # seconds

_blacklisted_tokens = set()

# === Load or initialize blacklist ===
BLACKLIST = set()
if os.path.exists(BLACKLIST_PATH):
    try:
        with open(BLACKLIST_PATH, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                BLACKLIST = set(data)
            else:
                log_event("⚠️ [AutoBlacklist] Invalid format in blacklist file. Expected list.")
    except Exception as e:
        log_event(f"⚠️ [AutoBlacklist] Error loading blacklist file: {e}")


async def run():
    log_event("🔒 AutoRug Blacklist running...")
    while True:
        update_status("auto_rug_blacklist")
        await check_and_update_blacklist()
        await asyncio.sleep(CHECK_INTERVAL)


async def check_and_update_blacklist():
    try:
        tokens = get_tagged_tokens()
        tokens = coerce_to_dict(tokens)

        new_blacklisted = []

        for token_address, info in tokens.items():
            info = coerce_to_dict(info, f"auto_rug_blacklist.token:{token_address}")
            tags = info.get("tags", [])

            if not isinstance(tags, list):
                log_event(f"⚠️ [AutoBlacklist] Invalid tags format for {token_address}: {type(tags).__name__}")
                continue

            if "rugged" in tags or "honeypot" in tags:
                if token_address not in BLACKLIST:
                    BLACKLIST.add(token_address)
                    new_blacklisted.append(token_address)
                    log_event(f"🚫 [AutoBlacklist] Blacklisted token: {token_address}")
                    log_scanner_insight("rug_blacklist", token_address, 100, "auto_rug_tagged")
                    await librarian.inject_blacklist_flag(token_address, reason="auto_rug_blacklist")

        if new_blacklisted:
            with open(BLACKLIST_PATH, "w") as f:
                json.dump(sorted(BLACKLIST), f)

    except Exception as e:
        log_event(f"⚠️ [AutoBlacklist] Failed to check/update blacklist: {e}")

def load_blacklist():
    global _blacklisted_tokens
    if not os.path.exists(BLACKLIST_PATH):
        _blacklisted_tokens = set()
        return
    try:
        with open(BLACKLIST_PATH, "r") as f:
            data = json.load(f)
            _blacklisted_tokens = set(data)
    except Exception as e:
        logging.warning(f"[Blacklist] Failed to load: {e}")
        _blacklisted_tokens = set()

def save_blacklist():
    try:
        os.makedirs(os.path.dirname(BLACKLIST_PATH), exist_ok=True)
        with open(BLACKLIST_PATH, "w") as f:
            json.dump(list(_blacklisted_tokens), f, indent=2)
    except Exception as e:
        logging.warning(f"[Blacklist] Failed to save: {e}")

def is_blacklisted_token(token_address: str) -> bool:
    return token_address.lower() in _blacklisted_tokens

def add_token_to_blacklist(token_address: str):
    token = token_address.lower()
    if token not in _blacklisted_tokens:
        _blacklisted_tokens.add(token)
        save_blacklist()
        logging.info(f"[Blacklist] Added token {token} to auto-rug blacklist.")

def remove_token_from_blacklist(token_address: str):
    token = token_address.lower()
    if token in _blacklisted_tokens:
        _blacklisted_tokens.remove(token)
        save_blacklist()
        logging.info(f"[Blacklist] Removed token {token} from blacklist.")

# Load once on module import
load_blacklist()
