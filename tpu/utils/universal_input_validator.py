# utils/universal_validator.py
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

# ----------------------------
# Generic coercion/ensure utils
# ----------------------------

def ensure_dict(obj: Any, fallback: Optional[dict] = None) -> dict:
    """Ensure the input is a dictionary; handle common near-misses."""
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list) and len(obj) == 1 and isinstance(obj[0], dict):
        return obj[0]
    if fallback is not None:
        return fallback
    logging.warning(f"[Validator] Expected dict, got {type(obj)}. Returning empty dict.")
    return {}

def ensure_str(value: Any, fallback: str = "") -> str:
    """Ensure the input is a string; stringify otherwise."""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        return fallback

def coerce_to_dict(obj: Any) -> dict:
    """
    Convert an object to a dictionary if possible.
    - If already a dict, return it.
    - If list, convert to {index: value}.
    - If has __dict__, return vars(obj).
    - Else return {}.
    """
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list):
        return {i: v for i, v in enumerate(obj)}
    if hasattr(obj, "__dict__"):
        try:
            return vars(obj)
        except Exception:
            pass
    return {}

def coerce_to_list(obj: Any) -> list:
    """Coerce any value to a list (None -> [], list -> list, other -> [obj])."""
    if isinstance(obj, list):
        return obj
    if obj is None:
        return []
    return [obj]

def ensure_list(obj: Any, fallback: Optional[list] = None) -> list:
    """Ensure the input is a list; wrap dicts/values as needed."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        return [obj]
    if fallback is not None:
        return fallback
    logging.warning(f"[Validator] Expected list, got {type(obj)}. Returning empty list.")
    return []

def coerce_float(value: Any, default: float = 0.0) -> float:
    """Safely convert to float."""
    try:
        return float(value)
    except Exception:
        logging.warning(f"[Validator] Could not convert to float: {value}")
        return default

def coerce_int(value: Any, default: int = 0) -> int:
    """Safely convert to int."""
    try:
        return int(value)
    except Exception:
        logging.warning(f"[Validator] Could not convert to int: {value}")
        return default

def safe_parse(data: Any) -> dict:
    """
    Safely parse input to dict if possible. Returns {} on failure.
    Handles both JSON strings and dict-like inputs.
    """
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}

# ----------------------------
# Structure validators
# ----------------------------


def validate_wallet_structure(wallet: Any) -> bool:
    """Basic structure check for wallet object."""
    wallet = ensure_dict(wallet)
    if "address" not in wallet or not isinstance(wallet["address"], str):
        logging.warning(f"[Validator] Wallet missing address: {wallet}")
        return False
    return True

# ----------------------------
# Token address & logging utils
# ----------------------------

def is_valid_token_address(address: Any) -> bool:
    """Simple sanity check for Solana token addresses (loose, non-Base58-strict)."""
    if not isinstance(address, str):
        return False
    return len(address) in (32, 44, 88) and address.isalnum()

def log_validation_warning(message: str, context: Optional[dict] = None) -> None:
    """Standardized validation warning with optional context."""
    log_msg = f"[Validation] ⚠️ {message}"
    if context:
        log_msg += f" | Context: {context}"
    logging.warning(log_msg)

# ----------------------------
# Mint inference from free text
# ----------------------------

_BASE58_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_SOLSCAN_RE = re.compile(r"solscan\.io/token/([1-9A-HJ-NP-Za-km-z]{32,44})", re.I)
_RAYDIUM_RE = re.compile(r"raydium\.io/amm/([1-9A-HJ-NP-Za-km-z]{32,44})", re.I)
_DEXS_RE = re.compile(r"dexscreener\.com/solana/([1-9A-HJ-NP-Za-km-z]{32,44})", re.I)

def _infer_mint_from_text(text: str) -> Optional[str]:
    """Try to infer a mint from known explorer links or base58-ish substrings."""
    for rx in (_SOLSCAN_RE, _RAYDIUM_RE, _DEXS_RE):
        m = rx.search(text or "")
        if m:
            return m.group(1)
    m2 = _BASE58_RE.search(text or "")
    return m2.group(0) if m2 else None

# ----------------------------
# Telegram token record normalizer
# ----------------------------

def validate_token_record(tok: dict) -> Optional[dict]:
    """
    Normalize Telegram-derived token dicts, or return None to skip.
    Expected fields out: symbol, mint, group, message, timestamp (all strings).
    """
    if not isinstance(tok, dict):
        logging.warning("[Validator] token not a dict: %r", tok)
        return None

    symbol = ensure_str(tok.get("symbol"), "")
    message = ensure_str(tok.get("message"), "")
    group = ensure_str(tok.get("group"), "")
    timestamp = ensure_str(tok.get("timestamp"), "")

    mint = tok.get("mint")
    if not mint:
        mint = _infer_mint_from_text(message)
        if not mint:
            # Quietly skip if we truly cannot infer — avoids warning spam
            logging.debug("[Validator] No mint inferred; skipping. tok=%r", tok)
            return None

    return {
        "symbol": symbol,
        "mint": ensure_str(mint, ""),
        "group": group,
        "message": message,
        "timestamp": timestamp,
    }
