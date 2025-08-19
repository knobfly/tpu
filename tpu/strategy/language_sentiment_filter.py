# === sniper_bot/modules/strategy/language_sentiment_filter.py ===

from utils.language_detector import detect_language
from utils.nlp_sentiment import analyze_sentiment

SUPPORTED_LANGUAGES = {"en", "es", "zh", "ko", "tr", "ru"}  # Expandable

def filter_language_sentiment(text: str, expected_lang: str = "en") -> dict:
    """
    Detects language and sentiment of a message.
    Returns:
        {
            "language": detected lang code,
            "matches_expected": True/False,
            "sentiment_score": -5 to +5,
            "polarity": str,
            "reason": str
        }
    """
    lang = detect_language(text)
    matches = lang == expected_lang

    sentiment = analyze_sentiment(text)
    score = sentiment.get("score", 0)
    polarity = sentiment.get("label", "neutral")

    reason = "lang mismatch" if not matches else "ok"
    if abs(score) < 1 and matches:
        reason = "neutral"
    elif matches:
        reason = f"{polarity} sentiment"

    return {
        "language": lang,
        "matches_expected": matches,
        "sentiment_score": score,
        "polarity": polarity,
        "reason": reason
    }
