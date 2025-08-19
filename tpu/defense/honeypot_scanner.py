# /honeypot_monitor.py

import asyncio
import logging
from datetime import datetime, timedelta

import aiohttp
from core.live_config import config
from inputs.wallet.wallet_core import WalletManager
from librarian.data_librarian import librarian
from special.insight_logger import log_ai_insight
from strategy.strategy_memory import tag_token_result
from utils.logger import log_event
from utils.service_status import update_status
from utils.token_utils import add_to_blacklist, detect_honeypot

HONEYPOT_CHECK_DELAY = 8  # seconds
CHECK_INTERVAL = 5  # seconds

SOLSCAN_API = "https://public-api.solscan.io/token/{}"

class HoneypotMonitor:
    def __init__(self, wallet: WalletManager, telegram=None):
        self.wallet = wallet
        self.pending_tokens = {}  # token_address -> timestamp
        self.tg = telegram

    def add_token(self, token_address: str):
        if not isinstance(token_address, str) or not token_address.strip():
            logging.warning(f"⚠️ Invalid token address: {token_address}")
            return
        now = datetime.utcnow()
        self.pending_tokens[token_address] = now
        logging.info(f"🧪 Queued token for honeypot check: {token_address}")

    async def run(self):
        logging.info("🕵️ HoneypotMonitor started.")
        while True:
            try:
                update_status("honeypot_monitor")
                now = datetime.utcnow()

                to_check = [
                    token for token, ts in self.pending_tokens.items()
                    if (now - ts) > timedelta(seconds=HONEYPOT_CHECK_DELAY)
                ]

                for token in to_check:
                    try:
                        is_hp = detect_honeypot(token, self.wallet.keypair, config)
                        if is_hp:
                            log_event(f"💀 Honeypot detected: {token}")
                            tag_token_result(token, "honeypot")
                            add_to_blacklist(token)
                            await librarian.tag_token(token, "honeypot")
                            log_ai_insight("honeypot_detected", {"token": token})

                            if self.tg:
                                await self.tg.send_message(
                                    f"🚨 *Honeypot Detected!*\nToken: `{token}` has been blacklisted.",
                                    parse_mode="Markdown"
                                )
                        else:
                            log_event(f"✅ Token is not a honeypot: {token}")
                            await librarian.tag_token(token, "clean")

                    except Exception as e:
                        logging.error(f"❌ Error checking honeypot for {token}: {e}")

                    finally:
                        self.pending_tokens.pop(token, None)

            except Exception as loop_err:
                logging.error(f"⚠️ HoneypotMonitor loop error: {loop_err}")

            await asyncio.sleep(CHECK_INTERVAL)


async def is_honeypot(token_address: str) -> bool:
    """
    Uses Solscan API to check if a token has suspicious sell restrictions or trap behavior.
    This is a basic heuristic, not guaranteed to catch all honeypots.
    """
    try:
        headers = {
            "accept": "application/json"
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(SOLSCAN_API.format(token_address), headers=headers) as resp:
                if resp.status != 200:
                    logging.warning(f"[HoneypotScanner] Solscan query failed: {resp.status}")
                    return False
                data = await resp.json()

        # Heuristic: no LP info or no supply = suspect
        lp_data = data.get("lp_holders", [])
        supply = data.get("tokenAmount", {}).get("totalSupply", 0)
        is_flagged = len(lp_data) == 0 or supply == 0

        if is_flagged:
            logging.info(f"[HoneypotScanner] Honeypot suspected: {token_address}")
        return is_flagged

    except Exception as e:
        logging.warning(f"[HoneypotScanner] Exception: {e}")
        return False
