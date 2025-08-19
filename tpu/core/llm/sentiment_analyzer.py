import logging

from textblob import TextBlob  # lightweight NLP sentiment


def analyze_sentiment(text: str) -> dict:
    """
    Analyzes sentiment of a given text using polarity and subjectivity.
    Returns a dict with polarity, subjectivity, and label.
    """
    try:
        blob = TextBlob(text)
        polarity = blob.sentiment.polarity
        label = "positive" if polarity > 0.1 else "negative" if polarity < -0.1 else "neutral"
        return {
            "polarity": polarity,
            "subjectivity": blob.sentiment.subjectivity,
            "label": label
        }
    except Exception as e:
        logging.warning(f"[SentimentAnalyzer] Failed: {e}")
        return {"polarity": 0, "subjectivity": 0, "label": "neutral"}
