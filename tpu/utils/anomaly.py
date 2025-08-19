import numpy as np
from sklearn.ensemble import IsolationForest

_model_cache = {}

def anomaly_score(series: list[float], key: str = "default") -> float:
    if len(series) < 16:
        return 0.0
    X = np.array(series, dtype=float).reshape(-1, 1)
    model = _model_cache.get(key) or IsolationForest(contamination=0.1, random_state=42)
    model.fit(X)
    _model_cache[key] = model
    # score of latest point (higher = more normal in sklearn; invert for anomaly)
    s = model.score_samples(X)[-1]
    return float(-s)  # positive = more anomalous
