# /firehose/rug_signature_engine.py

import logging
import re

RUG_PATTERNS = [
    r"disable\s*trading",
    r"set\s*tax\s*to\s*100",
    r"blacklist",
    r"transfer\s*ownership",
    r"stealth\s*mint",
    r"emergency\s*withdraw",
    r"dev\s*can\s*pull",
    r"liquidity\s*remove",
]

def is_rug_signature(logs: list[str]) -> bool:
    try:
        full_logs = " ".join(logs).lower()
        for pattern in RUG_PATTERNS:
            if re.search(pattern, full_logs):
                return True
        return False
    except Exception as e:
        logging.warning(f"[RugSignature] Error parsing logs: {e}")
        return False

detect_rug_signature = is_rug_signature
