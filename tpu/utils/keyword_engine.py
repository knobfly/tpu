# modules/utils/keyword_engine.py

SNIPE_KEYWORDS = {
    "launch": 3,
    "live": 3,
    "deploy": 2,
    "just launched": 4,
    "pumped": 2,
    "txns": 2,
    "fresh": 2,
    "ape": 1,
}

TRADE_KEYWORDS = {
    "celeb": 3,
    "wallet": 2,
    "community": 2,
    "alpha": 3,
    "meta": 2,
    "solid dev": 2,
    "trending": 2,
    "backed": 4,
    "theme": 1,
    "chart": 1,
    "tokenomics": 1,
}


def evaluate_keywords(text: str, mode: str = "trade") -> int:
    """
    Scores a block of text based on mode-specific keywords.
    mode = 'snipe' or 'trade'
    """
    text = text.lower()
    score = 0
    if mode == "snipe":
        for word, weight in SNIPE_KEYWORDS.items():
            if word in text:
                score += weight
    else:
        for word, weight in TRADE_KEYWORDS.items():
            if word in text:
                score += weight
    return score
