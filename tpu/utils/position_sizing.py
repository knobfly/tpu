# /utils/position_sizing.py
from __future__ import annotations

import math


def kelly_fraction(win_prob: float, win_loss_ratio: float) -> float:
    """
    Kelly f* = p - (1-p)/b, where b = win_loss_ratio. Clamped to [0,1].
    """
    p = max(0.0, min(1.0, float(win_prob)))
    b = max(1e-6, float(win_loss_ratio))
    f = p - (1.0 - p) / b
    return max(0.0, min(1.0, f))

def size_from_score(
    final_score: float,
    base_sol: float,
    *,
    vol_tag: str = "unknown",
    kelly_p: float = 0.55,
    kelly_b: float = 1.5,
    hard_cap_sol: float = 2.0,
) -> float:
    """
    Map your 0..100 score to a multiplier and blend with Kelly.
    Applies a quick volatility haircut.
    """
    s = max(0.0, min(100.0, final_score))
    # score multiplier (0..3x)
    mult = 0.3 + 2.7 * (s / 100.0)

    # Kelly base fraction
    f_k = kelly_fraction(kelly_p, kelly_b)

    # volatility haircut
    haircuts = {"high": 0.5, "medium": 0.75, "low": 1.0, "unknown": 0.8}
    h = haircuts.get(vol_tag, 0.8)

    amt = base_sol * mult * f_k * h
    return round(min(amt, hard_cap_sol), 6)
