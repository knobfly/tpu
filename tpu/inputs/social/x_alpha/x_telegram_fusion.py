# /x_alpha/x_telegram_fusion.py

from inputs.social.telegram_group_scanner import scan_message
from inputs.social.x_alpha.alpha_account_tracker import alpha_account_tracker
from special.insight_logger import log_scanner_insight
from strategy.strategy_memory import tag_token_result


def fuse_token_signal(token, telegram_group, x_handle, score=0):
    """
    Fuse a cross-platform signal from both Telegram and X.
    Boost reputation, tag token, and log correlated insight.
    """

    # 🏷️ Tag for group tracking
    scan_message.tag_token(token, telegram_group, reason="x_correlation")

    # 🧠 Register post and apply tag
    alpha_account_tracker.register_post(x_handle, token, outcome="pending")
    tag_token_result(token, "cross_platform_alpha")
    tag_token_result(token, "tg_x_overlap")

    # 📊 Log insight
    log_scanner_insight(
        token=token,
        source="tg+x_fusion",
        sentiment=score,
        volume=1,
        result="tg_x_overlap"
    )

    return {
        "token": token,
        "group": telegram_group,
        "x_handle": x_handle,
        "correlated": True,
        "score": score
    }
