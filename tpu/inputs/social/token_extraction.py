# inputs/social/token_extraction.py

import logging
import re
from datetime import datetime
from typing import List

from core.live_config import config
from utils.universal_input_validator import validate_token_record

try:
    from utils.token_matcher import best_symbol_match
except Exception:
    best_symbol_match = None  # fallback: no

# Common Solana token formats or contract mentions
TOKEN_REGEX = re.compile(r"\b(?:[A-Za-z0-9]{32,44})\b")

def extract_tokens_from_text(text: str, *, group: str = "", symbol: str = "") -> List[str]:
    """
    Extracts possible Solana token addresses or contract references from group chat text.
    Optionally validates via validate_token_record if group/symbol context is passed.
    """
    if not text or not isinstance(text, str):
        return []

    # raw extraction
    tokens = TOKEN_REGEX.findall(text)

    # symbol normalizing if config + matcher available
    canon = set(config.get("canonical_symbols", []))  # e.g., ["SOL","USDC","BONK",...]
    if best_symbol_match and canon:
        normalized = set()
        for t in tokens:
            m = best_symbol_match(t.upper(), list(canon))
            normalized.add(m or t.upper())
        tokens = list(normalized)

    # optional validation: skip tokens without a mint if context given
    if group or symbol:
        validated_tokens = []
        for mint in tokens:
            rec = validate_token_record({
                "group": group,
                "symbol": symbol,
                "message": text,
                "mint": mint,
                "timestamp": datetime.utcnow().isoformat(),
            })
            if rec:
                validated_tokens.append(rec["mint"])
        return validated_tokens

    return list(set(tokens))  # remove duplicates
