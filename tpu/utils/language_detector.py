import re

from langdetect import DetectorFactory, detect

DetectorFactory.seed = 0  # Consistent results


def detect_language(text: str) -> str:
    """
    Detects the language of the given text.
    Returns ISO 639-1 code (e.g., 'en', 'es').
    """
    try:
        clean_text = re.sub(r"[^\w\s]", "", text)
        return detect(clean_text) if clean_text else "unknown"
    except Exception:
        return "unknown"


def is_english(text: str) -> bool:
    return detect_language(text) == "en"
