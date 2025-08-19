import json
import os
from collections import defaultdict

THEME_LOG_FILE = "/home/ubuntu/nyx/runtime/memory/strategy/theme_memory.json"

def load_theme_memory():
    if not os.path.exists(THEME_LOG_FILE):
        return {}
    try:
        with open(THEME_LOG_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_theme_memory(data):
    try:
        with open(THEME_LOG_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except:
        pass

def log_theme_result(themes: list, outcome: str):
    """
    Logs the outcome of a trade given the themes involved.

    Example: log_theme_result(["celeb", "bundle"], "rug")
    """
    data = load_theme_memory()

    for theme in themes:
        theme_data = data.get(theme, defaultdict(int))
        theme_data[outcome] = theme_data.get(outcome, 0) + 1
        data[theme] = theme_data

    save_theme_memory(data)

def evaluate_theme_strength(themes: list) -> dict:
    """
    Returns a score summary for the provided themes.

    Output:
        {
            "celeb": {"score": -4, "rug": 5, "profit": 1},
            "bundle": {"score": -6, "rug": 3, "moon": 0},
        }
    """
    data = load_theme_memory()
    scores = {}

    for theme in themes:
        outcomes = data.get(theme, {})
        profit = outcomes.get("profit", 0)
        moon = outcomes.get("moon", 0)
        rug = outcomes.get("rug", 0)
        loss = outcomes.get("loss", 0)
        dead = outcomes.get("dead", 0)

        score = profit * 2 + moon * 3 - rug * 3 - loss - dead
        scores[theme] = {
            "score": score,
            **outcomes
        }

    return scores

def get_top_themes(limit=10):
    data = load_theme_memory()
    theme_scores = []

    for theme, outcomes in data.items():
        profit = outcomes.get("profit", 0)
        moon = outcomes.get("moon", 0)
        rug = outcomes.get("rug", 0)
        score = profit * 2 + moon * 3 - rug * 2
        theme_scores.append((theme, score))

    return sorted(theme_scores, key=lambda x: -x[1])[:limit]

def get_risky_themes(threshold=-3):
    data = evaluate_theme_strength(list(load_theme_memory().keys()))
    return [theme for theme, stat in data.items() if stat["score"] <= threshold]
