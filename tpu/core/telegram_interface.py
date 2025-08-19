import asyncio
import json
import logging
import os
import subprocess
import sys

import psutil
from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from aiogram.utils import executor
from aiogram.utils.markdown import escape_md
from core.ai_brain import (
    ai_brain,
    nyx_enrich_token,
    nyx_meta_theme_from_keywords,
    nyx_trade_summary,
)
from core.live_config import config
from core.live_config import config as live_config
from core.live_config import save_config
from core.llm.llm_brain import LLMRunMode, get_llm_explanation, init_llm_brain, llm_brain
from core.llm.style_evolution import style_evolution
from exec.feeding_frenzy import is_frenzy_active
from exec.open_position_tracker import open_position_tracker
from inputs.social.sentiment_scanner import get_sentiment_report
from inputs.wallet.multi_wallet_manager import MultiWalletManager
from inputs.wallet.wallet_core import WalletManager
from inputs.wallet.wallet_storyteller import generate_wallet_bio
from librarian.feature_store import init_feature_store
from maintenance.auto_rebalance import rebalance_all_wallets
from maintenance.emergency_kill_switch import activate_kill_switch
from memory.memory_trim_guardian import trigger_trim_check
from special.daily_summary_report import get_daily_summary
from special.insight_logger import flush_insights, generate_daily_summary, get_trade_history_summary
from strategy.contextual_bandit import get_bandit_sync, init_bandit_manager
from strategy.strategy_memory import (
    STRATEGIES,
    calculate_total_win_rate,
    get_highest_scoring_idle_token,
    get_recent_overlap_triggers,
    get_strategy_report,
    get_strategy_score,
    get_tagged_tokens,
    get_tagged_tokens_report,
)
from utils.crash_guardian import crash_guardian
from utils.logger import log_event
from utils.rpc_loader import get_active_rpc
from utils.service_status import get_status_report
from utils.telegram_utils import build_config_keyboard
from utils.universal_input_validator import safe_parse

API_TOKEN = live_config["telegram_token"]
CHAT_ID = live_config["telegram_chat_id"]

bot = Bot(token=API_TOKEN, parse_mode="MarkdownV2")
dp = Dispatcher(bot)

# === Global Modes ===
LLM_MODE_ENABLED = True
FREE_CHAT_ENABLED = True



def is_bot_running():
    try:
        output = subprocess.check_output(["pgrep", "-f", "main.py"])
        return bool(output.strip())
    except subprocess.CalledProcessError:
        return False

def stop_bot_process():
    try:
        result = subprocess.run(["pkill", "-f", "main.py"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        return result.returncode == 0
    except Exception:
        return False


def launch_main_bot():
    logging.info("🟢 Launching main.py from Telegram")
    python_path = subprocess.getoutput("which python3").strip()
    script_path = "/home/ubuntu/nyx/main.py"
    log_path = "/home/ubuntu/nyx/runtime/logs/main_output.log"

    with open(log_path, "a") as log_file:
        subprocess.Popen(
            [python_path, script_path],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd="/home/ubuntu/nyx",
            start_new_session=True
        )

BOT_PID_FILE = "/home/ubuntu/nyx/core/bot_engine.pid"
BOT_LOG_FILE = "/home/ubuntu/nyx/runtime/logs/bot_engine.log"


class TelegramInterface:
    def __init__(self, config, wallet=None):
        self.config = config
        if wallet:
            self.wallet = wallet
        else:
            try:
                self.wallet = WalletManager.from_file(config["wallet_keypair"], name="main")
            except Exception as e:
                raise RuntimeError(f"Failed to load wallet for TelegramInterface: {e}")
        self.bot = Bot(token=config.get("telegram_token"))
        self.dp = Dispatcher(self.bot)
        self.register_handlers()
        self.ai = ai_brain
        self.llm = init_llm_brain()

    async def send_message(self, text: str):
        try:
            await self.bot.send_message(config["telegram_chat_id"], text)
        except Exception as e:
            logging.error(f"[TelegramInterface] Failed to send message: {e}")

    def register_handlers(self):
        @self.dp.message_handler(commands=["start"])
        async def handle_start_command(message: types.Message):
            summary = await get_daily_summary()

            if not summary:
                await message.reply("📭 No activity logged in the last 24 hours.")
                return

            await message.reply(summary, parse_mode="Markdown")

        @self.dp.message_handler(commands=["brain"])
        async def cmd_brain(msg: types.Message):
            await bot.send_chat_action(msg.chat.id, "typing")
            profile = ai_brain.get_identity_profile()
            soul = profile.get("soul", "")
            formatted = (
                f"*🧠 Name:* {profile['name']}\n"
                f"*⚙️ Role:* {profile['role']}\n"
                f"*🪙 Chain:* {profile['chain']}\n\n"
                f"*🎯 Mission:* {profile['mission']}\n"
                f"*🔥 Drive:* {profile['drive']}\n"
                f"*🌌 Soul:* {soul}"
            )
            await msg.reply(formatted, parse_mode="Markdown")

        @self.dp.message_handler(commands=["ask"])
        async def cmd_ask(msg: types.Message):
            prompt = msg.get_args()
            if not prompt:
                await msg.reply("🧠 Ask me something. Usage: `/ask what tokens are hot?`", parse_mode="Markdown")
                return
            await bot.send_chat_action(msg.chat.id, "typing")
            reply = await llm.ask(prompt)
            await msg.reply(reply, parse_mode="Markdown")

        @self.dp.message_handler(commands=["bandit"])
        async def cmd_bandit(msg: types.Message):
            await bot.send_chat_action(msg.chat.id, "typing")
            try:
                from strategy.contextual_bandit import get_bandit_sync
                b = get_bandit_sync()
                arms = b.arms
                weights = b.current_weights()

                lines = ["🎯 *Bandit Overview*"]
                lines.append(f"Policy: `{b.policy}` | ε: `{b.epsilon}`")
                for name, st in arms.items():
                    lines.append(
                        f"• `{name}` → pulls={st.pulls} | mean={st.mean_reward:.4f} | w={weights.get(name, 0):.3f}"
                    )
                last = b.last_choice() or "none"
                lines.append(f"\nLast chosen: `{last}`")

                keyboard = InlineKeyboardMarkup()
                keyboard.add(
                    InlineKeyboardButton("🔄 Switch Policy", callback_data="bandit_switch"),
                    InlineKeyboardButton("➕ Add Reward", callback_data="bandit_add_reward"),
                    InlineKeyboardButton("♻️ Refresh", callback_data="bandit_refresh")
                )

                await msg.reply("\n".join(lines), parse_mode="Markdown", reply_markup=keyboard)
            except Exception as e:
                await msg.reply(f"❌ Bandit status error: {e}")

        @self.dp.callback_query_handler(lambda c: c.data.startswith("bandit_"))
        async def bandit_callback(callback_query: types.CallbackQuery):
            try:
                from strategy.contextual_bandit import get_bandit_sync
                b = get_bandit_sync()

                if callback_query.data == "bandit_switch":
                    new_policy = "ucb1" if b.policy == "thompson" else "thompson"
                    b.policy = new_policy
                    config["bandit"]["policy"] = new_policy
                    save_config()
                    await callback_query.answer(f"Switched policy to {new_policy.upper()}")
                    await cmd_bandit(callback_query.message)

                elif callback_query.data == "bandit_add_reward":
                    await callback_query.answer("Reply with: /bandit_add <strategy> <reward>")
                    await callback_query.message.reply("Usage: `/bandit_add <strategy> <reward>`", parse_mode="Markdown")

                elif callback_query.data == "bandit_refresh":
                    await b.sync_rewards_from_feature_store()
                    await callback_query.answer("Bandit data refreshed.")
                    await cmd_bandit(callback_query.message)
            except Exception as e:
                await callback_query.answer(f"Error: {e}")

        @self.dp.message_handler(commands=["inject_token"])
        async def inject_token_command(message: types.Message):
            try:
                token_hint = message.text.split(maxsplit=1)[1].strip() if len(message.text.split()) > 1 else ""
                if not token_hint:
                    await message.reply("🧬 Usage: /inject_token <name or keyword>")
                    return

                ai_brain.manual_token_hint = token_hint
                ai_brain.manual_token_source = "manual_injection"

                await message.reply(
                    f"🧠 Got it. Nyx is now watching for *{token_hint}*. Will auto-reset after buy/sell.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                await message.reply(f"⚠️ Error: {e}")

        @self.dp.message_handler(commands=["inject_group"])
        async def inject_group_command(message: types.Message):
            try:
                group_hint = message.text.split(maxsplit=1)[1].strip() if len(message.text.split()) > 1 else ""
                if not group_hint:
                    await message.reply("📡 Usage: /inject_group <group name or partial>")
                    return

                ai_brain.manual_group_hint = group_hint
                ai_brain.manual_group_source = "manual_injection"

                await message.reply(
                    f"🛰️ Nyx is now scanning group messages for: *{group_hint}*. Will auto-reset after event.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                await message.reply(f"⚠️ Error: {e}")

        @self.dp.message_handler(commands=["tagged"])
        async def cmd_tagged(msg: types.Message):
            try:
                tokens = get_tagged_tokens()
                if not tokens:
                    await msg.reply("🧠 No tokens currently tagged.")
                else:
                    await msg.reply(f"🧠 Tagged tokens:\n\n" + "\n".join(tokens))
            except Exception as e:
                logging.warning(f"[TG] /tagged fail: {e}")
                await msg.reply("Strategy memory unreadable.")

        @self.dp.message_handler(commands=["positions"])
        async def handle_positions_command(message: types.Message):
            positions = open_position_tracker.get_open_positions()
            if not positions:
                await message.reply("🗓️ No open positions currently.")
                return

            reply = "📌 *Open Positions:*\n"
            for wallet, tokens in positions.items():
                reply += f"\n`{wallet}`\n"
                for token, data in tokens.items():
                    if data.get("status") == "holding":
                        reply += f"- {data.get('token_symbol')} | {data['amount']} @ {data['buy_price']} | {data['strategy']}\n"

            await message.reply(reply, parse_mode="Markdown")

        @self.dp.message_handler(commands=["pnl"])
        async def handle_pnl_command(message: types.Message):
            try:
                summary = get_trade_history_summary(limit=20)
                await message.reply(summary)
            except Exception as e:
                await message.reply(f"❌ Failed to fetch PnL: {e}")

        @self.dp.message_handler(commands=["pause_brain"])
        async def pause_brain_cmd(message: types.Message):
            ai_brain.pause_brain()
            await message.answer("🛑 Nyx has been paused. She will observe but not act.")

        @self.dp.message_handler(commands=["resume_brain"])
        async def resume_brain_cmd(message: types.Message):
            ai_brain.resume_brain()
            await message.answer("✅ Nyx is back in action. Full systems go.")

        @self.dp.message_handler(commands=["brain_status"])
        async def brain_status_cmd(message: types.Message):
            try:
                status = "🟢 Active" if not ai_brain.is_paused() else "🛑 Paused"
                injected_tokens = len(getattr(ai_brain, "injected_tokens", []))
                injected_groups = len(getattr(ai_brain, "injected_groups", []))
                await message.answer(
                    f"🧠 Brain Status: {status}\n"
                    f"💡 Injected Tokens: {injected_tokens}\n"
                    f"📡 Injected Groups: {injected_groups}"
                )
            except Exception as e:
                await message.answer(f"⚠️ Failed to check brain status: {e}")

        @self.dp.message_handler(commands=["llm_explain"])
        async def llm_explain(message: types.Message):
            try:
                from core.llm.llm_brain import llm_brain
                response = await llm_brain.explain_latest_decision()
                await message.reply(response or "🤖 No decision explanation available.")
            except Exception as e:
                await message.reply(f"❌ LLM error: {e}")

        @self.dp.message_handler(commands=["wallet_story"])
        async def wallet_story_handler(message: types.Message):
            try:
                parts = message.text.split()
                wallet_key = parts[1] if len(parts) > 1 else "alpha_rotator"
                path = f"/home/ubuntu/nyx/wallets/{wallet_key}.json"

                if not os.path.exists(path):
                    await message.answer(f"❌ Wallet `{wallet_key}` not found.")
                    return

                wallet = WalletManager.from_file(path)
                wallet_address = getattr(wallet, "address", None)

                if not wallet_address:
                    await message.answer("❌ Could not find wallet address.")
                    return

                story = await self.ai.generate_wallet_story(wallet_address)

                if not story or not story.strip():
                    await message.answer("🤖 Nyx couldn’t craft a story for this wallet yet.")
                    return

                await message.answer(escape_md(story), parse_mode="MarkdownV2")

            except Exception as e:
                await message.answer(f"📉 Failed to generate wallet story: `{e}`", parse_mode="Markdown")

        @self.dp.message_handler(commands=["status"])
        async def status_handler(message: types.Message):
            sol = await self.wallet.get_balance_display()
            mode = config.get("mode", "unknown")
            frenzy = "ACTIVE" if is_frenzy_active() else "OFF"
            ai = "ON" if config.get("ai_strategy") else "OFF"
            text = (
                "📡 *Bot Status:*\n"
                f"• Mode: `{mode}`\n"
                f"• Frenzy: `{frenzy}`\n"
                f"• AI: `{ai}`\n"
                f"• SOL: `{sol}`"
            )
            await message.reply(text, parse_mode="Markdown")

        @self.dp.message_handler(commands=["start_robot"])
        async def start_robot(message: types.Message):
            if is_bot_running():
                await message.reply("✨ *Nyx is already active, darling.* She's watching the charts so you don't have to.")
                return
            launch_main_bot()
            await message.reply("✅ Nyx launched via Telegram.")

        @self.dp.message_handler(commands=["stop"])
        async def stop_robot(message: types.Message):
            if stop_bot_process():
                await message.reply("🛑 Nyx sleepmode entered successfully.")
            else:
                await message.reply("❌ Bot wasn't running or already stopped.")

        @self.dp.message_handler(commands=["wallet_status"])
        async def wallet_handler(message: types.Message):
            from inputs.wallet.multi_wallet_manager import multi_wallet
            try:
                report = await multi_wallet.get_wallets_report()
                await message.reply(report, parse_mode="Markdown")
                text = "💼 *All Wallet Balances:*\n"
            except Exception as e:
                await message.reply(f"❌ Failed to fetch wallet status: `{e}`", parse_mode="Markdown")

        @self.dp.message_handler(commands=["summary"])
        async def cmd_summary(msg: types.Message):
            await self.send_chat_action(msg.chat.id, "typing")
            insights = ai_brain.get_ai_insights()
            formatted = (
                f"*📊 Strategy Summary:*\n"
                f"• Mode: `{insights['active_mode']}`\n"
                f"• Risk: `{insights['risk_mode']}`\n"
                f"• Goal: `{insights['goal_profit']}`\n"
                f"• Tokens Tracked: `{len(insights['token_type_stats'])}`\n"
                f"• Meta Keywords: `{len(insights['meta_keyword_stats'])}`\n"
                f"• Win/Loss Records: `{len(insights['win_loss_timeline'])}`"
            )
            await msg.reply(formatted, parse_mode="Markdown")

        @self.dp.message_handler(commands=["report_strategy"])
        async def report_handler(message: types.Message):
            try:
                report = get_strategy_report()
                await message.reply(f"📈 *Strategy Report:*\n```\n{report}\n```", parse_mode="Markdown")
            except Exception as e:
                await message.reply(f"⚠️ Failed to generate strategy report: {e}")

        @self.dp.message_handler(commands=["tagged_tokens"])
        async def tagged_handler(message: types.Message):
            try:
                tags = get_tagged_tokens_report()
                await message.reply(f"🏷️ Tagged Tokens:\n```\n{tags}\n```", parse_mode="Markdown")
            except Exception as e:
                await message.reply(f"⚠️ Failed to fetch tagged tokens: {e}")

        @self.dp.message_handler(commands=["ai_insights"])
        async def ai_handler(message: types.Message):
            try:
                data = ai_brain.get_ai_insights()
                insights = [
                    f"🧠 *AI Strategy:* {'ON ✅' if data.get('ai_strategy_enabled') else 'OFF ❌'}",
                    f"🎯 *Goal Profit:* `{data.get('goal_profit', '?')}%`",
                    f"📈 *Mode:* `{data.get('active_mode', 'Unknown')}`",
                    f"🦮 *Risk Mode:* `{data.get('risk_mode', 'Unknown')}`",
                    f"🛌 *Idle State:* `{data.get('idle_state', 'Unknown')}`",
                    f"📊 *Volume Trend:* `{', '.join(map(str, data.get('volume_trend', [])[-5:]))}`",
                    "",
                    "🔍 *Token Type Stats:*",
                    "\n".join([f"• {k}: {v}" for k, v in data.get("token_type_stats", {}).items()]) or "None",
                    "",
                    "🏷️ *Meta Keyword Stats:*",
                    "\n".join([f"• {k}: {v}" for k, v in data.get("meta_keyword_stats", {}).items()]) or "None",
                    "",
                    "📰 *Scanner Stats:*",
                    "\n".join([f"• {k}: {v}" for k, v in data.get("scanner_stats", {}).items()]) or "None",
                    "",
                    "🤊 *Cooldowns:*",
                    "\n".join([f"• {k}: {v}" for k, v in data.get("cooldowns", {}).items()]) or "None",
                    "",
                    "📈 *Win/Loss Timeline:*",
                    " ".join(data.get("win_loss_timeline", [])) or "No data",
                ]
                await message.reply("\n".join(insights), parse_mode="Markdown")
            except Exception as e:
                logging.error(f"[Telegram] AI Insights error: {e}")
                await message.reply(f"❌ Error fetching insights: `{e}`", parse_mode="Markdown")

        @self.dp.message_handler(commands=["ai_debug"])
        async def ai_debug_handler(message: types.Message):
            try:
                data = ai_brain.get_ai_insights()
                text = json.dumps(data, indent=2)
                await message.reply(f"```\n{text}\n```", parse_mode="Markdown")
            except Exception as e:
                await message.reply(f"❌ AI Debug error: `{e}`")

        @self.dp.message_handler(commands=["meta_keywords"])
        async def handle_meta_keywords(message: types.Message):
            keywords = self.load_ai_keywords()
            if not keywords:
                await message.reply("No keywords found yet.")
                return
            sorted_keywords = sorted(keywords.items(), key=lambda x: x[1]["count"], reverse=True)[:20]
            lines = []
            for word, meta in sorted_keywords:
                sources = ', '.join(meta.get("sources", []))
                last_seen = meta.get("last_seen", "unknown")
                lines.append(f"`{word}` — {meta['count']} hits, last seen {last_seen}, via {sources}")

            text = "*🧠 Top AI Keywords:*\n" + "\n".join(lines)
            reload_button = InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔄 Reload", callback_data="reload_keywords")
            )
            await message.reply(text, parse_mode="Markdown", reply_markup=reload_button)

        @self.dp.message_handler(commands=["frenzy_status"])
        async def frenzy_handler(message: types.Message):
            active = is_frenzy_active()
            await message.reply("🐟 Feeding Frenzy: ACTIVE" if active else "🐟 Feeding Frenzy: OFF")

        @self.dp.message_handler(commands=["emergency_stop"])
        async def kill_handler(message: types.Message):
            activate_kill_switch("Manual via Telegram")
            await message.reply("🚩 Kill switch activated.")

        @self.dp.message_handler(commands=["restart"])
        async def restart_handler(message: types.Message):
            flush_insights()
            await message.reply("♻️ Restarting bot...")
            os.system("pkill -f main.py && nohup python3 main.py &")

        @self.dp.message_handler(commands=["flush_insights"])
        async def flush_insights_handler(message: types.Message):
            try:
                flush_insights()
                await message.reply("🧹 Insights flushed successfully.")
            except Exception as e:
                await message.reply(f"⚠️ Failed to flush insights: {e}")

        @self.dp.message_handler(commands=["strategy_score"])
        async def strategy_score_handler(message: types.Message):
            try:
                from strategy.strategy_memory import STRATEGIES, get_strategy_score
                lines = [f"{s}: `{get_strategy_score(s):.2f}`" for s in STRATEGIES]
                await message.reply("*📊 Strategy Scores:*\n" + "\n".join(lines), parse_mode="Markdown")
            except Exception as e:
                await message.reply(f"❌ Could not get strategy scores: {e}")

        @self.dp.message_handler(commands=["trim_data"])
        async def handle_trim_data(message: types.Message):
            try:
                from librarian.data_librarian import librarian
                result = librarian.clear_memory_logs()
                await message.reply(result)
            except Exception as e:
                await message.reply(f"⚠️ Could not trim memory: {e}")

        @self.dp.message_handler(commands=["flush_logs"])
        async def flush_logs_handler(message: types.Message):
            try:
                flush_insights()
                await message.reply("🧾 Logs flushed and written to disk.")
            except Exception as e:
                await message.reply(f"⚠️ Flush failed: {e}")

        @self.dp.message_handler(commands=["strategy_snapshot"])
        async def snapshot_handler(message: types.Message):
            try:
                from memory.strategy_snapshot import save_snapshot
                save_snapshot()
                await message.reply("📸 Snapshot of strategy taken.")
            except Exception as e:
                await message.reply(f"⚠️ Snapshot failed: {e}")

        @self.dp.message_handler(commands=["reset_injections"])
        async def reset_injections_handler(message: types.Message):
            ai_brain.manual_token_hint = None
            ai_brain.manual_group_hint = None
            await message.reply("🧼 Manual injections cleared.")

        @self.dp.message_handler(commands=["sentiment"])
        async def sentiment_handler(message: types.Message):
            report = get_sentiment_report()
            await message.reply(report, parse_mode="Markdown")

        @self.dp.message_handler(commands=["service"])
        async def service_handler(message: types.Message):
            from utils.crash_guardian import get_guardian_status_snapshot
            try:
                snapshot = get_guardian_status_snapshot()  # ✅ Corrected method call
                modules = snapshot.get("modules", {})

                # === Dynamic category logic ===
                categories = {
                    "🛰️ Scanners": [m for m in modules if "scanner" in m or "signal" in m],
                    "📊 Trackers": [m for m in modules if "tracker" in m or "spy" in m],
                    "🛡️ Defenders": [m for m in modules if "defender" in m or "blacklist" in m],
                    "⚔️ Race Tools": [m for m in modules if "race" in m],
                    "🧠 Core Engine": [
                        m for m in modules
                        if m not in ["scanner", "tracker", "defender", "race"]
                    ],
                }

                msg = "*📡 Module Status Check:*\n\n"
                for category, mod_list in categories.items():
                    if not mod_list:
                        continue
                    msg += f"*{category}*\n"
                    for name in sorted(mod_list):
                        info = modules.get(name, {})
                        emoji = "🟢" if info.get("alive") else "🔴"
                        last_beat = info.get("last_beat_sec", "?")
                        msg += f"  {emoji} `{name}` — {last_beat}s ago\n"
                    msg += "\n"

                # === System Stats ===
                sys_data = snapshot.get("system", {})
                msg += "*System Stats:*\n"
                msg += f"• CPU: `{sys_data.get('cpu_pct', 0)}%`\n"
                msg += f"• RAM: `{sys_data.get('ram_pct', 0)}%`\n"
                msg += f"• Disk Free: `{sys_data.get('disk_free_pct', 0)}%`\n"
                if sys_data.get("open_fd_pct") is not None:
                    msg += f"• Open FDs: `{sys_data.get('open_fd_pct')}%`\n"

                # === External Links ===
                ext_data = snapshot.get("external", {})
                msg += "\n*External Links:*\n"
                msg += f"• RPC: {'🟢 OK' if ext_data.get('rpc_ok') else '🔴 Down'} ({ext_data.get('rpc_latency_ms', '?')}ms)\n"
                msg += f"• WebSocket: {'🟢 OK' if ext_data.get('websocket_ok') else '🔴 Down'}\n"
                msg += f"• Firehose: {'🟢 OK' if ext_data.get('firehose_ok') else '🔴 Down'}\n"  

                await message.reply(msg, parse_mode="Markdown")

            except Exception as e:
                await message.reply(f"❌ Failed to fetch service status: `{e}`", parse_mode="Markdown")

        @self.dp.message_handler(commands=["config"])
        async def config_handler(message: types.Message):
            await self.send_config_buttons(message.chat.id)

        @self.dp.message_handler(commands=["force_reset"])
        async def force_reset_handler(message: types.Message):
            await message.reply("🧠 Please reply with your 4-digit reset code.")

        @self.dp.message_handler()
        async def check_code(msg: types.Message):
            if msg.reply_to_message and msg.reply_to_message.text and "reset code" in msg.reply_to_message.text:
                if msg.text.strip() == config.get("reset_code", "0000"):
                    await msg.reply("♻️ Code accepted. Restarting bot...")
                    flush_insights()
                    os.system("pkill -f main.py && nohup python3 main.py &")
                else:
                    await msg.reply("❌ Invalid code. Reset aborted.")
                self.dp.message_handlers.unregister(check_code)

        @self.dp.callback_query_handler(lambda c: c.data.startswith("toggle_"))
        async def handle_button(callback_query: types.CallbackQuery):
            key = callback_query.data.replace("toggle_", "")
            config[key] = not config.get(key, False)
            save_config()
            await callback_query.answer("Updated.")
            await self.send_config_buttons(callback_query.from_user.id)

        @self.dp.callback_query_handler(lambda c: c.data.startswith("toggle_"))
        async def process_toggle_buttons(callback_query: types.CallbackQuery):
            toggle = callback_query.data.replace("toggle_", "")
            if toggle == "talk":
                config.llm_talking_enabled = not config.llm_talking_enabled
                state = "ON" if config.llm_talking_enabled else "OFF"
                await callback_query.message.edit_text(f"💬 Talk mode toggled: {state}")

        @self.dp.message_handler()
        async def free_chat(msg: types.Message):
            print("✅ Telegram: Finished registering all handlers.")

            if not FREE_CHAT_ENABLED or not msg.text or msg.text.startswith("/"):
                return
            out = await self.llm.owner_say(msg.text.strip())
            await msg.reply(out)

    async def send_chat_action(self, chat_id: int, action: str = "typing"):
        try:
            await self.bot.send_chat_action(chat_id, action)
        except Exception as e:
            logging.warning(f"[Telegram] Failed to send chat action: {e}")

    def load_ai_keywords(self):
        try:
            with open("/home/ubuntu/nyx/runtime/logs/ai_keywords.json", "r") as f:
                return json.load(f)
        except:
            return {}

    async def send_config_buttons(self, chat_id):
        def toggle_label(key: str, label: str):
            val = config.get(key)
            status = "ON ✅" if val else "OFF ❌"
            return InlineKeyboardButton(f"{label}: {status}", callback_data=f"toggle_{key}")

        # === 🧠 AI Logic
        ai_logic_buttons = [
            [toggle_label("ai_strategy", "AI Strategy"), toggle_label("strategy_rotation", "Rotation")],
            [toggle_label("auto_frenzy", "Auto Frenzy"), toggle_label("wallet_rebalance", "Rebalance")],
            [toggle_label("use_stop_snipe", "Stop Snipe"), toggle_label("lp_filter", "LP Filter")],
            [toggle_label("rebuy_on_dip", "Rebuy on Dip"), toggle_label("contextual_holding", "Contextual Hold")],
            [toggle_label("force_scalp_mode", "Force Scalp"), toggle_label("time_stop_loss", "Time SL")],
        ]

        # === 📡 Learning / Speech / Memory
        learning_buttons = [
            [toggle_label("enable_telegram_learning", "TG Learning"), toggle_label("enable_telegram_talking", "TG Talking")],
            [toggle_label("enable_twitter_learning", "X Learning"), toggle_label("foreign_language_mode", "Language Bridge")],
            [toggle_label("allow_public_alpha", "Public Alpha"), toggle_label("debug_mode", "Debug Mode")],
        ]

        # === 🛡️ Risk + Protection
        risk_buttons = [
            [toggle_label("use_race_protection", "Race Protection"), toggle_label("auto_blacklist_rug", "Auto Blacklist")],
            [toggle_label("sniper_defender_enabled", "Snipe Defender"), toggle_label("junk_token_cleaner", "Junk Cleaner")],
        ]

        # === 🐦 X (Twitter) Behavior Toggles
        x_config_buttons = [
            [toggle_label("x_autopost_enabled", "Auto Post"), toggle_label("x_autofollow_enabled", "Auto Follow")],
            [toggle_label("x_backoff_enabled", "Backoff System"), toggle_label("x_english_only", "English Only")],
            [toggle_label("x_quote_mode", "Quote Mode"), toggle_label("x_post_cooldowns", "Post Cooldown")],
        ]

        # === 🕹️ Bot Behavior
        bot_behavior_buttons = [
            [toggle_label("auto_start", "Auto Start"), toggle_label("auto_sell_mode", "Auto Sell")],
            [toggle_label("manual_override", "Manual Mode"), toggle_label("heartbeat_alerts", "HB Alerts")],
        ]

        # Combine all buttons
        keyboard = InlineKeyboardMarkup(inline_keyboard=(
            ai_logic_buttons +
            learning_buttons +
            risk_buttons +
            x_config_buttons +
            bot_behavior_buttons
        ))

        await self.bot.send_message(chat_id, "⚙️ *Config Panel:*", reply_markup=keyboard, parse_mode="Markdown")

    async def close(self):
        try:
            session = await self.bot.get_session()
            await session.close()
        except Exception as e:
            log_event(f"[TelegramInterface] Failed to close session: {e}")

async def send_message_to_owner(text: str):
    chat_id = config.get("telegram_chat_id")
    if not chat_id:
        return
    try:
        bot = Bot(token=config.get("telegram_bot_token"))
        await bot.send_message(chat_id, text, parse_mode="Markdown")
        await bot.session.close()
    except Exception as e:
        logging.error(f"[TelegramInterface] Failed to send message: {e}")

def log_message_feedback(content: str, engagement: float = 0.0, sentiment: float = 0.0):
    style_evolution().record_message_feedback(
        engagement=engagement,  # 0..1
        sentiment=sentiment,    # -1..1
        length_tokens=len(content.split()), 
        context="telegram"  # or "x" for Twitter
    )


# === External alert hook ===
async def send_telegram_alert(bot_token: str, chat_id: str, message: str):
    bot = Bot(token=bot_token)
    try:
        await bot.send_message(chat_id=chat_id, text=message)
    finally:
        await bot.session.close()
