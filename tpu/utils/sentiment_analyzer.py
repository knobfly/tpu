import json
import logging
from datetime import datetime
from typing import Dict, List

PLATFORM_MEMORY_FILE = "/home/ubuntu/nyx/runtime/memory/platform_reputation.json"
VERIFIED_INFLUENCERS = ["@solshillgod", "@earlybirdbot", "TG:AlphaCalls", "YT:SOLRockets"]

# === Load/Save Platform Reputation Memory ===
def _load_memory() -> Dict[str, int]:
    try:
        with open(PLATFORM_MEMORY_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def _save_memory(data: Dict[str, int]):
    with open(PLATFORM_MEMORY_FILE, "w") as f:
        json.dump(data, f, indent=2)

# === Regional Peak Time Weighting ===
def _regional_weight(platform: str) -> float:
    hour = datetime.utcnow().hour
    if platform == "weibo":
        if 12 <= hour <= 16:  # CN peak hours
            return 1.25
    return 1.0

# === Sentiment Quality Analyzer ===
def analyze_sentiment(posts: List[str]) -> str:
    combined = " ".join(posts).lower()
    if any(neg in combined for neg in ["rug", "scam", "exit"]):
        return "negative"
    if any(pos in combined for pos in ["moon", "pump", "0.1 to 1"]):
        return "positive"
    return "neutral"

# === Post-Volume Correlation Bonus ===
def get_volume_alignment_bonus(social_score: float, volume_score: float) -> float:
    if abs(social_score - volume_score) <= 3:
        return 2.5
    elif social_score > 5 and volume_score < 2:
        return -2
    return 0

# === Influencer Boost ===
def contains_verified_influencer(posts: List[str]) -> bool:
    return any(name.lower() in " ".join(posts).lower() for name in VERIFIED_INFLUENCERS)

# === Bot/Spam Detection ===
def is_bot_spam(post_times: List[str]) -> bool:
    if len(post_times) < 5:
        return False
    try:
        timestamps = sorted([datetime.fromisoformat(t) for t in post_times])
        diffs = [(timestamps[i+1] - timestamps[i]).total_seconds() for i in range(len(timestamps) - 1)]
        avg_spacing = sum(diffs) / len(diffs)
        return avg_spacing < 10
    except Exception:
        return False

# === Social Sentiment Scoring ===
def score_social_sentiment(platform: str, posts: List[str], post_times: List[str], volume_score: float) -> float:
    sentiment = analyze_sentiment(posts)
    memory = _load_memory()
    platform_rep = memory.get(platform, 0)
    regional_boost = _regional_weight(platform)

    score = 0

    if is_bot_spam(post_times):
        logging.info(f"⚠️ Spam detected on {platform} — score minimized.")
        return 1

    if sentiment == "positive":
        score += 6
    elif sentiment == "neutral":
        score += 3
    else:
        score -= 3

    if contains_verified_influencer(posts):
        score += 5

    score += platform_rep / 20  # e.g., +1 per 20 rep points
    score *= regional_boost
    score += get_volume_alignment_bonus(score, volume_score)

    return round(score, 2)

# === Update Platform Performance ===
def update_platform_result(platform: str, outcome: str):
    memory = _load_memory()
    if platform not in memory:
        memory[platform] = 0

    if outcome == "win":
        memory[platform] += 3
    elif outcome == "rug":
        memory[platform] -= 5
    elif outcome == "loss":
        memory[platform] -= 2

    memory[platform] = max(-20, min(40, memory[platform]))  # Clamp
    _save_memory(memory)
