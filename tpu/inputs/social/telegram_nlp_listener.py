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
        rec = embed_message(text=cleaned, source=f"tg::{chat_id}:{user}")
        embed_id = rec.get("id") if isinstance(rec, dict) else rec

        # --- Influencer Profiling ---
        from collections import defaultdict
        influencer_tracker = getattr(analyze_telegram_message, 'influencer_tracker', None)
        if influencer_tracker is None:
            influencer_tracker = defaultdict(lambda: {'count': 0, 'last': 0})
            setattr(analyze_telegram_message, 'influencer_tracker', influencer_tracker)
        now = time.time()
        influencer_rec = influencer_tracker[user]
        influencer_rec['count'] += 1
        influencer_rec['last'] = now
        if influencer_rec['count'] > 20:
            try:
                from librarian.data_librarian import librarian
                librarian.catalog_influencer({
                    'user': user,
                    'group': chat_id,
                    'count': influencer_rec['count'],
                    'last': datetime.utcnow().isoformat()
                })
            except Exception:
                pass

        # --- Scam/Rug Detection ---
        scam_keywords = ["rug", "scam", "exit", "pull", "hack", "exploit", "stolen", "drain"]
        if any(k in cleaned.lower() for k in scam_keywords):
            try:
                from librarian.data_librarian import librarian
                librarian.blacklist_source({
                    'user': user,
                    'group': chat_id,
                    'text': cleaned,
                    'timestamp': datetime.utcnow().isoformat()
                })
                logging.info(f"[ScamDetect] Blacklisted source {user} in {chat_id}")
            except Exception:
                pass

        # --- Structured ingest to librarian ---
        try:
            from librarian.data_librarian import librarian
            msg_obj = {
                'group': None,
                'user': user,
                'text': cleaned,
                'keywords': keywords,
                'sentiment': sentiment.get('label'),
                'wallets': [],
                'tokens': [],
                'timestamp': datetime.utcnow().isoformat()
            }
            librarian.ingest_telegram_message(msg_obj)
        except Exception as e:
            logging.warning(f"[TGNLP] librarian ingest failed: {e}")

        return {
            "sentiment": sentiment.get("label"),
            "sentiment_score": sentiment.get("score", 0.0),
            "keywords": keywords,
            "confidence": max(0.1, float(sentiment.get("score", 0.0) or 0.0)),
            "toxicity": 0.0,
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
