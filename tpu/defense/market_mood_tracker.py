# /market_mood_tracker.py

import asyncio
import logging
from typing import Any
from inputs.meta_data.token_metadata_fetcher import fetch_sol_volume
from librarian.data_librarian import librarian
from utils.logger import log_event
from utils.service_status import update_status

MOOD_INTERVAL = 300  # every 5 minutes
MOOD_HISTORY = []
MOOD_ALERT_THRESHOLD = 15
MAX_HISTORY = 12  # keep 1 hour of data

VOLUME_TIERS = {
    "dead": 50_000,
    "normal": 150_000,
    "hype": 300_000
}

tg = None
last_mood = None


def attach_telegram(bot: Any):
    global tg
    tg = bot


def classify_mood(score: int) -> str:
    if score >= 70:
        return "🟢 HYPE"
    elif score >= 40:
        return "🟡 Neutral"
    return "🔴 Deadzone"


async def calculate_mood_score():
    try:
        daily_volume_sol = await fetch_sol_volume()
        if daily_volume_sol is None:
            raise ValueError("No volume data")

        if daily_volume_sol < VOLUME_TIERS["dead"]:
            volume_score = 10
        elif daily_volume_sol < VOLUME_TIERS["normal"]:
            volume_score = 40
        elif daily_volume_sol < VOLUME_TIERS["hype"]:
            volume_score = 70
        else:
            volume_score = 90

        try:
            from librarian.data_librarian import librarian
            smart_wallets = await librarian.get_recent_smart_wallet_activity(minutes=30)
            smart_count = len(smart_wallets)
            if smart_count == 0:
                wallet_score = 0
            elif smart_count < 5:
                wallet_score = 10
            elif smart_count < 10:
                wallet_score = 20
            else:
                wallet_score = 30
        except Exception as e:
            logging.warning(f"[MarketMood] Smart wallet scoring failed: {e}")
            wallet_score = 10  # fallback

        return min(volume_score + launch_score + wallet_score, 100)

    except Exception as e:
        logging.warning(f"[MarketMood] ❌ Failed to calculate mood: {e}")
        return None


async def run():
    global last_mood
    update_status("market_mood_tracker")
    log_event("📈 Market Mood Tracker running.")

    while True:
        score = await calculate_mood_score()
        if score is not None:
            MOOD_HISTORY.append(score)
            if len(MOOD_HISTORY) > MAX_HISTORY:
                MOOD_HISTORY.pop(0)

            mood_label = classify_mood(score)
            try:
                await librarian.update_market_mood(score)
            except Exception as e:
                logging.warning(f"[MarketMood] Librarian update failed: {e}")

            if last_mood is None or abs(score - last_mood) >= MOOD_ALERT_THRESHOLD:
                log_event(f"📊 Market Mood Shift: {score}/100 → {mood_label}")
                try:
                    if tg:
                        await tg.send_message(f"📊 Market Mood Update: {score}/100 → {mood_label}")
                except Exception as e:
                    logging.warning(f"[MarketMood] Telegram send failed: {e}")
                last_mood = score

        await asyncio.sleep(MOOD_INTERVAL)

def get_current_meta_trend() -> str:
    if not MOOD_HISTORY:
        return "Unknown"

    avg_score = sum(MOOD_HISTORY) / len(MOOD_HISTORY)
    return classify_mood(avg_score)
