# /daily_summary_report.py

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from core.llm.llm_brain import get_strategy_rotation_log
from inputs.social.x_alpha.x_signal_logger import get_x_activity_log
from inputs.wallet.wallet_behavior_analyzer import get_recent_wallet_activity
from librarian.data_librarian import librarian
from memory.trade_history import get_trade_history_summary
from strategy.strategy_memory import get_recent_keywords
from utils.crash_guardian import crash_guardian
from utils.logger import log_event
from utils.service_status import last_run_timestamps


def _format_lines(items, no_data_msg="No data."):
    return "\n".join(items) if items else f"• {no_data_msg}"

async def get_daily_summary():
    now = datetime.utcnow()
    since = now - timedelta(hours=24)

    report = [f"🧠 *Nyx Daily Intelligence Report*\n_(Last 24 hours)_\n"]

    # === 1. Trades ===
    try:
        trades = get_trade_history_summary(since=since)
        if trades:
            lines = [f"• {t['token']} — {t['result']} ({t['reason']})" for t in trades]
            win_rate = sum(1 for t in trades if t['result'] == 'win') / len(trades) * 100
            report.append(f"📈 *Trades* — {len(trades)} total | {win_rate:.1f}% winrate")
            report.append(_format_lines(lines))
        else:
            report.append("📈 *Trades*")
            report.append("• No trades in the last 24 hours.")
    except Exception as e:
        logging.warning(f"[DailyReport] Failed trades: {e}")

    # === 2. Module Failures ===
    try:
        issues = crash_guardian.get_guardian_status_snapshot(since=since)
        lines = [f"• {name} failed {count}x" for name, count in issues.items()]
        report.append("\n❌ *Module Failures*")
        report.append(_format_lines(lines))
    except Exception as e:
        logging.warning(f"[DailyReport] Module fail check failed: {e}")

    # === 3. Keywords Learned ===
    try:
        keywords = get_recent_keywords(since=since)
        lines = [f"• {kw['keyword']} ({kw['source']})" for kw in keywords]
        report.append("\n🧠 *Keywords Learned*")
        report.append(_format_lines(lines))
    except Exception as e:
        logging.warning(f"[DailyReport] Keyword fetch failed: {e}")

    # === 4. Telegram Activity ===
    try:
        groups = librarian.load_json_file("/home/ubuntu/nyx/runtime/telegram/groups_joined.json")
        recent = [g for g in groups if datetime.fromisoformat(g["joined_at"]) > since]
        lines = [f"• Joined: {g['name']}" for g in recent]
        report.append("\n💬 *Telegram Activity*")
        report.append(_format_lines(lines))
    except Exception:
        report.append("\n💬 *Telegram Activity*")
        report.append("• No group join data.")

    # === 5. X Activity ===
    try:
        x_actions = get_x_activity_log(since=since)
        follows = x_actions.get("follows", [])
        replies = x_actions.get("replies", [])
        posts = x_actions.get("posts", [])
        lines = []
        if follows:
            lines.append(f"• Followed {len(follows)} accounts")
        if posts:
            lines.append(f"• Posted {len(posts)} times")
        if replies:
            lines.append(f"• Replied {len(replies)} times")
        report.append("\n📡 *X Activity*")
        report.append(_format_lines(lines))
    except Exception as e:
        logging.warning(f"[DailyReport] X log fail: {e}")

    # === 6. Wallet Intelligence ===
    try:
        wallets = await get_recent_wallet_activity(since=since)
        lines = [f"• {w['address']} — {w.get('label', 'Unknown')}" for w in wallets]
        report.append("\n👛 *Wallet Intelligence*")
        report.append(_format_lines(lines))
    except Exception as e:
        logging.warning(f"[DailyReport] Wallet activity failed: {e}")

    # === 7. Strategy/Meta Rotation ===
    try:
        rotations = get_strategy_rotation_log(since=since)
        lines = [f"• {r['strategy']} → {r['meta']} @ {r['time']}" for r in rotations]
        report.append("\n🧠 *Strategy & Meta*")
        report.append(_format_lines(lines))
    except Exception:
        report.append("\n🧠 *Strategy & Meta*")
        report.append("• No changes.")

    report.append(f"\n🗓️ Report Generated: `{now.isoformat()}`")
    return "\n".join(report)



def _format_lines(lines: List[str]) -> str:
    return "\n".join(lines) if lines else "• No data."
