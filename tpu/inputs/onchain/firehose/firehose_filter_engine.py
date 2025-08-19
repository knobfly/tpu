# firehose/firehose_filter_engine.py

import logging

from inputs.meta_data.token_metadata import is_dust_token
from strategy.strategy_memory import is_blacklisted_token


def filter_event(event: dict) -> bool:
    try:
        # Check blacklist or dust
        if is_blacklisted_token(event.get("tx_hash")):
            return False

        if is_dust_token(event.get("tx_hash")):
            return False

        # Basic sanity checks
        if not event.get("tx_hash") or len(event.get("logs", [])) == 0:
            return False

        # Only pass known events for now
        if event.get("event_type") in ["lp_create", "swap", "mint"]:
            return True

        return False  # Reject anything else by default

    except Exception as e:
        logging.warning(f"[FirehoseFilter] Failed filter logic: {e}")
        return False
