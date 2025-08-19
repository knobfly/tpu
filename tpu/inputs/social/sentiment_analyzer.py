import logging
import re
from statistics import mean

# Word lists (extend as you wish)
POSITIVE_WORDS = {
    "moon", "pump", "gain", "rocket", "bull", "profit", "win", "ath",
    "green", "pumpin", "uponly", "breakout", "surge"
}
NEGATIVE_WORDS = {
    "rug", "dump", "bear", "scam", "loss", "rekt", "dead", "fail",
    "down", "dumping", "crash"
}

# Emoji heuristics
POS_EMOJI = set("🚀🔥✅💎🟢📈✨👍😎🏆💰⭐️⭐⭐⭐")
NEG_EMOJI = set("⚠️❌💀🟥🔻📉🤡😡😭🧻")

# Simple boosters / dampeners
BOOSTERS = {"very", "so", "super", "mega", "ultra", "insane", "crazy"}
DAMPENERS = {"maybe", "kinda", "sorta", "slightly"}
NEGATORS = {"not", "no", "never", "isnt", "isn't", "aint", "ain't", "dont", "don't"}

def _emoji_score(text: str) -> float:
    pos = sum(ch in POS_EMOJI for ch in text)
    neg = sum(ch in NEG_EMOJI for ch in text)
    if pos == neg == 0:
        return 0.0
    raw = (pos - neg) / max(1, (pos + neg))  # -1..1
    return raw

def _punct_boost(text: str) -> float:
    ex = text.count("!")
    ex_boost = min(0.2, ex * 0.05)  # cap at +0.2
    # ALL-CAPS tokens boost a bit if bullish words exist, penalize if bearish
    caps = sum(1 for w in text.split() if len(w) >= 3 and w.isupper())
    cap_boost = min(0.2, caps * 0.03)
    return ex_boost + cap_boost

def analyze_sentiment(text: str) -> dict:
    """
    Heuristic sentiment: returns {"score": -1..1, "label": "negative/neutral/positive"}.
    Robust to templated 'trending' posts (emojis, caps, !, negation).
    """
    try:
        if not text:
            return {"score": 0.0, "label": "neutral"}

        t = text.strip()
        tl = t.lower()

        # Base word scoring with simple negation handling on a small window
        words = re.findall(r"\b[\w']+\b", tl)
        scores = []
        i = 0
        while i < len(words):
            w = words[i]
            negated = (w in NEGATORS)
            if negated:
                i += 1
                # flip the next 1-3 tokens if they’re sentiment-laden
                window = words[i:i+3]
                for w2 in window:
                    if w2 in POSITIVE_WORDS:
                        scores.append(-1)
                    elif w2 in NEGATIVE_WORDS:
                        scores.append(+1)
                i += len(window)
                continue

            # regular word hit
            if w in POSITIVE_WORDS:
                val = 1
                # booster/dampener check
                if i > 0 and words[i-1] in BOOSTERS:
                    val += 0.2
                if i > 0 and words[i-1] in DAMPENERS:
                    val -= 0.2
                scores.append(val)
            elif w in NEGATIVE_WORDS:
                val = -1
                if i > 0 and words[i-1] in BOOSTERS:
                    val -= 0.2
                if i > 0 and words[i-1] in DAMPENERS:
                    val += 0.2
                scores.append(val)
            i += 1

        # Emoji and punctuation/caps adjustments
        e_score = _emoji_score(t)              # -1..1
        p_boost = _punct_boost(t)              # 0..~0.4
        if e_score != 0:
            scores.append(e_score * 1.0)       # weigh emojis like a word

        # If nothing matched, default neutral but still consider emojis
        if not scores:
            base = e_score
        else:
            base = mean(scores)

        # Apply punctuation/caps boost in the direction of base
        if base > 0:
            base = min(1.0, base + p_boost)
        elif base < 0:
            base = max(-1.0, base - p_boost)
        # If still zero, nudge to slight neutral to avoid None-like behavior
        score = float(base if base != 0 else 0.0)

        label = "positive" if score > 0.05 else "negative" if score < -0.05 else "neutral"
        return {"score": score, "label": label}

    except Exception as e:
        logging.warning(f"[SentimentAnalyzer] Failed to analyze: {e}")
        return {"score": 0.0, "label": "neutral"}

