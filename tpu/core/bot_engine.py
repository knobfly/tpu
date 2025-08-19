
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict

from core.live_config import config
from exec.auto_sell_logic import AutoSellLogic
from exec.feeding_frenzy import FeedingFrenzy
from exec.token_reentry_monitor import monitor_token_reentry_loop
from exec.trade_executor import TradeExecutor
from inputs.meta_data.meta_strategy_engine import meta_strategy_engine
from inputs.onchain.onchain_listener import OnchainListener
from inputs.wallet.wallet_core import WalletManager
from memory.trade_history import post_trade_strategy_feedback
from scoring.scoring_engine import score_token
from solana.rpc.async_api import AsyncClient
from special.adaptive_trade_controller import adaptive_controller
from special.insight_logger import log_scanner_insight
from strategy.stop_snipe_defender import is_snipe_blocked
from utils.dexscreener_adapter import get_dexscreener_summary
from utils.keyword_engine import evaluate_keywords
from utils.logger import log_event
from utils.token_utils import get_token_metadata, is_blacklisted_token, is_dust_value

# Optional injection
ai_brain = None

class BotEngine:
    def __init__(self, wallet: WalletManager, rpc: AsyncClient):
        self.wallet = wallet
        self.rpc = rpc
        self.session = None
        self.last_heartbeat = datetime.utcnow()
        self.active_tokens = {}  # token: buy_time
        self.logger = log_event
        self.fallback_triggered = False

        # Core modules
        self.auto_sell = AutoSellLogic(wallet)
        self.executor = TradeExecutor()  # Uses wallet internally
        self.feeding_frenzy = FeedingFrenzy(wallet=wallet, engine=self, telegram=None)

        # On-chain listener
        self.listener = OnchainListener(
            wallet=wallet,
            telegram=None,
            trade_executor=self.executor,
            auto_sell=self.auto_sell,
            logger=self.logger,
            feeding_frenzy=self.feeding_frenzy
        )

    def inject_telegram(self, tg):
        self.listener.telegram = tg
        self.feeding_frenzy.telegram = tg

    def inject_ai(self, ai):
        global ai_brain
        ai_brain = ai

    async def run(self):
        log_event("🎯 Bot engine started.")
        asyncio.create_task(self._heartbeat_loop())

        from utils.service_status import update_status
        while True:
            update_status("bot_engine")  # ✅ Heartbeat tag
            await asyncio.sleep(10)

    async def start_reflex_watch(wallet):
        price_feed = your_price_function  # existing price fetcher
        volume_feed = your_volume_function
        for token in await wallet.get_tokens():
            asyncio.create_task(adaptive_controller().watch_token(token["mint"], price_feed, volume_feed))

    def current_meta_strategy():
        return meta_strategy_engine().current_strategy()

    async def _heartbeat_loop(self):
        while True:
            self.last_heartbeat = datetime.utcnow()
            await asyncio.sleep(600)  # 10 mins

    def get_active_token_count(self) -> int:
        return len(self.active_tokens)

    def remove_token(self, address: str):
        self.active_tokens.pop(address, None)

    def should_snipe_token(self, token_data: Dict[str, Any]) -> bool:
        address = token_data.get("address")
        if not address:
            return False

        if is_blacklisted_token(address):
            log_event(f"🚫 Token {address} is blacklisted. Skipping.")
            return False

        if is_dust_value(token_data):
            log_event(f"🧊 Token {address} is dust. Skipping.")
            return False

        if is_snipe_blocked():
            log_event("🛑 Snipe blocked due to rug wave defender.")
            return False

        # === NEW: Heuristic Rug Evaluation ===
        from defense.rug_wave_defender import evaluate_token_for_rug  # 🔥 Injected here
        if evaluate_token_for_rug(token_data):
            log_event(f"⚠️ Rug-risk token {address} detected. Skipping.")
            return False

        # === PHASE 1: SNIPE SCORING ===
        snipe = score_token(address, token_data, mode="snipe")
        snipe_score = snipe["score"]
        token_data["score"] = snipe_score
        token_data["snipe_breakdown"] = snipe["breakdown"]
        log_scanner_insight("bot_engine", f"🥷 Snipe Score for {address}: {snipe_score:.2f}")

        if snipe_score < config.get("min_snipe_score", 30):
            log_event(f"⚖️ Snipe score too low ({snipe_score:.2f}) → Skip {address}")
            return False

        # === Volume & LP Check (Dexscreener) ===
        dex = get_dexscreener_summary(address)
        volume = dex.get("volume_5m", 0)
        liquidity = dex.get("liquidity", 0)
        token_data["dex_liquidity"] = liquidity

        token_data["dex_volume"] = volume
        token_data["dex_liquidity"] = liquidity

        if volume < 5000 or liquidity < 500:
            log_event(f"🧪 Skipping {address} — Weak Dexscreener: Volume {volume}, LP {liquidity}")
            return False

        return True  # ✅ PASS: token is worthy of sniper bite

    async def process_token_signal(self, token_data: Dict[str, Any]):
        if not self.should_snipe_token(token_data):
            return

        try:
            await self.executor.buy_token(token_data)
            self.active_tokens[token_data["address"]] = datetime.utcnow()
            log_event(f"✅ Sniped token {token_data['address']}")

            # Launch recheck loop after snipe
            asyncio.create_task(monitor_token_reentry_loop(token_data, self.executor))

        except Exception as e:
            log_event(f"❌ Failed to buy or rotate {token_data.get('address')}: {e}")


