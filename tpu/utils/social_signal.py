import logging
from typing import Dict

from utils.social_sources import fetch_recent_posts

# === Platform Weights ===
PLATFORM_WEIGHTS = {
    "x": 1.0,
    "telegram": 0.9,
    "discord": 0.8,
    "youtube": 0.7,
    "weibo": 0.6
}

# === Scoring Factors ===
VERIFIED_INFLUENCER_BOOST = 1.5
SPAM_POST_PENALTY = 0.4
NEGATIVE_SENTIMENT_PENALTY = 0.3
POST_TO_VOLUME_CORRELATION_BOOST = 0.6
PLATFORM_REPUTATION_FACTOR = 0.5

# === Final Social Score Computation ===
def get_social_score(token_address: str, metadata: Dict = {}) -> float:
    try:
        signals = fetch_social_data(token_address)

        score = 0
        for platform, signal in signals.items():
            weight = PLATFORM_WEIGHTS.get(platform, 0.5)
            engagement = signal.get("engagement", 0)
            sentiment = signal.get("sentiment", 0.5)
            verified = signal.get("verified", False)
            spammy = signal.get("spam", False)
            correlated = signal.get("correlated_to_volume", False)
            history_success = signal.get("platform_success_factor", 1.0)

            # === Base Score from Engagement and Platform Weight ===
            platform_score = engagement * weight

            # === Social Modifiers ===
            if verified:
                platform_score *= VERIFIED_INFLUENCER_BOOST
            if spammy:
                platform_score *= SPAM_POST_PENALTY
            if sentiment < 0.4:
                platform_score *= NEGATIVE_SENTIMENT_PENALTY
            if correlated:
                platform_score += POST_TO_VOLUME_CORRELATION_BOOST

            # === Platform Memory Reputation ===
            platform_score *= (1 + (history_success - 1) * PLATFORM_REPUTATION_FACTOR)

            score += platform_score

        return round(score, 2)

    except Exception as e:
        logging.warning(f"⚠️ Social score evaluation failed for {token_address}: {e}")
        return 0.0
