# inputs/social/telegram_message_router.py

"""
Router glue:
- Bot-side (aiogram) handlers: optional, keep commands/admin here.
- User-side (Telethon) listeners: started as a long-running task.
"""

from typing import Optional

# BOT (aiogram) — optional: only import if you actually register bot commands here
try:
    from aiogram import Dispatcher
except Exception:  # aiogram may not be installed on some envs
    Dispatcher = None  # type: ignore

# USER (Telethon) listener runner
from inputs.social.telegram_signal_listener import run_telegram_signal_listener


# ===== Bot-side (aiogram) =====
def register_bot_handlers(dp: Optional["Dispatcher"] = None) -> None:
    """
    Register bot (aiogram) command handlers here if you have any.
    This does NOT start user listeners.
    """
    if dp is None:
        return

    # Example:
    # @dp.message_handler(commands=["ping"])
    # async def _ping(msg: types.Message):
    #     await msg.reply("pong")
    #
    # Keep your existing bot command registrations here.


# ===== User-side (Telethon) =====
async def start_user_listeners() -> None:
    """
    Start user (Telethon) listeners (learning/signals).
    This is a long-running coroutine—run under CrashGuardian or create_task().
    """
    await run_telegram_signal_listener()
