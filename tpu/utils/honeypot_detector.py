import logging
from typing import Any, Dict
from core.live_config import config as live_config
from solana.keypair import Keypair
from solana.rpc.api import Client
from utils.token_utils import add_to_blacklist, build_sell_instruction, is_dust_value
from utils.tx_utils import simulate_transaction


def check_if_honeypot(token_address: str, client: Client, wallet: Keypair, estimated_value: float = 0.0) -> bool:
    """
    Simulates a sell TX to detect honeypots.
    Returns True if token is likely a honeypot.
    """
    try:
        if not isinstance(token_address, str) or not token_address.strip():
            logging.warning(f"❌ Invalid token address: {token_address}")
            return True

        if is_dust_value(estimated_value):
            logging.warning(f"💨 Token {token_address} is considered dust — skipping honeypot check.")
            return True

        sell_ix = build_sell_instruction(token_address, wallet.public_key, 0.00001, live_config)
        if not sell_ix:
            logging.warning(f"🚨 Honeypot check failed: could not build sell instruction for {token_address}")
            add_to_blacklist(token_address)
            return True

        result = simulate_transaction(
            client,
            instructions=[sell_ix],
            wallet=wallet,
            boost=live_config.get("use_compute_unit_boost", False)
        )

        if not isinstance(result, dict):
            logging.warning(f"⚠️ Invalid simulation result format for {token_address}: {result}")
            add_to_blacklist(token_address)
            return True

        if result.get("err"):
            logging.warning(f"📛 Honeypot suspected: simulation error: {result['err']}")
            add_to_blacklist(token_address)
            return True

        logs = result.get("logs", [])
        if any("error" in line.lower() or "denied" in line.lower() for line in logs):
            suspicious = next((line for line in logs if "error" in line.lower() or "denied" in line.lower()), "unknown")
            logging.warning(f"🢨 Honeypot suspected from logs: {suspicious}")
            add_to_blacklist(token_address)
            return True

        logging.info(f"✅ Token {token_address} passed honeypot check.")
        return False

    except Exception as e:
        logging.error(f"💥 Honeypot detection failed for {token_address}: {e}")
        add_to_blacklist(token_address)
        return True
