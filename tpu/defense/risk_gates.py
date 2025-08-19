# /defense/risk_gates.py
from __future__ import annotations

import logging
from typing import List, Tuple


def basic_trade_gate(
    token: str,
    *,
    blacklist_check,
    honeypot_check,
    lp_status_check,
) -> Tuple[bool, List[str]]:
    """
    Central risk gate: returns (ok, flags)
    - blacklist_check: callable(str)->bool
    - honeypot_check: callable(str)->bool
    - lp_status_check: callable(str)->str  ('locked'|'unlocked'|unknown)
    """
    flags: List[str] = []
    try:
        if blacklist_check(token):
            flags.append("blacklisted")
    except Exception:
        pass
    try:
        if honeypot_check(token):
            flags.append("honeypot")
    except Exception:
        pass
    try:
        lp = lp_status_check(token)
        if lp == "unlocked":
            flags.append("lp_unlocked")
    except Exception:
        pass
    return (len(flags) == 0), flags
