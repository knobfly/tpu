# modules/utils/sentiment_cache.py

# This should be populated by sentiment_scanner
_sentiment_scores = {}  # token → float (0.0 to 1.0)

def update_sentiment(token: str, score: float):
    _sentiment_scores[token] = score

def get_recent_sentiment(token: str) -> float:
    return _sentiment_scores.get(token, 0.0)
