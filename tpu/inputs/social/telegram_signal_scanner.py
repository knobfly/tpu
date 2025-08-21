
import logging
import re
import time
from datetime import datetime
from core.live_config import config
from aiogram import types
from special.insight_logger import log_scanner_insight, log_insight, generate_daily_summary
from strategy.strategy_memory import tag_token_result, update_meta_keywords
from utils.logger import log_event
from utils.token_utils import extract_token_mentions
from utils.universal_input_validator import ensure_str


# Hype boost keywords
HYPE_WORDS = {
    "call", "buy", "moon", "send", "ape", "entry", "pump", "alert",
    "launching", "listing", "solana", "pumpfun", "live", "dropping", "now", "dev",
    "check", "volume", "whale", "chart", "vibes", "rebase", "team", "0/0"
}


# === Telegram Group Scanner Handler ===
async def handle_group_message(message: types.Message):
    from collections import defaultdict
    # --- Spam/Flood Detection ---
    spam_tracker = getattr(handle_group_message, 'spam_tracker', None)
    if spam_tracker is None:
        spam_tracker = defaultdict(lambda: {'count': 0, 'last': 0})
        setattr(handle_group_message, 'spam_tracker', spam_tracker)
    now = time.time()
    sender = getattr(message, 'from_user', None)
    sender_id = getattr(sender, 'id', None) if sender else None
    spam_key = f"{sender_id}"
    rec = spam_tracker[spam_key]
    if now - rec['last'] < 5:
        rec['count'] += 1
    else:
        rec['count'] = 1
    rec['last'] = now
    if rec['count'] > 5:
        logging.info(f"[SpamDetect] Ignoring flood from {sender_id}")
        return

    # --- Influencer Profiling ---
    influencer_tracker = getattr(handle_group_message, 'influencer_tracker', None)
    if influencer_tracker is None:
        influencer_tracker = defaultdict(lambda: {'count': 0, 'last': 0})
        setattr(handle_group_message, 'influencer_tracker', influencer_tracker)
    influencer_rec = influencer_tracker[sender_id]
    influencer_rec['count'] += 1
    influencer_rec['last'] = now
    if influencer_rec['count'] > 20:
        try:
            from librarian.data_librarian import librarian
            librarian.catalog_influencer({
                'user': sender_id,
                'group': None,
                'count': influencer_rec['count'],
                'last': datetime.now().isoformat()
            })
        except Exception:
            pass

    # --- Scam/Rug Signal Detection ---
    scam_keywords = ["rug", "scam", "exit", "pull", "hack", "exploit", "stolen", "drain"]
    raw_text = ensure_str(message.text) if message and message.text else ""
    text = raw_text.lower()
    if any(k in text for k in scam_keywords):
        try:
            from librarian.data_librarian import librarian
            librarian.blacklist_source({
                'user': sender_id,
                'group': None,
                'text': text,
                'timestamp': datetime.now().isoformat()
            })
            logging.info(f"[ScamDetect] Blacklisted source {sender_id}")
            # Log scam/rug detection to insight_logger
            log_insight("scam_detected", {
                'user': sender_id,
                'text': text,
                'timestamp': datetime.now().isoformat(),
                'keywords': scam_keywords
            })
        except Exception:
            pass
        return

    if not message or not message.text:
        return

    mentions = extract_token_mentions(text)
    if not mentions:
        return

    matched_words = [w for w in HYPE_WORDS if w in text]
    score = len(matched_words)

    for token in mentions:
        log_event(f"📣 Telegram mention: {token} (score {score})")
        # --- Structured ingest to librarian ---
        from librarian.data_librarian import librarian
        msg_obj = {
            'group': None,
            'user': None,
            'text': text,
            'keywords': matched_words,
            'sentiment': score,
            'wallets': [],
            'tokens': mentions,
            'timestamp': datetime.now().isoformat()
        }
        try:
            librarian.ingest_telegram_message(msg_obj)
        except Exception as e:
            logging.warning(f"[TGSignalScanner] librarian ingest failed: {e}")

        # Tag metadata keywords based on hype words
        for word in matched_words:
            update_meta_keywords(token, word)

        tag_token_result(token, reason="telegram_hype")

        # Log analytics event to insight_logger
        log_scanner_insight(
            source="telegram_group",
            token=token,
            sentiment=score,
            volume=0,
            result="scanned",
            meta_words=matched_words,
        )

        # Optionally trigger self-DM summary (daily or periodic)
        if score > 5 and config.get("enable_telegram_talking", False):
            summary = generate_daily_summary()
            # Here, send summary to self-DM (pseudo-code, replace with actual DM logic)
            # send_self_dm(summary)
            logging.info(f"[SelfDM] Daily summary triggered: {summary}")
