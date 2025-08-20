# librarian_classify.py
# Classification and topic extraction helpers for DataLibrarian.

from librarian.librarian_config import GENRES

def lowerize(x):
    if isinstance(x, str): return x.lower()
    if isinstance(x, (list, tuple, set)): return [str(i).lower() for i in x]
    return str(x).lower()

def extract_topics(payload: dict) -> set:
    text_bits = []
    for k, v in payload.items():
        if isinstance(v, (str, int, float)): text_bits.append(str(v))
        elif isinstance(v, (list, tuple, set)):
            text_bits.extend([str(i) for i in v])
        elif isinstance(v, dict):
            for vv in v.values():
                if isinstance(vv, (str, int, float)): text_bits.append(str(vv))
    blob = " ".join(lowerize(text_bits))
    hits = set()
    for genre, keys in GENRES.items():
        for kw in keys:
            if kw in blob:
                hits.add(kw)
    return hits

def classify_genre(payload: dict) -> str:
    topics = extract_topics(payload)
    order = ["risk","profits","losses","listings","wallets","charts","social","math","memes"]
    for g in order:
        if any(kw in topics for kw in GENRES[g]):
            return g
    return "misc"
