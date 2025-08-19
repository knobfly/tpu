import logging
import re

from textblob import TextBlob


def clean_text(text: str) -> str:
    """
    Cleans text by removing URLs, mentions, and special characters.
    """
    text = re.sub(r"http\S+", "", text)  # Remove URLs
    text = re.sub(r"@\w+", "", text)     # Remove mentions
    text = re.sub(r"[^A-Za-z0-9\s]", "", text)  # Remove special chars
    return text.strip()

def get_sentiment_score(text: str) -> float:
    """
    Returns sentiment polarity score between -1.0 (negative) and 1.0 (positive).
    """
    try:
        clean = clean_text(text)
        if not clean:
            return 0.0
        return round(TextBlob(clean).sentiment.polarity, 3)
    except Exception:
        return 0.0


def is_positive(text: str, threshold: float = 0.2) -> bool:
    return get_sentiment_score(text) > threshold


def is_negative(text: str, threshold: float = -0.2) -> bool:
    return get_sentiment_score(text) < threshold

def sentiment_label(score: float) -> str:
    if score > 0.1:
        return "positive"
    elif score < -0.1:
        return "negative"
    return "neutral"
