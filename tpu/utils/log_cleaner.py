import asyncio
import logging
import os
from datetime import datetime

from librarian.data_librarian import librarian

# === Logs to Clean ===
LOG_FILES = [
    "/home/ubuntu/nyx/runtime/logs/api_server.log",
    "/home/ubuntu/nyx/runtime/logs/controller.log",
    "/home/ubuntu/nyx/runtime/logs/system.log",
    "/home/ubuntu/nyx/runtime/logs/telegram.log",
    "/home/ubuntu/nyx/runtime/logs/watchdog.log",
    "/home/ubuntu/nyx/runtime/logs/main_output.log",
    "/home/ubuntu/nyx/runtime/logs/bot_engine.log"
]

# === Keywords to Keep ===
KEYWORDS = ["BUY", "SELL", "RUG", "ERROR", "FRENZY", "PROFIT", "STRATEGY", "HONEYPOT"]

async def clean_logs():
    for path in LOG_FILES:
        try:
            if not os.path.exists(path):
                continue

            with open(path, "r", encoding="utf-8", errors="ignore") as file:
                lines = file.readlines()

            important = [line for line in lines if any(kw in line.upper() for kw in KEYWORDS)]
            tail = lines[-500:] if len(lines) > 500 else lines
            final_lines = list(dict.fromkeys(important + tail))  # Deduped

            final_lines.append(f"{datetime.utcnow().isoformat()} 🧹 Log cleaned.\n")

            with open(path, "w", encoding="utf-8") as file:
                file.writelines(final_lines)

            logging.info(f"🧼 Cleaned {os.path.basename(path)} — kept {len(final_lines)} lines.")
        except Exception as e:
            logging.warning(f"[LogCleaner] Failed to clean {path}: {e}")

# === Optional Background Cleaner ===
async def run_hourly_maintenance():
    while True:
        try:
            await clean_logs()
            librarian.prune_memory()  # 🧠 Prune outdated signals
            librarian.evolve_strategy()  # 🧠 Learn from outcomes
        except Exception as e:
            logging.warning(f"[Maintenance] Error during upkeep: {e}")
        await asyncio.sleep(3600)  # Run every hour
