# /firehose/event_classifier.py

import logging
import re

EVENT_KEYWORDS = {
    "lp_create": ["initialize", "create pool", "add liquidity", "new lp"],
    "swap": ["swap", "traded", "exchanged"],
    "mint": ["mint", "create", "new token"],
    "burn": ["burn", "destroy", "remove supply"],
    "transfer": ["transfer", "send", "receive"],
}

def enrich_event_with_classification(event: dict) -> dict:
    try:
        logs = " ".join(event.get("logs", [])).lower()
        for event_type, keywords in EVENT_KEYWORDS.items():
            for kw in keywords:
                if re.search(rf"\b{kw}\b", logs):
                    event["event_type"] = event_type
                    return event

        event["event_type"] = "unknown"
    except Exception as e:
        logging.warning(f"[EventClassifier] Failed to classify logs: {e}")
        event["event_type"] = "unknown"

    return event

def classify_txn_events(decoded_event: dict) -> list[dict]:
    """
    Classifies raw decoded firehose tx data into structured events.
    Each returned event will have:
        - type: "buy", "sell", "add_liquidity", "remove_liquidity", etc.
        - token: token mint involved
        - signer: wallet address
        - amount: numeric size
        - timestamp: when it happened
    """
    events = []

    instructions = decoded_event.get("instructions", [])
    signer = decoded_event.get("signer")
    timestamp = decoded_event.get("timestamp")
    token = decoded_event.get("token")
    raw_logs = decoded_event.get("logs", [])

    for ix in instructions:
        ix_type = ix.get("type")
        amount = ix.get("amount", 0)
        tkn = ix.get("token", token)

        if ix_type in {"buy", "sell", "add_liquidity", "remove_liquidity"}:
            events.append({
                "type": ix_type,
                "token": tkn,
                "signer": signer,
                "amount": amount,
                "timestamp": timestamp,
                "logs": raw_logs
            })

    return events
