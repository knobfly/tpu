# /honeypot_similarity_scanner.py

import asyncio
import datetime
import difflib
import hashlib
import json
import logging
import os
from typing import Dict, Optional, Tuple

from librarian.data_librarian import librarian
from utils.fetch_bytecode import fetch_contract_bytecode
from utils.logger import log_event
from utils.rpc_loader import get_active_rpc, report_rpc_failure
from utils.service_status import update_status

# === Config ===
KNOWN_RUGS_FILE = "/home/ubuntu/nyx/runtime/data/honeypot_signatures.json"
SIMILARITY_THRESHOLD = 0.85
MAX_PENALTY = 25
BASE_PENALTY = 10
CACHE_TTL_SECONDS = 3600

# In-memory TTL cache (token -> (timestamp, penalty))
_similarity_cache: Dict[str, Tuple[float, int]] = {}

update_status("honeypot_similarity_scanner")

# ---------------------------
# Utility Functions
# ---------------------------

def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _now_ts() -> float:
    return datetime.datetime.utcnow().timestamp()


def _ensure_dir(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)


def _load_json(path: str, default):
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"[HoneypotScanner] Failed to load {path}: {e}")
        return default


def _save_json(path: str, data: dict):
    path = os.path.expanduser(path)
    try:
        _ensure_dir(path)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.error(f"[HoneypotScanner] Failed to save {path}: {e}")


# ---------------------------
# Signature Store
# ---------------------------

def load_known_signatures() -> Dict[str, str]:
    return _load_json(KNOWN_RUGS_FILE, {})


def save_known_signatures(data: Dict[str, str]):
    _save_json(KNOWN_RUGS_FILE, data)


def save_new_rug_pattern(token_address: str, label: Optional[str] = None):
    try:
        bytecode = _run_async(fetch_contract_bytecode(token_address, rpc_url=get_active_rpc()))
        if not bytecode:
            logging.warning(f"[HoneypotScanner] No bytecode to learn from for {token_address}")
            return

        known = load_known_signatures()
        label = label or f"rug_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        known[label] = bytecode
        save_known_signatures(known)

        logging.warning(f"💀 Learned new rug pattern: {label} (from {token_address})")
    except Exception as e:
        report_rpc_failure(get_active_rpc())
        logging.error(f"[HoneypotScanner] Failed to save rug pattern: {e}")


# ---------------------------
# Similarity Core
# ---------------------------

def _compute_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _best_similarity_match(bytecode: str, known_patterns: Dict[str, str]) -> Tuple[Optional[str], float]:
    best_label, best_score = None, 0.0
    for label, pattern in known_patterns.items():
        score = _compute_similarity(bytecode, pattern)
        if score > best_score:
            best_label, best_score = label, score
    return best_label, best_score


# ---------------------------
# Async Evaluation
# ---------------------------

async def _evaluate_similarity_penalty_async(token_address: str) -> int:
    try:
        update_status("honeypot_similarity_scanner")

        cached = _similarity_cache.get(token_address)
        if cached and (_now_ts() - cached[0]) < CACHE_TTL_SECONDS:
            return cached[1]

        rpc = get_active_rpc()
        bytecode = await fetch_contract_bytecode(token_address, rpc_url=rpc)
        if not bytecode:
            _similarity_cache[token_address] = (_now_ts(), 0)
            return 0

        known_patterns = load_known_signatures()
        if not known_patterns:
            _similarity_cache[token_address] = (_now_ts(), 0)
            return 0

        label, score = _best_similarity_match(bytecode, known_patterns)
        if score >= SIMILARITY_THRESHOLD:
            penalty = int(BASE_PENALTY + (score - SIMILARITY_THRESHOLD) * (MAX_PENALTY - BASE_PENALTY) / (1.0 - SIMILARITY_THRESHOLD))
            penalty = max(BASE_PENALTY, min(penalty, MAX_PENALTY))

            logging.warning(f"⚠️ Honeypot similarity match for {token_address} → {label} ({score:.2f}), penalty={penalty}")
            log_event(f"[HoneypotScanner] {token_address} matched {label} @ {score:.2f} (penalty={penalty})")

            try:
                await librarian.record_token_result(
                    token=token_address,
                    result="rug",
                    score=0,
                    token_type="bytecode_match",
                    source="honeypot_similarity"
                )
            except Exception:
                pass

            _similarity_cache[token_address] = (_now_ts(), penalty)
            return penalty

        _similarity_cache[token_address] = (_now_ts(), 0)
        return 0

    except Exception as e:
        report_rpc_failure(get_active_rpc())
        logging.error(f"[HoneypotScanner] Error for {token_address}: {e}")
        _similarity_cache[token_address] = (_now_ts(), 0)
        return 0


def get_similarity_penalty(token_address: str) -> int:
    """
    Sync-safe entry point for penalty scoring.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        return loop.create_task(_evaluate_similarity_penalty_async(token_address)).result()  # type: ignore
    else:
        return _run_async(_evaluate_similarity_penalty_async(token_address))


# ---------------------------
# Async Runner
# ---------------------------

def _run_async(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        return asyncio.get_event_loop().run_until_complete(coro)
