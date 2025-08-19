import logging
import os
from datetime import datetime

LOG_DIR = "/home/ubuntu/nyx/runtime/logs"
TRADE_LOG = os.path.join(LOG_DIR, "trades.log")
ERROR_LOG = os.path.join(LOG_DIR, "errors.log")
SYSTEM_LOG = os.path.join(LOG_DIR, "system.log")

# Ensure log directory exists
os.makedirs(LOG_DIR, exist_ok=True)

# === Base Logger Setup ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(SYSTEM_LOG),
        logging.StreamHandler()
    ]
)

# === Trade Logger ===
def log_trade(token_name: str, token_address: str, sol_amount: float, pnl: float, outcome: str):
    entry = f"{timestamp()} | {token_name} ({token_address}) | Amount: {sol_amount:.3f} SOL | PnL: {pnl:.4f} | Result: {outcome.upper()}\n"
    with open(TRADE_LOG, "a") as f:
        f.write(entry)

# === Error Logger ===
def log_error(error: str):
    entry = f"{timestamp()} | ERROR: {error}\n"
    with open(ERROR_LOG, "a") as f:
        f.write(entry)
    logging.error(error)

# === System Event Logger ===
def log_event(event: str):
    entry = f"{timestamp()} | {event}\n"
    with open(SYSTEM_LOG, "a") as f:
        f.write(entry)
    logging.info(event)

# === Timestamp Generator ===
def timestamp():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

# === Log Cleanup ===
def trigger_log_cleanup(max_lines: int = 1000):
    for path in [TRADE_LOG, ERROR_LOG, SYSTEM_LOG]:
        if os.path.exists(path):
            with open(path, "r") as f:
                lines = f.readlines()
            if len(lines) > max_lines:
                with open(path, "w") as f:
                    f.writelines(lines[-max_lines:])
                logging.info(f"🧹 Cleaned {path}: kept last {min(len(lines), max_lines)} lines")
