import json
import logging
import os

WEIGHTS_FILE = "/home/ubuntu/nyx/runtime/logs/reinforcement_weights.json"

def load_weights() -> dict:
    if not os.path.exists(WEIGHTS_FILE):
        return {"profit": 2, "loss": -1, "moon": 3, "rug": -5, "dead": -2}
    try:
        with open(WEIGHTS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"[ReinforcementWeights] Failed to load weights: {e}")
        return {"profit": 2, "loss": -1, "moon": 3, "rug": -5, "dead": -2}

def save_weights(weights: dict):
    try:
        with open(WEIGHTS_FILE, "w") as f:
            json.dump(weights, f, indent=2)
    except Exception as e:
        logging.warning(f"[ReinforcementWeights] Failed to save weights: {e}")
