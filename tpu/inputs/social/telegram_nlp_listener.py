# /telegram_nlp_listener.py

import logging

from core.llm.group_message_embedder import embed_message  # already in your tree
from inputs.social.sentiment_analyzer import analyze_sentiment  # you already have it
from utils.clean_text import normalize_text
from utils.keyword_tools import extract_keywords  # you have this too


async def analyze_telegram_message(text: str, user: str, chat_id: int) -> dict:
    """Runs NLP scoring, embedding, and returns a structured view."""
    try:
        cleaned = (normalize_text(text) or "").strip()

        sentiment = analyze_sentiment(cleaned)  # expects dict {label, score}
        keywords = extract_keywords(cleaned, top_k=10)

        # CALL-SITE FIX: do not pass unknown kwargs; encode them in `source`
        rec = embed_message(text=cleaned, source=f"tg::{chat_id}:{user}")
        embed_id = rec.get("id") if isinstance(rec, dict) else rec

        return {
            "sentiment": sentiment.get("label"),
            "sentiment_score": sentiment.get("score", 0.0),
            "keywords": keywords,
            "confidence": max(0.1, float(sentiment.get("score", 0.0) or 0.0)),
            "toxicity": 0.0,      # fill if you wire a toxicity model
            "embed_id": embed_id,
        }

    except Exception as e:
        logging.warning(f"[TGNLP] Failed telegram NLP: {e}")
        return {
            "sentiment": "neutral",
            "sentiment_score": 0.0,
            "keywords": [],
            "confidence": 0.1,
            "toxicity": 0.0,
            "embed_id": None,
        }

# alias
fetch_group_sentiment = analyze_telegram_message
