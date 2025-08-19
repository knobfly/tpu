import logging
import re

from aiogram import types
from special.insight_logger import log_scanner_insight
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
    try:
        if not message or not message.text:
            return

        raw_text = ensure_str(message.text)
        text = raw_text.lower()

        mentions = extract_token_mentions(text)
        if not mentions:
            return

        matched_words = [w for w in HYPE_WORDS if w in text]
        score = len(matched_words)

        for token in mentions:
            log_event(f"📣 Telegram mention: {token} (score {score})")

            # Tag metadata keywords based on hype words
            for word in matched_words:
                update_meta_keywords(token, word)

            tag_token_result(token, reason="telegram_hype")

            log_scanner_insight(
                source="telegram_group",
                token=token,
                sentiment=score,
                volume=0,
                result="scanned",
                meta_words=matched_words,
            )

    except Exception as e:
        logging.warning(f"⚠️ Telegram scanner error: {e}")
