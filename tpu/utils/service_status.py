import json
import os
import time
from datetime import datetime, timedelta

from utils.logger import log_event

SERVICE_HEARTBEATS = {}
SERVICE_WARNINGS = {}
HEARTBEAT_TIMEOUT = 90  # seconds

STATUS_FILE = "/home/ubuntu/nyx/runtime/logs/service_status.json"
last_run_timestamps = {}

# === Ensure log directory exists ===
os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)

# === Fallback: create/reset malformed file ===
if not os.path.exists(STATUS_FILE):
    with open(STATUS_FILE, "w") as f:
        json.dump({}, f, indent=2)
else:
    try:
        with open(STATUS_FILE, "r") as f:
            json.load(f)
    except json.JSONDecodeError:
        with open(STATUS_FILE, "w") as f:
            json.dump({}, f, indent=2)

# === Update heartbeat + timestamp in memory and disk ===
def update_status(name: str, running: bool = True):
    now = time.time()
    SERVICE_HEARTBEATS[name] = now
    last_run_timestamps[name] = datetime.utcnow()

    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r") as f:
                data = json.load(f)
        else:
            data = {}

        data[name] = {
            "running": running,
            "last_updated": datetime.utcnow().isoformat(),
            "last_heartbeat": now
        }

        with open(STATUS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[ServiceStatus] Failed to update status for {name}: {e}")

# === Generate visual report of service health ===
def get_status_report(timeout_sec: int = 60):
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r") as f:
                data = json.load(f)
        else:
            return {}
    except Exception as e:
        print(f"[ServiceStatus] Failed to load status: {e}")
        return {}

    now = time.time()
    report = {}
    for name, entry in data.items():
        last_heartbeat = entry.get("last_heartbeat", 0)
        running = entry.get("running", False)
        alive = running and (now - last_heartbeat) <= timeout_sec
        report[name] = "🟢" if alive else "🔴"
    return report

# === Get all heartbeat timestamps (disk snapshot) ===
def get_all_status_timestamps():
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r") as f:
                return json.load(f)
        else:
            return {}
    except Exception as e:
        print(f"[ServiceStatus] Failed to load timestamps: {e}")
        return {}

# === In-memory stale module detector ===
def get_missing_services():
    now = time.time()
    return [
        name for name, last in SERVICE_HEARTBEATS.items()
        if now - last > HEARTBEAT_TIMEOUT
    ]

def needs_attention(name: str):
    return name in get_missing_services()

# === Timestamp access ===
def get_last_run_timestamps(since=None):
    if not since:
        return last_run_timestamps
    return {
        name: ts for name, ts in last_run_timestamps.items()
        if ts >= since
    }

# === Optional CLI Test ===
if __name__ == "__main__":
    update_status("test_module")
    print(get_status_report())
