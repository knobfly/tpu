import math

from sklearn.feature_extraction.text import TfidfVectorizer

_vectorizer = TfidfVectorizer()
_embedding_cache = {}

def get_reasoning_embedding(text: str) -> list:
    """
    Returns a vector representation of the given reasoning tag.
    Uses cached vectorizer with TF-IDF. No internet/API required.
    """
    global _embedding_cache
    if text in _embedding_cache:
        return _embedding_cache[text]

    try:
        matrix = _vectorizer.fit_transform([text])
        vector = matrix.toarray()[0].tolist()
        _embedding_cache[text] = vector
        return vector
    except:
        return [0.0]

def cosine_similarity(vec1: list, vec2: list) -> float:
    """
    Computes cosine similarity between two vectors.
    """
    if not vec1 or not vec2:
        return 0.0
    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)
