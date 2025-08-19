import asyncio
import logging

from core.ai_brain import ai_brain
from core.live_config import config
from cortex.meta_cortex import assess_confidence
from cortex.txn_cortex import register_buy
from exec.trade_executor import TradeExecutor
from librarian.data_librarian import librarian
from special.insight_logger import log_scanner_insight
from utils.logger import log_event
from utils.service_status import update_status
from utils.token_utils import get_token_metadata

REFRESH_INTERVAL = 45  # seconds
VOLUME_THRESHOLD = 800  # SOL
SENTIMENT_THRESHOLD = 0.3  # low sentiment
MAX_LOOKBACK = 5  # minutes

class VolumeDivergenceDetector:
    def __init__(self):
        self.seen = set()

    async def run(self):
        log_event("⚠️ Volume Divergence Detector running...")
        while True:
            try:
                await self.scan()
            except Exception as e:
                logging.warning(f"[Divergence] Error: {e}")
            await asyncio.sleep(REFRESH_INTERVAL)

    async def scan(self):
        from scoring.scoring_engine import score_token
        from utils.sentiment_cache import get_recent_sentiment
        from utils.volume_utils import get_recent_token_volumes

        update_status("volume_divergence")

        volumes = await get_recent_token_volumes(minutes=MAX_LOOKBACK)
        for token, vol in volumes.items():
            if token in self.seen or vol < VOLUME_THRESHOLD:
                continue

            sentiment = get_recent_sentiment(token)
            if sentiment >= SENTIMENT_THRESHOLD:
                continue  # not divergent

            self.seen.add(token)

            try:
                metadata = await get_token_metadata(token)
                if not metadata:
                    continue

                score_result = await score_token(token, config, wallet=None)
                score = score_result.get("score", 0)
                confidence = assess_confidence(token, metadata, score)

                log_event(f"⚠️ Volume Spike w/ Low Sentiment → {token} | Vol: {vol:.0f} SOL | Sentiment: {sentiment:.2f} | Score: {score:.2f}")
                log_scanner_insight(
                    token=token,
                    source="volume_divergence",
                    sentiment=sentiment,
                    volume=vol,
                    result="divergent_spike"
                )

                if confidence >= config.get("divergence_conf_threshold", 6.5):
                    librarian.record_scanner_source(token, "volume_divergence")
                    tx = TradeExecutor.buy_token(token, metadata, score, source="volume_divergence")
                    register_buy(token, wallet=None, tx=tx)
            except Exception as e:
                logging.warning(f"[Divergence] Failed to process {token}: {e}")
