# modules/strategy_auditor.py

import logging
from datetime import datetime

from core.live_config import config
from special.insight_logger import log_ai_insight
from strategy.strategy_memory import get_strategy_performance, get_tagged_tokens
from utils.logger import log_event
from utils.service_status import update_status

# Optional reverse learning
try:
    from special.reverse_learning import record_strategy_result
except ImportError:
    record_strategy_result = None

# Optional AI integration
try:
    from core.ai_brain import ai_brain
except ImportError:
    ai_brain = None


async def analyze_strategy_memory():
    """
    Real-time trending tag memory audit.
    """
    update_status("strategy_auditor")
    try:
        tokens = get_tagged_tokens()
        if not isinstance(tokens, dict) or not tokens:
            log_event("⚠️ [StrategyAudit] Strategy data malformed or empty.")
            return

        tag_counts = {}
        for info in tokens.values():
            for tag in info.get("tags", []):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        log_event(f"📊 [StrategyAudit] Top memory tags: {top_tags}")

        # Optional: Inject top tag awareness
        if ai_brain:
            for tag, count in top_tags:
                ai_brain.inject_keyword(tag, score=count)

    except Exception as e:
        log_event(f"❌ [StrategyAudit] Tag analysis error: {e}")


def audit_strategy():
    """
    Full strategy performance audit + AI learning + logging.
    """
    update_status("strategy_auditor")

    try:
        summary_result = get_strategy_performance(strategy="default")
        summary, data = summary_result if isinstance(summary_result, tuple) else ({}, summary_result)
    except Exception as e:
        logging.warning(f"[StrategyAudit] Failed to retrieve strategy data: {e}")
        return {}

    if not isinstance(data, dict) or not data:
        logging.warning("[StrategyAudit] Strategy data malformed or empty.")
        return {}

    insight = {
        "timestamp": datetime.utcnow().isoformat(),
        "summary": "",
        "recommendations": [],
        "highlighted": [],
        "confidence": 50,
    }

    high_performers = []
    underperformers = []
    low_volume = []
    total_trades = 0
    total_wins = 0

    for strat, stats in data.items():
        wins = stats.get("win", 0)
        losses = stats.get("loss", 0)
        total = wins + losses

        if total < 3:
            low_volume.append((strat, total))
            continue

        win_rate = wins / total if total > 0 else 0
        total_trades += total
        total_wins += wins

        if win_rate >= 0.65:
            high_performers.append((strat, win_rate, total))
        elif win_rate < 0.4:
            underperformers.append((strat, win_rate, total))

        if record_strategy_result:
            record_strategy_result(strat, win_rate, total)

        if ai_brain:
            if win_rate >= 0.75:
                ai_brain.tag_strategy(strat, "🔥")
            elif win_rate < 0.35:
                ai_brain.tag_strategy(strat, "🧊")

    insight["highlighted"] = [s[0] for s in high_performers]
    insight["confidence"] += len(high_performers) * 5
    insight["confidence"] -= len(underperformers) * 5
    insight["confidence"] = max(10, min(insight["confidence"], 100))

    summary_lines = []
    for strat, rate, count in sorted(high_performers, key=lambda x: -x[1]):
        summary_lines.append(f"✅ `{strat}`: {rate:.1%} win rate over {count} trades")
    for strat, rate, count in sorted(underperformers, key=lambda x: x[1]):
        summary_lines.append(f"⚠️ `{strat}`: {rate:.1%} win rate over {count} trades")
    for strat, count in low_volume:
        summary_lines.append(f"ℹ️ `{strat}`: low usage ({count} trades)")

    if total_trades > 0:
        overall_win_rate = total_wins / total_trades
        insight["summary"] = (
            f"Total Trades: {total_trades} | Overall Win Rate: {overall_win_rate:.1%}\n" +
            "\n".join(summary_lines)
        )
    else:
        insight["summary"] = "No recent strategy usage to audit."

    if underperformers:
        insight["recommendations"].append("⚠️ Rotate or pause underperforming strategies.")
    if high_performers:
        insight["recommendations"].append("✅ Boost weights of top strategies.")
    if low_volume:
        insight["recommendations"].append("🔎 Gather more data on low-usage strategies.")
    if insight["confidence"] > 75:
        insight["recommendations"].append("🟢 Strategy pool looks healthy.")

    log_event(f"[StrategyAudit] 🧠 {insight['summary']}")
    log_ai_insight(insight)
    return insight
