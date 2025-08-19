import logging
from datetime import datetime

from memory.shared_runtime import shared_memory
from strategy.reinforcement_tracker import get_recent_results
from strategy.reinforcement_weights import evolve_reasoning_weights
from strategy.tag_theme_memory import decay_unused_tags


def evolve_strategy_phase():
    """
    Mutates and adapts strategy modes based on recent performance.

    Triggers:
    - Loss streaks or rug clusters
    - Moon streaks
    - Score entropy (unpredictable outcomes)
    - Shifts in tag accuracy
    """

    logging.info("🧬 [StrategyEvolution] Beginning phase evaluation...")

    results = get_recent_results(limit=50)
    if not results:
        return

    rugs = sum(1 for r in results if r.get("outcome") == "rug")
    moons = sum(1 for r in results if r.get("outcome") == "moon")
    entropy = calculate_score_entropy(results)

    if rugs >= 5:
        logging.warning("☠️  [Evolution] High rug cluster — entering defensive phase.")
        shared_memory["strategy_mode"] = "defensive"
    elif moons >= 4:
        logging.info("🚀 [Evolution] Moon surge detected — entering aggressive phase.")
        shared_memory["strategy_mode"] = "frenzy"
    elif entropy > 0.6:
        logging.info("🌀 [Evolution] High entropy — entering adaptive phase.")
        shared_memory["strategy_mode"] = "adaptive"
    else:
        shared_memory["strategy_mode"] = "balanced"

    shared_memory["last_strategy_phase"] = datetime.utcnow().isoformat()
    evolve_reasoning_weights()
    decay_unused_tags()
    logging.info(f"🎛️  [Strategy Mode] Now set to: {shared_memory['strategy_mode']}")

def calculate_score_entropy(results):
    from statistics import stdev
    scores = [r.get("final_score", 0) for r in results if isinstance(r.get("final_score", 0), (int, float))]
    return round(stdev(scores) / 100, 3) if len(scores) > 5 else 0
