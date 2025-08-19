import time
from datetime import datetime, timezone


def get_token_age_minutes(token_context: dict) -> int:
    """
    Returns the number of minutes since LP or token launch.
    Falls back to deploy time if LP not present.
    """
    ts = token_context.get("lp_timestamp") or token_context.get("deploy_timestamp")
    if not ts:
        return 999  # assume old token if no timestamp
    try:
        launch_time = datetime.fromisoformat(ts)
        now = datetime.utcnow()
        delta = now - launch_time
        return int(delta.total_seconds() / 60)
    except Exception:
        return 999

def now_ts() -> float:
    """Returns current UTC timestamp as float."""
    return time.time()

def now_iso() -> str:
    """Returns current UTC time in ISO 8601 format."""
    return datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()

def seconds_ago(iso_time: str) -> float:
    """Returns how many seconds ago a given ISO timestamp was."""
    try:
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return 999999.0
