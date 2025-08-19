# /smart_wallet_personality.py

import logging
from datetime import datetime

from special.insight_logger import log_scanner_insight
from utils.wallet_tracker import get_wallet_trade_history

# === Personality Labels ===
PERSONALITY_LABELS = {
    "diamond_hands": "📈 Holds tokens through dips and sells at peak.",
    "paper_hands": "📉 Sells fast, rarely profits.",
    "sniper": "🎯 Buys fast on new tokens, sells after quick gains.",
    "exit_scammer": "🧨 Buys own token then dumps after pump.",
    "greedy_farmer": "🌾 Buys many tokens but rarely wins big.",
    "meta_aligner": "📊 Focuses only on trending or high-scored tokens.",
}

def detect_wallet_personality(wallet_address: str) -> dict:
    try:
        history = get_wallet_trade_history(wallet_address)
        if not history or len(history) < 5:
            return {"label": "unknown", "score": 0, "note": "Insufficient trade data"}

        wins = [t for t in history if t["result"] == "win"]
        losses = [t for t in history if t["result"] == "loss"]
        rugs = [t for t in history if t["result"] == "rug"]

        win_rate = len(wins) / len(history)
        rug_rate = len(rugs) / len(history)
        avg_hold = sum(t.get("hold_minutes", 1) for t in history) / len(history)

        label = "unknown"
        score = 50
        if win_rate > 0.7 and avg_hold > 30:
            label, score = "diamond_hands", 90
        elif rug_rate > 0.5:
            label, score = "exit_scammer", 20
        elif win_rate < 0.3 and avg_hold < 5:
            label, score = "paper_hands", 25
        elif win_rate > 0.5 and avg_hold < 10:
            label, score = "sniper", 75
        elif len(history) > 20 and win_rate < 0.4:
            label, score = "greedy_farmer", 40

        insight = {
            "wallet": wallet_address,
            "label": label,
            "score": score,
            "note": PERSONALITY_LABELS.get(label, "N/A"),
            "timestamp": datetime.utcnow().isoformat()
        }

        log_scanner_insight(insight)
        return insight

    except Exception as e:
        logging.error(f"[WalletPersonality] Failed: {e}")
        return {"label": "error", "score": 0, "note": "Exception during analysis"}
