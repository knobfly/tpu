# utils/token_matcher.py
from rapidfuzz import fuzz, process


def best_symbol_match(query: str, candidates: list[str], cutoff: int = 85) -> str | None:
    if not query or not candidates:
        return None
    m = process.extractOne(query, candidates, scorer=fuzz.token_sort_ratio)
    return m[0] if m and m[1] >= cutoff else None
