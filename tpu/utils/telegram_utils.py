import asyncio
import json
import logging
import re
from typing import Optional

import aiohttp
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from core.live_config import config

DEFAULT_CHAT_ID = config.get("telegram_chat_id")


async def send_telegram_message(
    text: str,
    chat_id: Optional[int | str] = None,
    *,
    parse_mode: Optional[str] = None,
    silent: Optional[bool] = None,
    disable_web_page_preview: Optional[bool] = True,
    reply_to_message_id: Optional[int] = None,
    timeout_sec: int = 15,
) -> bool:
    """
    Send a message via Telegram Bot API.

    Args:
        text: Message text to send.
        chat_id: Target chat id; falls back to DEFAULT_CHAT_ID or config.
        parse_mode: "Markdown", "MarkdownV2", "HTML", or None.
        silent: If True, send silently (disable_notification).
        disable_web_page_preview: If True, no link previews.
        reply_to_message_id: Optional message to reply to.
        timeout_sec: HTTP timeout seconds.

    Returns:
        True on success, False on (logged) failure.
    """
    cid = chat_id or DEFAULT_CHAT_ID or config.get("telegram_chat_id")
    if not cid or not text:
        logging.warning("[TelegramInterface] send_telegram_message(): missing chat_id or text.")
        return False

    bot_token = config.get("telegram_token")
    if not bot_token:
        logging.error("❌ Telegram bot token not set in config.")
        return False

    # Defaults
    if silent is None:
        silent = bool(config.get("telegram_silent", False))

    # Telegram endpoint
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    payload = {
        "chat_id": cid,
        "text": text,
        "disable_notification": bool(silent),
        "disable_web_page_preview": bool(disable_web_page_preview),
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_to_message_id:
        payload["reply_to_message_id"] = int(reply_to_message_id)

    try:
        timeout = aiohttp.ClientTimeout(total=timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    return True
                # Handle rate limits & errors with detail
                body = await resp.text()
                logging.warning(f"📤 Telegram send failed ({resp.status}): {body[:500]}")
                # Optional: basic 429 backoff
                if resp.status == 429:
                    retry_after = 2
                    try:
                        data = await resp.json(content_type=None)
                        retry_after = int(data.get("parameters", {}).get("retry_after", 2))
                    except Exception:
                        pass
                    await asyncio.sleep(max(1, retry_after))
                return False
    except Exception as e:
        logging.error(f"🚨 Telegram message error: {e}")
        return False

def build_config_keyboard(config: dict) -> InlineKeyboardMarkup:
    """
    Builds an inline keyboard from a config dictionary.
    Each key is a toggle with True/False value.
    """
    keyboard = InlineKeyboardMarkup(row_width=2)
    for key, value in config.items():
        label = f"{'✅' if value else '❌'} {key.replace('_', ' ').title()}"
        button = InlineKeyboardButton(text=label, callback_data=f"toggle:{key}")
        keyboard.add(button)
    return keyboard

def safe_markdown(text: str) -> str:
    """
    Escapes Telegram markdown special characters.
    """
    if not isinstance(text, str):
        return str(text)
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)
