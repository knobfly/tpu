import asyncio
import logging
import time

from core.live_config import config
from librarian.data_librarian import librarian  # ✅ Replaces ai_brain
from special.insight_logger import log_scanner_insight
from utils.logger import log_event
from utils.service_status import update_status

# === Internal memory for token timestamps ===
recent_tokens = {}  # token -> [timestamps of buys]

# === Parameters ===
RACE_TIME_WINDOW = 8               # seconds for cluster detection
RACE_THRESHOLD = 4                # buys within window
SAFE_MODE_ENABLED = True          # extra cautious
TX_WINDOW_SECONDS = 30
TX_RATE_THRESHOLD = 5             # tx/s global
CHECK_INTERVAL = 10
ENTRY_DELAY_SECONDS = 1.5


class RaceProtector:
    def __init__(self):
        self.last_tx_times = []
        self.delay_active = False
        self.tx_window = TX_WINDOW_SECONDS
        self.threshold_tx_rate = TX_RATE_THRESHOLD
        self.check_interval = CHECK_INTERVAL
        self.delay_seconds = ENTRY_DELAY_SECONDS

    async def run(self):
        log_event("🛡️ Race protection module active.")
        while True:
            update_status("race_protection")
            try:
                await self.check_tx_congestion()
            except Exception as e:
                logging.warning(f"[RaceProtector] Error: {e}")
            await asyncio.sleep(self.check_interval)

    def record_tx_time(self):
        now = time.time()
        self.last_tx_times.append(now)
        self.last_tx_times = [t for t in self.last_tx_times if now - t <= self.tx_window]

    async def check_tx_congestion(self):
        tx_count = len(self.last_tx_times)
        rate = tx_count / self.tx_window if self.tx_window else 0

        if rate >= self.threshold_tx_rate:
            if not self.delay_active:
                self.delay_active = True
                log_event(f"🚧 Sniper race detected — delaying entries by {self.delay_seconds}s")
                log_scanner_insight("race_protection", "global", rate, "delaying")
        else:
            if self.delay_active:
                log_event("✅ Race protection off — TX rate normalized.")
                self.delay_active = False
                log_scanner_insight("race_protection", "global", rate, "resumed")

    def should_delay_entry(self) -> bool:
        return self.delay_active

    def check_token_race_risk(self, token_address: str) -> bool:
        now = time.time()
        timestamps = recent_tokens.get(token_address, [])
        timestamps = [ts for ts in timestamps if now - ts <= RACE_TIME_WINDOW]
        timestamps.append(now)
        recent_tokens[token_address] = timestamps

        if len(timestamps) >= RACE_THRESHOLD:
            log_event(f"⚠️ Token-level sniper race risk: {token_address} ({len(timestamps)} entries)")
            if config.get("race_protection", True):
                librarian.tag_token(token_address, "race_risk")
                return True
        return False


def check_sandwich_risk(token_address: str) -> bool:
    now = time.time()
    timestamps = recent_tokens.get(token_address, [])
    timestamps = [ts for ts in timestamps if now - ts <= RACE_TIME_WINDOW]
    timestamps.append(now)
    recent_tokens[token_address] = timestamps

    if len(timestamps) >= RACE_THRESHOLD:
        log_event(f"⚠️ Sandwich risk detected for {token_address} ({len(timestamps)} rapid buys)")
        if SAFE_MODE_ENABLED or config.get("race_protection", True):
            librarian.tag_token(token_address, "sandwich_risk")
            return True
    return False


# === Singleton instance ===
race_protector = RaceProtector()
