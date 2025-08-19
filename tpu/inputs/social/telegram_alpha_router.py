# modules/telegram_alpha_router.py

import logging
import time

from aiogram import Bot
from core.live_config import config
from special.insight_logger import log_ai_insight
from utils.logger import log_event
from utils.telegram_utils import safe_markdown

# minimal dedupe/throttle
_last_post_ts = 0.0
_last_fingerprint = None
COOLDOWN_SECONDS = 30

def _fingerprint_event(event: dict) -> str:
    # crude but effective – token + top keyword + addr
    return f"{event.get('tickers')}|{event.get('addresses')}|{event.get('sentiment')}"

async def maybe_post_alpha_ping(event: dict):
    if not config.get("allow_public_alpha", False):
        return

    global _last_post_ts, _last_fingerprint
    now = time.time()

    if now - _last_post_ts < COOLDOWN_SECONDS:
        return

    fp = _fingerprint_event(event)
    if fp == _last_fingerprint:
        return

    _last_post_ts = now
    _last_fingerprint = fp

    bot_token = config.get("telegram_bot_token")
    if not bot_token:
        return

    text = format_alpha_message(event)
    if not text:
        return

    try:
        chat_id = config.get("alpha_channel_id") or config.get("telegram_chat_id")
        bot = Bot(token=bot_token, parse_mode="Markdown")
        await bot.send_message(chat_id, text)
        await bot.session.close()

        log_ai_insight("Telegram Alpha", {"event": event})
    except Exception as e:
        logging.warning(f"[TGAlpha] Failed to post alpha: {e}")


def format_alpha_message(event: dict) -> str:
    try:
        tickers = ", ".join(event.get("tickers", [])) or "?"
        addrs = ", ".join(event.get("addresses", [])) or "-"
        sent = event.get("sentiment", "neutral")
        conf = event.get("confidence", 0.0)
        kws = ", ".join(event.get("keywords", [])[:5]) or "-"

        txt = (
            f"🔥 *Telegram Signal Detected*\n"
            f"• Tickers: `{tickers}`\n"
            f"• Addresses: `{addrs}`\n"
            f"• Sentiment: `{sent}` ({conf:.2f})\n"
            f"• Keywords: `{kws}`\n"
        )
        return txt
    except Exception:
        return None
