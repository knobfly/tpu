# /emergency_kill_switch.py

import logging
import os

from core.live_config import config
from special.insight_logger import log_ai_insight
from utils.logger import log_event

KILL_SWITCH_FILE = "emergency_stop.flag"

def activate_kill_switch(reason: str = "Unknown"):
    """
    Creates the kill switch flag and logs the event.
    """
    with open(KILL_SWITCH_FILE, "w") as f:
        f.write(reason)
    config["stop_snipe_mode"] = True
    log_event(f"🛑 Emergency Kill Switch Activated! Reason: {reason}")
    logging.warning("❗ All sniping is now disabled until manually reset.")
    log_ai_insight("kill_switch_activated", {"reason": reason})

def deactivate_kill_switch():
    """
    Removes the kill switch flag and resumes sniping.
    """
    if os.path.exists(KILL_SWITCH_FILE):
        os.remove(KILL_SWITCH_FILE)
        config["stop_snipe_mode"] = False
        log_event("✅ Emergency Kill Switch Deactivated. Sniping resumed.")
        logging.info("🔓 Emergency stop lifted.")
        log_ai_insight("kill_switch_deactivated")

def is_kill_switch_active() -> bool:
    return os.path.exists(KILL_SWITCH_FILE)

def get_kill_reason() -> str:
    if not is_kill_switch_active():
        return "Not active"
    try:
        with open(KILL_SWITCH_FILE, "r") as f:
            return f.read().strip()
    except:
        return "Unknown"
