# modules/liquidity_monitor.py

import asyncio
import logging
import time
from datetime import datetime, timedelta

import aiohttp
from core.live_config import config
from librarian.data_librarian import librarian
from memory.shared_runtime import shared_memory
from special.insight_logger import log_ai_insight, log_scanner_insight
from strategy.strategy_memory import tag_token_result
from utils.logger import log_event
from utils.rpc_helper import fetch_token_account_info
from utils.rpc_loader import get_active_rpc
from utils.service_status import update_status
from utils.token_utils import is_blacklisted_token

# === Config ===
LP_CHECK_INTERVAL = 90  # seconds
LP_DATA_URL = "https://api.geckoterminal.com/api/v2/networks/solana/pools"
LP_UNLOCK_TAGS = ["unlock", "unlocked", "lp released", "liquidity removed"]
FIREHOSE_LIQUIDITY_REMOVAL_TAG = "liquidity_removed"


class LiquidityMonitor:
    def __init__(self):
        self.seen_pools = set()
        self.lp_risk_tokens = {}

    async def run(self):
        if not config.get("use_lp_filter", True):
            log_event("⚠️ Liquidity monitor disabled by config.")
            return
        log_event("🔐 Liquidity monitor started.")

        while True:
            update_status("liquidity_monitor")
            try:
                await self.check_lp_status()
            except Exception as e:
                logging.error(f"❌ Liquidity monitor error: {e}")
            await asyncio.sleep(LP_CHECK_INTERVAL)

    async def check_lp_status(self):
        cutoff_time = datetime.utcnow() - timedelta(minutes=10)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(LP_DATA_URL) as resp:
                    if resp.status != 200:
                        raise Exception(f"LP fetch failed: {resp.status}")
                    data = await resp.json()
        except Exception as e:
            logging.error(f"⚠️ Error fetching LP data: {e}")
            return

        pools = data.get("data", [])
        for pool in pools:
            try:
                pool_id = pool.get("id")
                if not pool_id or pool_id in self.seen_pools:
                    continue

                self.seen_pools.add(pool_id)
                pool_info = pool.get("attributes", {})
                token_address = pool_info.get("base_token_address") or pool_info.get("token_address")
                if not token_address or is_blacklisted_token(token_address):
                    continue

                last_updated_str = pool_info.get("updated_at")
                if not last_updated_str:
                    continue

                try:
                    last_updated = datetime.fromisoformat(last_updated_str.replace("Z", "+00:00"))
                except Exception:
                    continue

                if last_updated < cutoff_time:
                    continue

                event_text = f"{pool_info.get('name', '')} {pool_info.get('dex_name', '')}".lower()
                if any(tag in event_text for tag in LP_UNLOCK_TAGS):
                    risk_data = {
                        "pool": pool_id,
                        "dex": pool_info.get("dex_name", "unknown"),
                        "updated": last_updated_str
                    }
                    self.lp_risk_tokens[token_address] = risk_data
                    tag_token_result(token_address, "lp_unlocked")
                    log_event(f"🔓 LP Unlock detected for {token_address} ({pool_info.get('name')})")

                    log_scanner_insight(
                        token=token_address,
                        source="liquidity_monitor",
                        sentiment=0.0,
                        volume=0.0,
                        result="lp_unlocked"
                    )

                    try:
                        await librarian.tag_token(token_address, "lp_unlocked", context=risk_data)
                    except Exception as e:
                        logging.debug(f"[LiquidityMonitor] librarian.tag_token failed: {e}")

                    log_ai_insight({
                        "token": token_address,
                        "module": "liquidity_monitor",
                        "tag": "lp_unlocked",
                        "dex": risk_data["dex"],
                        "timestamp": time.time()
                    })

            except Exception as e:
                logging.warning(f"⚠️ LP monitor loop error: {e}")

    def is_lp_risky(self, token: str) -> bool:
        return token in self.lp_risk_tokens

def is_liquidity_removed(token_address: str) -> bool:
    """
    Check if liquidity was removed for this token.
    Primary: Firehose-tagged event.
    Fallback: Solana RPC via LP token account analysis.
    """
    try:
        # === Primary check via Firehose memory ===
        recent_events = shared_memory.get("firehose_events", {}).get(token_address, [])
        for event in recent_events:
            if event.get("tag") == FIREHOSE_LIQUIDITY_REMOVAL_TAG:
                logging.info(f"[LiquidityMonitor] 🧯 Liquidity removed via Firehose tag: {token_address}")
                return True

        # === Fallback check via RPC ===
        rpc = get_active_rpc()
        lp_accounts = shared_memory.get("lp_accounts", {}).get(token_address, [])

        for lp_account in lp_accounts:
            info = fetch_token_account_info(lp_account, rpc)
            if not info or info.get("result", {}).get("value") is None:
                logging.warning(f"[LiquidityMonitor] RPC LP account missing: {lp_account}")
                continue

            balance = int(info["result"]["value"].get("amount", 0))
            if balance == 0:
                logging.info(f"[LiquidityMonitor] RPC confirms LP removed: {lp_account}")
                return True

    except Exception as e:
        logging.error(f"[LiquidityMonitor] Liquidity check failed: {e}")

    return False


async def check_lp_add_event(token_address: str) -> bool:
    """
    Check if the given token has any recent LP add/unlock events.
    Returns True if a risky LP event is detected.
    """
    try:
        monitor = LiquidityMonitor()
        return monitor.is_lp_risky(token_address)
    except Exception as e:
        logging.warning(f"[LiquidityMonitor] check_lp_add_event failed for {token_address}: {e}")
        return False
