import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from memory.shared_runtime import shared_memory
from strategy.reinforcement_tracker import get_recent_results


def detect_theme_phase(limit=50) -> dict:
    """
    Scans recent trades to determine dominant profitable and unprofitable themes.
    Updates shared memory with meta phase info.
    """
    results = get_recent_results(limit=limit)
    theme_counts = defaultdict(lambda: {"win": 0, "loss": 0})

    for r in results:
        themes = r.get("themes", [])
        outcome = r.get("outcome")
        if not themes or not outcome:
            continue
        for theme in themes:
            if outcome in ["profit", "moon"]:
                theme_counts[theme]["win"] += 1
            elif outcome in ["loss", "rug", "dead"]:
                theme_counts[theme]["loss"] += 1

    trending = []
    avoid = []

    for theme, counts in theme_counts.items():
        wins = counts["win"]
        losses = counts["loss"]
        total = wins + losses
        if total < 3:
            continue
        ratio = wins / total
        if ratio >= 0.6:
            trending.append(theme)
        elif ratio <= 0.3:
            avoid.append(theme)

    shared_memory["meta_trending_themes"] = trending
    shared_memory["meta_avoid_themes"] = avoid
    shared_memory["last_theme_phase"] = datetime.utcnow().isoformat()

    logging.info(f"📈 [ThemePhase] Trending: {trending}")
    logging.info(f"📉 [ThemePhase] Avoid: {avoid}")

    return {
        "trending": trending,
        "avoid": avoid
    }
