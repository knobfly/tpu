import logging

from inputs.social.sentiment_analyzer import analyze_sentiment
from memory.shared_runtime import shared_memory
from utils.clean_text import normalize_text
from utils.keyword_tools import extract_keywords


def summarize_event(event: dict) -> str:
    """
    Summarizes a single decoded firehose event with influencer and tag metadata.
    """
    try:
        token = event.get("token", "Unknown Token")
        tx_hash = event.get("tx_hash", "N/A")
        event_type = event.get("event_type", "unknown").replace("_", " ")
        influencers = event.get("influencer_wallets", [])
        tags = event.get("wallet_tags", [])

        summary = f"📡 Detected {event_type.title()} for {token}"

        if influencers:
            summary += f" | ⚠️ Influencers Involved: {', '.join(influencers[:3])}"

        if tags:
            summary += f" | 🧠 Tags: {', '.join(tags[:5])}"

        summary += f" | 🔗 Tx: {tx_hash[:8]}..."

        return summary

    except Exception as e:
        logging.warning(f"[EventSummarizer] Failed to summarize event: {e}")
        return "⚠️ Error generating event summary"


def summarize_event_activity(token: str) -> dict:
    """
    Generates an NLP-based summary of recent group messages tied to a token.
    Pulls from shared_memory['messages'][token] if available.
    """
    try:
        all_messages = shared_memory.get("messages", {}).get(token, [])
        if not all_messages:
            return {
                "summary": "No recent discussion.",
                "keywords": [],
                "sentiment": "neutral",
                "confidence": 0.0,
            }

        combined = " ".join([normalize_text(msg.get("text", "")) for msg in all_messages])
        sentiment = analyze_sentiment(combined)
        keywords = extract_keywords(combined, top_k=10)

        return {
            "summary": combined[:280] + "..." if len(combined) > 280 else combined,
            "keywords": keywords,
            "sentiment": sentiment.get("label", "neutral"),
            "confidence": sentiment.get("score", 0.0),
        }

    except Exception as e:
        logging.warning(f"[EventNLP] Failed to summarize {token}: {e}")
        return {
            "summary": "NLP summary failed.",
            "keywords": [],
            "sentiment": "neutral",
            "confidence": 0.0,
        }
