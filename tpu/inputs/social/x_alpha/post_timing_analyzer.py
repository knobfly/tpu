# /x_alpha/post_timing_analyzer.py

from datetime import datetime, timedelta

from inputs.wallet.wallet_behavior_analyzer import get_wallet_entry_times
from special.insight_logger import log_ai_insight  # Optional: for future tagging


def score_post_timing(token, post_time, min_delay_sec=3):
    """
    Returns float 0.0 - 1.0 based on how many smart wallets enter after post_time.
    Adds delay buffer (min_delay_sec) to prevent false positives.
    """
    try:
        entries = get_wallet_entry_times(token)
        if not entries:
            return 0.0

        post_dt = datetime.fromisoformat(post_time)
        valid_entries = []

        for e in entries:
            try:
                entry_time = datetime.fromisoformat(e["time"])
                if (entry_time - post_dt).total_seconds() >= min_delay_sec:
                    valid_entries.append(e)
            except Exception:
                continue

        score = len(valid_entries) / len(entries)

        # Optional: Tag if strong effect observed
        if score >= 0.75:
            log_ai_insight("post_effect_detected", {
                "token": token,
                "score": round(score, 4),
                "entries_after": len(valid_entries),
                "total_entries": len(entries)
            })

        return round(score, 4)

    except Exception as e:
        # Silent fail in runtime, log if needed
        return 0.0
