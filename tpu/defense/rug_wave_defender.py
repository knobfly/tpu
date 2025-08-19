import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta

from core.live_config import config
from special.ai_self_tuner import learn_from_result
from special.insight_logger import log_ai_insight
from utils.logger import log_event
from utils.service_status import update_status
from utils.universal_input_validator import coerce_to_dict

# Rug wave detection memory (can be expanded to persistent store)
_rug_events = defaultdict(list)  # token -> [timestamps]

# Threshold settings
RUG_WAVE_WINDOW = 120  # seconds
RUG_WAVE_THRESHOLD = 3  # events within window



class RugWaveDefender:
    RUG_WINDOW_SECONDS = 300
    RUG_LIMIT_PER_WINDOW = 2
    PAUSE_DURATION_MINUTES = 15

    def __init__(self, telegram_interface=None):
        self.rug_timestamps = []
        self.recent_rug_tokens = {}
        self.rug_pause_until = None
        self.tg = telegram_interface

    def is_snipe_blocked(self) -> bool:
        return bool(self.rug_pause_until and datetime.utcnow() < self.rug_pause_until)

    def record_rug_event(self, token_address: str):
        now = datetime.utcnow()
        self.rug_timestamps.append(now)
        self.recent_rug_tokens[token_address] = now

        window_start = now - timedelta(seconds=self.RUG_WINDOW_SECONDS)
        self.rug_timestamps[:] = [ts for ts in self.rug_timestamps if ts > window_start]
        self.recent_rug_tokens = {t: ts for t, ts in self.recent_rug_tokens.items() if ts > window_start}

        rug_count = len(self.rug_timestamps)
        log_event(f"⚠️ Rug recorded: {token_address} | {rug_count} rugs in last 5 min")
        learn_from_result(token_address, "rug_wave", -100)
        log_ai_insight("tag_token", {
            "token": token_address,
            "tag": "rug_wave_rug",
            "reason": "High rug frequency",
            "timestamp": now.timestamp()
        })

        if rug_count >= self.RUG_LIMIT_PER_WINDOW:
            self.rug_pause_until = now + timedelta(minutes=self.PAUSE_DURATION_MINUTES)
            config["stop_snipe_mode"] = True

            msg = (
                f"🔴 *Rug Wave Detected!*\n\n"
                f"Too many rugs in the past {self.RUG_WINDOW_SECONDS // 60} minutes.\n"
                f"Sniping paused for {self.PAUSE_DURATION_MINUTES} minutes."
            )
            log_event("🚨 Pausing snipes due to rug wave.")
            log_ai_insight("rug_wave_pause", {"rugs": rug_count})
            if self.tg:
                asyncio.create_task(self.tg.send_message(msg, parse_mode="Markdown"))

    def resume_snipe_if_ready(self):
        now = datetime.utcnow()
        update_status("rugwave_defender")

        if self.rug_pause_until and now >= self.rug_pause_until:
            self.rug_pause_until = None
            if config.get("stop_snipe_mode"):
                config["stop_snipe_mode"] = False
                log_event("✅ Resuming snipes after rug wave cooldown.")
                log_ai_insight("rug_wave_resume")
                if self.tg:
                    asyncio.create_task(
                        self.tg.send_message("🟢 *Sniping resumed after rug cooldown.*", parse_mode="Markdown")
                    )

    def manual_resume(self):
        self.rug_pause_until = None
        if config.get("stop_snipe_mode"):
            config["stop_snipe_mode"] = False
            log_event("🛠️ Manual override: Resuming snipes.")
            if self.tg:
                asyncio.create_task(
                    self.tg.send_message("🔓 *Manual override: Sniping resumed*", parse_mode="Markdown")
                )

    def get_rug_penalty(self, token_address: str) -> int:
        now = datetime.utcnow()
        ts = self.recent_rug_tokens.get(token_address)
        if ts and now - ts <= timedelta(seconds=self.RUG_WINDOW_SECONDS):
            return 50
        return 0

    def get_status(self):
        if self.is_snipe_blocked():
            remaining = int((self.rug_pause_until - datetime.utcnow()).total_seconds())
            return f"⛔ Paused ({remaining // 60}m left)"
        return "✅ Active"

    async def run(self):
        log_event("🌊 RugWaveDefender started.")
        while True:
            try:
                self.resume_snipe_if_ready()
            except Exception as e:
                logging.error(f"[RugWaveDefender] Error: {e}")
            await asyncio.sleep(10)


rug_wave_defender = RugWaveDefender()

def log_rug_event(token_address: str):
    now = time.time()
    _rug_events[token_address].append(now)

    # Trim old events
    _rug_events[token_address] = [
        ts for ts in _rug_events[token_address] if now - ts <= RUG_WAVE_WINDOW
    ]

def detect_rug_wave(token_address: str) -> bool:
    """
    Detects if multiple rug-like events have occurred in a short time span,
    indicating a possible rug wave across related tokens or contracts.
    """
    timestamps = _rug_events.get(token_address, [])
    if len(timestamps) >= RUG_WAVE_THRESHOLD:
        logging.warning(f"[RugWave] Detected rug wave for {token_address} ({len(timestamps)} events)")
        return True
    return False

def get_recent_rug_wave_tokens() -> list[str]:
    now = time.time()
    return [
        token for token, events in _rug_events.items()
        if len([ts for ts in events if now - ts <= RUG_WAVE_WINDOW]) >= RUG_WAVE_THRESHOLD
    ]


def evaluate_token_for_rug(token: dict) -> bool:
    token = coerce_to_dict(token, "AutoRug.eval_input")

    lp = token.get("liquidity", 0)
    volume = token.get("volume", 0)
    owner_percent = token.get("owner_percent", 0)
    has_mint_auth = token.get("has_mint_auth", True)
    trading_open = token.get("trading_open", True)
    created_seconds_ago = token.get("age_seconds", 0)
    renounced = token.get("renounced", False)

    suspicious_flags = []

    if lp < 500:
        suspicious_flags.append("🩸 Low LP")
    if volume < 1000:
        suspicious_flags.append("📉 Low Volume")
    if owner_percent > 10:
        suspicious_flags.append(f"👑 Owner holds {owner_percent:.2f}%")
    if has_mint_auth:
        suspicious_flags.append("🧬 Mint authority present")
    if not trading_open:
        suspicious_flags.append("🚫 Trading not open")
    if not renounced:
        suspicious_flags.append("🔐 Ownership not renounced")
    if created_seconds_ago < 300:
        suspicious_flags.append("⏰ Very new")

    if suspicious_flags:
        log_event(f"🚨 [AutoRug] Token flagged as suspicious: {', '.join(suspicious_flags)}")
        log_ai_insight("token_rug_warning", {
            "token": token.get("address", "unknown"),
            "reasons": suspicious_flags
        })
        return True

    return False


def get_rug_penalty(token_address: str = "") -> float:
    if rug_wave_defender.is_snipe_blocked():
        return 1.0
    return 0.5 if token_address and rug_wave_defender.get_rug_penalty(token_address) else 0.0
