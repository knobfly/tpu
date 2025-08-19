import asyncio
import logging
from datetime import datetime, timedelta

from core.llm.llm_brain import llm_brain
from librarian.data_librarian import librarian
from scoring.scoring_engine import score_token
from special.insight_logger import log_ai_insight
from strategy.strategy_memory import register_overlap_trigger
from utils.logger import log_event
from utils.service_status import update_status
from utils.wallet_helpers import get_recent_smart_buys
from utils.wallet_tracker import get_wallet_tracker_score

# Track recent overlap triggers to avoid spam
recent_triggers = {}
COOLDOWN_SECONDS = 300  # 5 min


def is_triggered_recently(token: str) -> bool:
    last = recent_triggers.get(token)
    if not last:
        return False
    return (datetime.utcnow() - last).total_seconds() < COOLDOWN_SECONDS


def mark_triggered(token: str):
    recent_triggers[token] = datetime.utcnow()


def trigger_overlap_alert(token: str, ai_score: float, wallet_score: float, wallet: str):
    log_event(f"🚨 Alpha+AI Overlap: {token} | AI Score: {ai_score:.2f} | Wallet Score: {wallet_score:.2f}")

    # Record to memory
    register_overlap_trigger(token, ai_score=ai_score, wallet_score=wallet_score)

    # Notify LLM and long-term brain
    librarian.register_overlap(token, ai_score, wallet_score)
    llm_brain.explain_overlap(token, ai_score=ai_score, wallet_score=wallet_score)

    # Log to insight system
    log_ai_insight("wallet_alpha_overlap", {
        "token": token,
        "wallet": wallet,
        "ai_score": round(ai_score, 3),
        "wallet_score": round(wallet_score, 3),
        "time": datetime.utcnow().isoformat()
    })


async def run_alpha_overlap_detector():
    update_status("wallet_alpha_trigger")
    log_event("🤖 Wallet Reaction Watcher activated.")

    while True:
        try:
            buys = get_recent_smart_buys(minutes=3)
            for token, wallet in buys:
                if is_triggered_recently(token):
                    continue

                try:
                    ai_score = await score_token(token)
                except Exception as e:
                    logging.warning(f"[WalletAlphaTrigger] Score error: {e}")
                    continue

                if ai_score < 0.85:
                    continue

                wallet_score = get_wallet_tracker_score(wallet)
                if wallet_score < 0.75:
                    continue

                mark_triggered(token)
                trigger_overlap_alert(token, ai_score, wallet_score, wallet)

        except Exception as e:
            logging.warning(f"[WalletAlphaTrigger] Error during scan: {e}")

        await asyncio.sleep(30)
