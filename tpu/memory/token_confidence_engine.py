from datetime import datetime, timedelta

_token_confidence = {}

def update_token_confidence(mint: str, delta: float, source: str = None):
    now = datetime.utcnow()
    if mint not in _token_confidence:
        _token_confidence[mint] = {"confidence": 0.5, "last_updated": now, "sources": {}}

    entry = _token_confidence[mint]
    entry["confidence"] = max(0.0, min(1.0, entry["confidence"] + delta))
    entry["last_updated"] = now
    entry["sources"][source or "unknown"] = now

def get_token_confidence(mint: str) -> float:
    entry = _token_confidence.get(mint)
    if not entry:
        return 0.5
    return entry["confidence"]

def decay_token_confidence(hours: int = 6):
    threshold = datetime.utcnow() - timedelta(hours=hours)
    for mint, data in list(_token_confidence.items()):
        if data["last_updated"] < threshold:
            data["confidence"] = max(0.0, data["confidence"] - 0.05)
            data["last_updated"] = datetime.utcnow()
