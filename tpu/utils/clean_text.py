# modules/utils/clean_text.py

import html
import re
import unicodedata


def clean_message_text(text: str) -> str:
    if not text:
        return ""

    # Decode HTML entities
    text = html.unescape(text)

    # Normalize Unicode
    text = unicodedata.normalize("NFKC", text)

    # Remove Telegram usernames and hashtags
    text = re.sub(r"@\w+", "", text)
    text = re.sub(r"#\w+", "", text)

    # Remove links
    text = re.sub(r"http\S+", "", text)

    # Remove special characters, multiple spaces
    text = re.sub(r"[^\w\s\.\!\?]", "", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip().lower()

def clean_name(name: str) -> str:
    """
    Cleans a token or project name by removing emojis, extra spaces, and non-alphanumeric chars.
    Keeps underscores and hyphens.
    """
    if not isinstance(name, str):
        return ""
    try:
        # Remove emojis and special unicode
        cleaned = re.sub(r'[^\w\s\-]', '', name)
        # Collapse multiple spaces
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned
    except Exception:
        return str(name)

def normalize_text(text: str) -> str:
    """
    Normalize and clean Telegram message text.
    - Lowercase
    - Remove extra spaces
    - Strip emojis/symbols (optional)
    - Remove special characters
    """
    text = text.lower()
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\w\s#@]', '', text)  # Keep hashtags, mentions
    return text.strip()
