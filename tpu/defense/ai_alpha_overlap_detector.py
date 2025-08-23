# /ai_alpha_overlap_detector.py

import asyncio
import logging
from datetime import datetime

from core.live_config import config
from cortex.meta_cortex import is_overlap_candidate
from inputs.wallet.wallet_alpha_trigger import get_recent_smart_buys
from librarian.data_librarian import librarian
from scoring.scoring_engine import score_token
from special.insight_logger import log_scanner_insight
from utils.logger import log_event
from utils.service_status import update_status

CHECK_INTERVAL = 15  # seconds
TRIGGER_SCORE = 85
OVERLAP_SCORE_THRESHOLD = 70
recent_triggers = set()


async def run_alpha_overlap_detector(wallet_manager=None):
    update_status("ai_alpha_overlap_detector")
    log_event("🤖 AI Alpha Overlap Detector activated.")

    while True:
        try:
            buys = get_recent_smart_buys(minutes=3)
            for token in buys:
                if token in recent_triggers:
                    continue

                ai_score = await score_token(token)
                if ai_score < OVERLAP_SCORE_THRESHOLD:
                    continue

                overlap = is_overlap_candidate(token, threshold=OVERLAP_SCORE_THRESHOLD)
                if overlap and ai_score >= TRIGGER_SCORE:
                    recent_triggers.add(token)
                    await trigger_overlap_alert(token, ai_score)

        except Exception as e:
            logging.warning(f"[AlphaOverlap] Error during scan: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


async def trigger_overlap_alert(token: str, score: float):
    log_event(f"[AlphaOverlap] 🚀 Overlap Detected: {token} | AI Score: {score}")

    log_scanner_insight(
        token=token,
        source="ai_alpha_overlap",
        sentiment=score,
        volume=0,
        result="overlap_trigger",
        tags=[f"timestamp:{datetime.utcnow().isoformat()}"]
    )

    try:
        await librarian.react_to_overlap(token, score)
    except Exception as e:
        logging.warning(f"[AlphaOverlap] react_to_overlap failed: {e}")
