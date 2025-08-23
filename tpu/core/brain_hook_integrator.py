import logging
from core.llm.llm_interface import (
    gpt_analyze_trend_theme,
    gpt_journal_trade,
    gpt_label_token_theme,
    gpt_predict_exit_window,
    gpt_score_token_name,
)
from special.insight_logger import log_ai_insight
from strategy.strategy_memory import tag_token_result


# === LLM Token Enrichment Hook ===
async def enrich_token_with_llm(token: str, metadata: dict) -> dict:
    result = {"token": token}

    try:
        score = await gpt_score_token_name(token, metadata)
        theme = await gpt_label_token_theme(token, metadata)

        result["llm_score"] = score
        result["llm_theme"] = theme

        tag_token_result(token, f"llm_score:{int(score)}", score)
        tag_token_result(token, f"llm_theme:{theme}", 80)

        log_ai_insight("🧠 LLM Enriched Token", {
            "token": token,
            "score": score,
            "theme": theme
        })

        return result

    except Exception as e:
        logging.warning(f"[LLM Router] Failed to enrich token {token}: {e}")
        return result


# === LLM Trade Journal Summary ===
async def summarize_trade_journal(token: str, action: str, result: str, reason: list) -> str:
    try:
        summary = await gpt_journal_trade(token, action, result, reason)
        log_ai_insight("🧾 Trade Journal", {"summary": summary})
        return summary
    except Exception as e:
        logging.warning(f"[LLM Router] Failed trade summary for {token}: {e}")
        return "No summary."


# === Meta Theme Summary from Trending ===
async def detect_meta_trend(keywords: list[str]) -> str:
    try:
        theme = await gpt_analyze_trend_theme(keywords)
        log_ai_insight("🧠 Meta Trend", {"keywords": keywords, "theme": theme})
        return theme
    except Exception as e:
        logging.warning(f"[LLM Router] Meta trend fail: {e}")
        return "unknown"


# === Exit Planner ===
async def generate_exit_strategy(token: str, launch_data: dict) -> str:
    try:
        advice = await gpt_predict_exit_window(token, launch_data)
        log_ai_insight("💸 Exit Strategy", {"token": token, "advice": advice})
        return advice
    except Exception as e:
        logging.warning(f"[LLM Router] Exit planner failed for {token}: {e}")
        return "No exit plan"
