# === telegram_controller.py ===
import asyncio
import contextlib
import gc
import os
import signal
import sys
from datetime import datetime

import aiohttp
import psutil
from aiogram import types
from aiogram.types import BotCommand
from aiogram.utils import exceptions as aio_exceptions
from core.live_config import config as live_config
from core.telegram_interface import TelegramInterface
from inputs.social.telegram_message_router import register_bot_handlers
from utils.logger import log_event
from utils.service_status import update_status

TG_PID_FILE = "/home/ubuntu/nyx/telegram_controller.pid"

# ---- graceful stop gate ----
stop_event = asyncio.Event()


# === Kill ghost processes ===
def kill_existing_telegram_bot():
    if not os.path.exists(TG_PID_FILE):
        return
    try:
        with open(TG_PID_FILE, "r") as f:
            old_pid = int(f.read().strip() or "0")
        if old_pid and psutil.pid_exists(old_pid):
            p = psutil.Process(old_pid)
            cmd = " ".join(p.cmdline()).lower()
            if "telegram_controller" in cmd:
                print(f"🪓 Killing ghost Telegram controller (PID {old_pid})")
                os.kill(old_pid, signal.SIGKILL)
    except Exception as e:
        print(f"[GhostKiller] Failed to kill previous process: {e}")
    finally:
        with contextlib.suppress(Exception):
            os.remove(TG_PID_FILE)


# === Bot commands ===
async def set_bot_commands(bot):
    commands = [
        BotCommand(command="start", description="Show all available commands"),
        BotCommand(command="start_robot", description="Start the sniper bot"),
        BotCommand(command="stop", description="Stop the sniper bot"),
        BotCommand(command="restart", description="Restart the bot"),
        BotCommand(command="force_reset", description="Emergency restart code"),
        BotCommand(command="brain", description="View Nyx's brain profile"),
        BotCommand(command="pause_brain", description="Pause Nyx AI"),
        BotCommand(command="resume_brain", description="Resume Nyx AI"),
        BotCommand(command="brain_status", description="View AI brain status"),
        BotCommand(command="trim_data", description="Reset brain + strategy"),
        BotCommand(command="ai_insights", description="View AI behavior + strategy"),
        BotCommand(command="ai_debug", description="Raw AI JSON state"),
        BotCommand(command="report_strategy", description="Current strategy stats"),
        BotCommand(command="tagged_tokens", description="Tagged token log"),
        BotCommand(command="meta_keywords", description="Top trending meta tags"),
        BotCommand(command="bandit", description="Show multi-armed bandit stats & controls"),
        BotCommand(command="ask", description="Ask Nyx anything"),
        BotCommand(command="llm_explain", description="Explain Nyx’s last trade"),
        BotCommand(command="wallet_story", description="LLM wallet profile"),
        BotCommand(command="sentiment", description="Sentiment trend report"),
        BotCommand(command="inject_token", description="Inject token signal"),
        BotCommand(command="inject_group", description="Inject group signal"),
        BotCommand(command="reset_injections", description="Clear all injections"),
        BotCommand(command="service", description="Status of all scanners/modules"),
        BotCommand(command="wallet_status", description="Check wallet balances"),
        BotCommand(command="frenzy_status", description="Feeding Frenzy mode"),
        BotCommand(command="config", description="Toggle config panel"),
        BotCommand(command="summary", description="Daily profit/loss summary"),
        BotCommand(command="flush_logs", description="Flush trade logs"),
        BotCommand(command="strategy_snapshot", description="Save strategy state"),
        BotCommand(command="positions", description="Show open positions"),
        BotCommand(command="pnl", description="Track trade performance"),
    ]
    await bot.set_my_commands(commands)


# === Signal handlers / quiet shutdown ===
def _install_signals():
    def _hit(*_):
        with contextlib.suppress(Exception):
            stop_event.set()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _hit)
        except Exception:
            # Some environments (e.g., Windows) may not support all signals
            pass


async def _run_polling(dp, bot):
    try:
        # lower timeout/relax so Ctrl-C interrupts quickly
        await dp.start_polling(timeout=30, relax=0.1)
    except (aio_exceptions.NetworkError, asyncio.CancelledError):
        # expected on shutdown
        pass
    except Exception as e:
        log_event(f"[Telegram] Polling stopped with error: {e}")


# === /start menu handler (kept as-is) ===
def register_start_menu(dp):
    @dp.message_handler(commands=["start"])
    async def handle_start(message: types.Message):
        help_text = (
            "🤖 *Nyx Bot Controller Ready*\n\n"
            "Use the buttons or type any command:\n\n"
            "🟢 *Core Controls:*\n"
            "• /start_robot — Start sniper bot\n"
            "• /stop — Stop sniper bot\n"
            "• /restart — Restart sniper bot\n"
            "• /force_reset — Emergency restart\n"
            "• /brain — Nyx description\n"
            "• /pause_brain — Pause Nyx (hibernation)\n"
            "• /resume_brain — Resume Nyx AI\n"
            "• /brain_status — Current AI brain status\n"
            "• /trim_data — Wipe strategy + memory\n\n"
            "📊 *Performance + Logs:*\n"
            "• /summary — Daily profit/loss report\n"
            "• /flush_logs — Save trade logs to disk\n"
            "• /strategy_snapshot — Save strategy state\n\n"
            "🧠 *AI + Strategy Tools:*\n"
            "• /ai_insights — Nyx trade behavior summary\n"
            "• /ai_debug — Raw AI state (JSON)\n"
            "• /report_strategy — Strategy memory stats\n"
            "• /tagged_tokens — Tag results from history\n"
            "• /meta_keywords — Top meta keywords\n"
            "• /ask — Ask Nyx anything (LLM-powered)\n\n"
            "• /bandit — Show multi-armed bandit stats & controls\n"
            "🔬 *LLM & Analysis:*\n"
            "• /llm_explain — Explain token signal\n"
            "• /wallet_story — Profile wallet's behavior\n"
            "• /sentiment — Telegram sentiment trends\n\n"
            "💡 *Manual Inputs:*\n"
            "• /inject_token — Manual token hint\n"
            "• /inject_group — Manual group hint\n"
            "• /reset_injections — Clear token/group hints\n\n"
            "📡 *System + Monitoring:*\n"
            "• /service — Module scanner status\n"
            "• /wallet_status — Show all wallet balances\n"
            "• /frenzy_status — Feeding Frenzy mode\n\n"
            "⚙️ *Bot Configuration:*\n"
            "• /config — Live config panel toggle"
        )
        await message.reply(help_text, parse_mode="Markdown")
        log_event("📲 Telegram controller start menu displayed")


# === Main Runner ===
async def main():
    kill_existing_telegram_bot()
    _install_signals()

    if not live_config.get("telegram_token") or not live_config.get("telegram_chat_id"):
        log_event("❌ Missing Telegram token or chat ID in config.json")
        return

    # write PID
    with open(TG_PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    update_status("telegram_controller")

    tg = TelegramInterface(config=live_config)
    bot, dp = tg.bot, tg.dp

    # Register handlers BEFORE polling
    register_bot_handlers(dp)
    register_start_menu(dp)
    await set_bot_commands(bot)

    # Ready ping
    with contextlib.suppress(Exception):
        await bot.send_message(
            chat_id=live_config.get("telegram_chat_id"),
            text=f"🟢 *Bot Controller Ready*\nConfig mode: `{live_config.get('mode')}`\nTime: `{datetime.utcnow().isoformat()}`",
            parse_mode="Markdown",
        )

    log_event("📡 Telegram controller polling started")

    poll_task = asyncio.create_task(_run_polling(dp, bot))

    # wait for stop (SIGINT/SIGTERM)
    await stop_event.wait()

    # tear down
    poll_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await poll_task

    # close aiogram storage & session
    with contextlib.suppress(Exception):
        await dp.storage.close()
        await dp.storage.wait_closed()
    with contextlib.suppress(Exception):
        sess = await bot.get_session()
        await sess.close()

    # close any lingering aiohttp sessions created elsewhere
    async def _close_orphaned_sessions():
        for obj in gc.get_objects():
            if isinstance(obj, aiohttp.ClientSession) and not obj.closed:
                with contextlib.suppress(Exception):
                    await obj.close()

    with contextlib.suppress(Exception):
        await _close_orphaned_sessions()

    # remove pid file
    with contextlib.suppress(Exception):
        os.remove(TG_PID_FILE)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # already handled by signal; keep stdout friendly
        print("❌ Telegram controller stopped.")
        sys.exit(0)
