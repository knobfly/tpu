import re

# Extend/adjust as you like
_STOPWORDS = {
    "this","that","they","them","you","have","with","just","and","the","for","from",
    "http","https","www","com","org","net","is","are","was","were","it","its","to",
    "of","in","on","at","as","by","be","or","an","a"
}

def extract_keywords(
    text: str,
    *,
    top_k: int | None = None,
    min_len: int = 3,
    **kwargs
) -> list[str]:
    """
    Blended keyword extractor:
      - Keeps original regex/blacklist spirit
      - Supports top_k, min_len, and extra kwargs
      - De-dupes in original order
      - Case-insensitive stopword filtering
      - Captures $TICKERS and #tags (returns without the prefix)
    """
    if not text:
        return []

    out: list[str] = []
    seen: set[str] = set()

    # 1) Capture $TICKERS / #tags first (e.g., $ALT, #Solana)
    for m in re.finditer(r'[$#]([A-Za-z0-9_]{2,20})', text):
        tok = m.group(1)  # drop the $ / #
        key = tok.lower()
        if len(key) >= min_len and key not in _STOPWORDS and key not in seen:
            seen.add(key)
            out.append(tok)

    # 2) General tokens: start with a letter, then letters/digits/underscore
    #    (mirrors your original, but parameterized by min_len)
    #    Lower bound is min_len; cap at 20 like your original
    pattern = rf"\b[a-zA-Z][a-zA-Z0-9_]{{{max(min_len-1,0)},20}}\b"
    for tok in re.findall(pattern, text):
        key = tok.lower()
        if key not in _STOPWORDS and key not in seen:
            seen.add(key)
            out.append(tok)

    # 3) Optional top_k limit
    if top_k is not None and top_k >= 0:
        out = out[:top_k]

    return out
