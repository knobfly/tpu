import asyncio
import logging
from typing import Any, Dict

from aiohttp import web
from core.live_config import config
from librarian.data_librarian import librarian
from special.insight_logger import log_scanner_insight
from strategy.strategy_memory import tag_token_result
from utils.logger import log_event
from utils.service_status import update_status
from utils.token_utils import get_token_metadata, tag_token_result

# === Shared runtime bot state ===
bot_state = {
    "paused": False,
    "frenzy_mode": False
}

# === Logging setup ===
logging.basicConfig(
    filename="/home/ubuntu/nyx/runtime/logs/webhook_listener.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

class WebhookListener:
    def __init__(self, telegram_interface, bot_engine, frenzy_controller):
        self.telegram = telegram_interface
        self.bot_engine = bot_engine
        self.frenzy = frenzy_controller
        self.secret = config.get("webhook_secret", "default_secret")
        self.app = web.Application()
        self.app.add_routes([web.post("/trigger", self.handle_trigger)])

    async def start(self):
        update_status("webhook_listener")
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", 8787)
        await site.start()
        logging.info("📡 Webhook listener started on port 8787")

    async def handle_trigger(self, request):
        try:
            data = await request.json()
            if not isinstance(data, dict):
                logging.warning("❌ Webhook: Invalid JSON payload")
                return web.Response(text="Invalid JSON", status=400)

            if data.get("secret") != self.secret:
                logging.warning("⚠️ Webhook: Unauthorized access attempt")
                return web.Response(text="Unauthorized", status=403)

            command = data.get("command")
            log_event(f"📥 Webhook command received: {command}")

            if command == "status":
                await self._send_telegram("📡 Webhook: Bot is running.")
                return web.Response(text="Status OK")

            elif command == "pause_bot":
                bot_state["paused"] = True
                await self._send_telegram("⏸ Bot paused via webhook.")
                return web.Response(text="Paused")

            elif command == "resume_bot":
                bot_state["paused"] = False
                await self._send_telegram("▶️ Bot resumed via webhook.")
                return web.Response(text="Resumed")

            elif command == "frenzy_on":
                await self.frenzy.activate_manual()
                bot_state["frenzy_mode"] = True
                await self._send_telegram("💥 Frenzy mode activated via webhook.")
                return web.Response(text="Frenzy activated")

            elif command == "frenzy_off":
                await self.frenzy.deactivate_manual()
                bot_state["frenzy_mode"] = False
                await self._send_telegram("🛑 Frenzy mode deactivated via webhook.")
                return web.Response(text="Frenzy deactivated")

            elif command == "snipe":
                token_address = data.get("token")
                platform = data.get("platform", "webhook")
                score = int(data.get("score", 0))

                if not token_address:
                    return web.Response(text="Missing token address", status=400)

                meta = get_token_metadata(token_address)
                tag_token_result(token_address, "webhook")

                log_scanner_insight(
                    token=token_address,
                    source="webhook_listener",
                    sentiment=score,
                    volume=len(meta.get("tags", [])),
                    result="webhook_snipe",
                    tags=["webhook", platform] + meta.get("tags", [])
                )

                librarian.record_signal(
                    token=token_address,
                    source="webhook_listener",
                    confidence=score / 10.0,
                    meta=meta
                )

                await self.bot_engine.manual_snipe(token_address)
                await self._send_telegram(
                    f"🎯 Manual snipe triggered via webhook:\n`{token_address}`\nFrom: `{platform}`"
                )
                return web.Response(text="Snipe triggered")

            return web.Response(text="Unknown command", status=400)

        except Exception as e:
            logging.exception(f"❌ Webhook error: {e}")
            return web.Response(text="Error", status=500)

    async def _send_telegram(self, message: str):
        try:
            if hasattr(self.telegram, "send_message"):
                await self.telegram.send_message(message, parse_mode="Markdown")
        except Exception as e:
            logging.warning(f"[Webhook] Failed to send Telegram message: {e}")
