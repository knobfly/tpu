# scoring/profile_selector.py
from __future__ import annotations
import math
from typing import Any, Dict, List, Tuple
from scoring.profile_config import load_profiles
from utils.time_utils import now_ts  # or time.time
from utils.logger import log_event

# Simple evaluators for `when` keys you use in YAML
def _passes_when(ctx: Dict[str, Any], when: Dict[str, Any]) -> bool:
    if not isinstance(when, dict):
        return True
    age_m = float(ctx.get("age_minutes", 9_999))
    pool_age_s = float(ctx.get("pool_age_seconds", 9e9))
    token_age_s = float(ctx.get("token_age_seconds", age_m * 60.0))
    # snipe keys
    if "max_pool_age_s" in when and pool_age_s > float(when["max_pool_age_s"]):
        return False
    if "max_token_age_s" in when and token_age_s > float(when["max_token_age_s"]):
        return False
    if "min_bars_1m" in when:
        if int(ctx.get("chart", {}).get("bars_1m", 0)) < int(when["min_bars_1m"]):
            return False
    if "trusted_source_ping" in when:
        want = bool(when["trusted_source_ping"])
        have = bool(ctx.get("trusted_source_ping") or ctx.get("social", {}).get("trusted_source"))
        if have != want:
            return False
    if "bundle_risk" in when:
        want = bool(when["bundle_risk"])
        have = bool(ctx.get("bundle", {}).get("risk") or ctx.get("bundle_risk"))
        if have != want:
            return False
    return True

def candidate_profiles(mode: str, ctx: Dict[str, Any]) -> List[Tuple[str, dict]]:
    """
    Returns [(profile_name, profile_dict)] filtered by YAML 'when' rules.
    Order is preserved from YAML for deterministic fallback.
    """
    profs = load_profiles().get(mode, {}) or {}
    out = []
    for name, prof in profs.items():
        when = (prof.get("when") or {})
        if _passes_when(ctx, when):
            out.append((name, prof))
    return out

def pick_profile_with_bandit(mode: str, ctx: Dict[str, Any]) -> Tuple[str, dict]:
    """
    Rules-first: filter by 'when', then use bandit as a tie-breaker.
    If only one matches, take it; if none match, fall back to a sensible default.
    """
    cands = candidate_profiles(mode, ctx)
    if len(cands) == 0:
        # Fall back to your current defaults
        fallback = "t0_liquidity" if mode == "snipe" else "momentum"
        prof = load_profiles().get(mode, {}).get(fallback, {}) or {}
        return fallback, prof
    if len(cands) == 1:
        return cands[0]

    # Tie-break with bandit (arms = profile names). Never escalates actions.
    try:
        from strategy.contextual_bandit import get_bandit_sync
        bm = get_bandit_sync()
        # Temporarily map bandit "arms" to profile names by sampling multiple times
        # Choose the arm with highest soft weight among candidate names
        weights = bm.current_weights()  # based on mean reward
        # pick the candidate with max weight (deterministic)
        best = max(cands, key=lambda kv: weights.get(kv[0], 0.0))
        return best
    except Exception:
        # Deterministic fallback: first in YAML order
        return cands[0]
