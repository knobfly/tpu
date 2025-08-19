# /firehose/alpha_poster.py

import logging
import time

from aiogram import Bot
from core.live_config import config
from utils.logger import log_event

_last_alpha_post = 0.0
ALPHA_COOLDOWN = 60  # seconds

async def post_firehose_alpha(event: dict, score: float):
    """
    Posts an alpha alert when firehose events are high quality.
    """
    global _last_alpha_post
    if time.time() - _last_alpha_post < ALPHA_COOLDOWN:
        return

    try:
        bot_token = config.get("telegram_bot_token")
        chat_id = config.get("telegram_chat_id")
        if not bot_token or not chat_id:
            return

        bot = Bot(token=bot_token, parse_mode="Markdown")
        message = (
            f"🚀 *Alpha Detected!*\n"
            f"Token: `{event.get('token', 'unknown')}`\n"
            f"Score: `{score:.2f}`\n"
            f"Type: `{event.get('event_type', '?')}`"
        )
        await bot.send_message(chat_id, message)
        await bot.session.close()

        _last_alpha_post = time.time()
        log_event(f"[AlphaPoster] Posted alpha for {event.get('token', '?')}")
    except Exception as e:
        logging.warning(f"[AlphaPoster] Failed to post alpha: {e}")
