from core.live_config import config
from inputs.social.twitter_api import post_quote as send_quote_to_x
from inputs.social.twitter_api import post_reply as send_reply_to_x
from utils.logger import log_event


async def post_quote(token: str, original_text: str):
    if not config.get("x_autopost_enabled", True):
        log_event("🔒 Quote post skipped: Auto posting disabled")
        return
    if not config.get("allow_posting", True):
        log_event("🔒 Quote post skipped: Posting disabled by master config")
        return
    if not config.get("x_quote_mode", True):
        log_event("💬 Quote mode is OFF — skipping quote")
        return

    message = f"${token} seeing smart buys. 👀\n{original_text}"
    try:
        await send_quote_to_x(message)
        log_event(f"📣 Quote posted for ${token}")
    except Exception as e:
        log_event(f"❌ Error posting quote for ${token}: {e}")

async def post_reply(token: str, handle: str, original_text: str):
    if not config.get("x_autopost_enabled", True):
        log_event("🔒 Reply post skipped: Auto posting disabled")
        return
    if not config.get("allow_posting", True):
        log_event("🔒 Reply post skipped: Posting disabled by master config")
        return

    message = f"${token} looks interesting — following wallets are entering. 🚀"
    try:
        await send_reply_to_x(handle, message)
        log_event(f"🗣️ Replied to @{handle} about ${token}")
    except Exception as e:
        log_event(f"❌ Error replying to @{handle}: {e}")
