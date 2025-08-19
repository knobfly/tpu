# volume_spike_detector.py

import asyncio
import logging
import time

from core.ai_brain import ai_brain  # (not used here yet, but kept for parity/terminology)
from core.live_config import config
from cortex.meta_cortex import assess_confidence
from cortex.txn_cortex import register_buy
from defense.race_protection import check_sandwich_risk
from exec.trade_executor import TradeExecutor
from librarian.data_librarian import librarian
from special.insight_logger import log_ai_insight, log_scanner_insight
from strategy.stop_snipe_defender import activate_stop_snipes  # (not used directly here)
from utils.logger import log_event
from utils.token_utils import get_token_metadata, get_token_volume

# --- Tunables ---
VOLUME_SPIKE_THRESHOLD = 3000   # Minimum volume in SOL
CHECK_INTERVAL = 8              # How often to scan
CONFIDENCE_THRESHOLD = 7.0      # AI threshold to allow buy


class VolumeSpikeDetector:
    def __init__(self):
        # track seen tokens in this process to avoid duplicate work in a short window
        # token -> first-seen ts
        self.recent_tokens: dict[str, float] = {}

    async def run(self):
        log_event("⚡ Volume Spike Detector running...")
        while True:
            try:
                await self.scan_recent_tokens()
            except Exception as e:
                logging.error(f"[VolumeSpikeDetector] Error: {e}")
            await asyncio.sleep(CHECK_INTERVAL)

    async def scan_recent_tokens(self):
        """
        Pull trending token candidates from Librarian (fused sources),
        confirm a realtime volume threshold, score, assess confidence,
        and optionally trigger a buy via TradeExecutor.
        """
        try:
            # pull a compact, ranked set; adjust knobs as you like
            token_list = librarian.get_trending_token_candidates(limit=50, window_minutes=60)
        except Exception as e:
            logging.warning(f"❌ Failed to fetch token candidates from AI brain: {e}")
            return

        for token_address in token_list or []:
            # skip if we've processed this token in the current session
            if token_address in self.recent_tokens:
                continue

            self.recent_tokens[token_address] = time.time()

            try:
                # --- Guard on actual on-chain volume ---
                volume = await get_token_volume(token_address)  # expected async
                thresh = float(config.get("volume_threshold", VOLUME_SPIKE_THRESHOLD))
                if not volume or float(volume) < thresh:
                    continue

                # --- Metadata + scoring ---
                metadata = await get_token_metadata(token_address)  # expected async

                # score_token may be sync or async; handle both
                try:
                    from scoring.scoring_engine import score_token
                    score_data = score_token(token_address)
                    if asyncio.iscoroutine(score_data):
                        score_data = await score_data
                except Exception:
                    score_data = {}

                score = float(score_data.get("score", 0.0))

                # --- AI confidence (meta-cortex) ---
                confidence = float(assess_confidence(token_address, metadata, score))

                # --- Logging & insights ---
                log_event(
                    f"⚡ Volume spike token: {token_address} ({float(volume):.2f} SOL) "
                    f"→ Score: {score:.2f}, AI: {confidence:.2f}"
                )
                try:
                    log_scanner_insight("volume_spike", token_address, score, float(volume))
                    log_ai_insight({
                        "timestamp": time.time(),
                        "module": "volume_spike_detector",
                        "token": token_address,
                        "volume": float(volume),
                        "score": score,
                        "confidence": confidence,
                    })
                except Exception:
                    pass

                # --- Route decision ---
                if confidence >= CONFIDENCE_THRESHOLD:
                    # Anti-sandwich / MEV protection
                    if check_sandwich_risk(token_address):
                        log_event(f"🚫 Skipping {token_address} due to sandwich risk")
                        continue

                    # Tag source
                    try:
                        librarian.record_scanner_source(token_address, "volume_spike")
                    except Exception:
                        pass

                    # Execute buy (handle async/sync implementation)
                    tx = TradeExecutor.buy_token(
                        token=token_address,
                        metadata=metadata,
                        score=score,
                        source="volume_spike",
                    )
                    if asyncio.iscoroutine(tx):
                        tx = await tx

                    # Register with txn cortex memory
                    try:
                        register_buy(token_address, wallet=None, tx=tx)
                    except Exception:
                        pass
                else:
                    log_event(f"⏳ Ignored {token_address} — confidence too low ({confidence:.2f})")

            except Exception as e:
                logging.warning(f"⚠️ Failed to process {token_address}: {e}")


# === Scoring Helper (optional utility used elsewhere) ===
async def get_volume_spike_score(token_address: str) -> float:
    """Score token volume on a 0–100 scale based on spike magnitude."""
    try:
        volume = await get_token_volume(token_address)
        if not volume:
            return 0.0
        v = float(volume)
        if v >= 10000:
            return min(v / 1000.0, 100.0)
        return 0.0
    except Exception as e:
        logging.warning(f"[VolumeSpikeScore] Error for {token_address}: {e}")
        return 0.0
