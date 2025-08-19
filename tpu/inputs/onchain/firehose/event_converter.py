# /firehose/event_converter.py

import logging
from typing import Dict, List

NFT_KEYWORDS = {"nft", "mint", "collection", "token", "metadata"}

def classify_event(logs: List[str]) -> str:
    """
    Classifies an event based on transaction logs.
    """
    logs_str = " ".join(logs).lower()

    if any(keyword in logs_str for keyword in NFT_KEYWORDS):
        return "nft_mint"
    elif "initialize" in logs_str and "lp" in logs_str:
        return "lp_create"
    elif "swap" in logs_str:
        return "swap"
    elif "burn" in logs_str:
        return "burn"
    elif "transfer" in logs_str:
        return "transfer"
    return "unknown"


def extract_token_address(instructions: List[dict]) -> str:
    """
    Attempts to extract a token/mint address from transaction instructions.
    """
    for ix in instructions:
        # Look for keys likely holding token/mint addresses
        for key in ("mint", "token", "address", "account", "programId"):
            addr = ix.get(key)
            if addr and isinstance(addr, str) and len(addr) > 30:
                return addr
    return ""


def convert_block_to_events(block_data: dict) -> List[Dict]:
    """
    Converts a raw Firehose block into structured events for the AI pipeline.
    """
    events = []
    try:
        slot = block_data.get("slot", 0)
        for tx in block_data.get("transactions", []):
            tx_hash = tx.get("tx_hash")
            logs = tx.get("logs", [])
            instructions = tx.get("instructions", [])

            event_type = classify_event(logs)
            token_address = extract_token_address(instructions)

            event = {
                "tx_hash": tx_hash,
                "slot": slot,
                "logs": logs,
                "event_type": event_type,
                "token_address": token_address,
                "description": " ".join(logs).lower(),
                "programs": instructions,
                "origin": "firehose"
            }
            events.append(event)

    except Exception as e:
        logging.error(f"[EventConverter] Failed converting block to events: {e}")

    return events
