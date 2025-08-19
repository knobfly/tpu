import asyncio
import logging
from datetime import datetime, timedelta

from core.live_config import config
from exec.auto_sell_logic import evaluate_early_exit
from exec.trade_executor import TradeExecutor
from inputs.wallet.wallet_core import WalletManager
from strategy.strategy_memory import record_result, tag_token_result
from utils.price_fetcher import get_token_price
from utils.service_status import update_status
from utils.telegram_utils import send_telegram_message
from utils.token_utils import add_to_blacklist, is_blacklisted_token, is_dust_value
from utils.tx_utils import confirm_token_sell

# === Feeding Frenzy Constants ===
FRENZY_DURATION_SECONDS = 120
SELL_AFTER_SECONDS = 10
FRENZY_BUY_AMOUNT_SOL = 0.1
MAX_TOKENS_PER_FRENZY = 8
FRENZY_REBUY_PERCENT = 0.5
RUG_LIMIT = 2
FRENZY_TRIGGER_COUNT = 3
FRENZY_ACTIVATION_WINDOW = 60
FRENZY_STARTUP_DELAY = 90  # seconds to wait after startup before AI can trigger

# === Shared State ===
frenzy_active = False
frenzy_start_time = None
frenzy_cooldown_until = None
auto_frenzy_enabled = True
frenzy_token_list = []
frenzy_stats = {"wins": 0, "rugs": 0, "losses": 0}
launch_timestamps = []
frenzy_task = None
startup_block_until = datetime.utcnow() + timedelta(seconds=FRENZY_STARTUP_DELAY)


class FeedingFrenzy:
    def __init__(self, wallet: WalletManager, engine, telegram=None):
        self.wallet = wallet
        self.engine = engine
        self.tg = telegram  # Optional
        self.executor: TradeExecutor = engine.executor
        update_status("feeding_frenzy")

    def is_ready(self):
        now = datetime.utcnow()
        if frenzy_active:
            return False
        if now < startup_block_until:
            return False
        if frenzy_cooldown_until and now < frenzy_cooldown_until:
            return False
        return True

    def record_launch(self) -> int:
        now = datetime.utcnow()
        launch_timestamps.append(now)
        window_start = now - timedelta(seconds=FRENZY_ACTIVATION_WINDOW)
        recent = [t for t in launch_timestamps if t > window_start]
        return len(recent)

    async def maybe_trigger_frenzy(self):
        if not self.is_ready():
            return
        if auto_frenzy_enabled and self.record_launch() >= FRENZY_TRIGGER_COUNT:
            await self.start_frenzy()

    async def start_frenzy(self):
        global frenzy_active, frenzy_start_time, frenzy_stats, frenzy_token_list, frenzy_task
        if frenzy_active:
            return
        frenzy_active = True
        frenzy_start_time = datetime.utcnow()
        frenzy_stats = {"wins": 0, "rugs": 0, "losses": 0}
        frenzy_token_list = []

        logging.info("🔥 Feeding Frenzy started!")
        await send_telegram_message("🔥 Feeding Frenzy started! Bot is sniping aggressively.")

        frenzy_task = asyncio.create_task(self._frenzy_loop())

    async def _frenzy_loop(self):
        global frenzy_active, frenzy_cooldown_until
        end_time = datetime.utcnow() + timedelta(seconds=FRENZY_DURATION_SECONDS)
        while datetime.utcnow() < end_time and frenzy_active:
            await asyncio.sleep(1)
        await self._end_frenzy()

    async def handle_token(self, token_address: str):
        if not frenzy_active:
            return
        if is_blacklisted_token(token_address) or is_dust_value(token_address):
            logging.info(f"⛔ Skipping blacklisted or dust token: {token_address}")
            return

        tag = self.engine.token_tags.get(token_address)
        if tag in ("rug", "dead"):
            logging.info(f"⚠️ Skipping tagged token {token_address}: {tag}")
            return

        if len(frenzy_token_list) >= MAX_TOKENS_PER_FRENZY:
            return

        frenzy_token_list.append(token_address)

        tx = await self.executor.buy_token(token_address, FRENZY_BUY_AMOUNT_SOL, wallet=self.wallet)
        if not tx:
            logging.warning(f"❌ Buy failed during frenzy: {token_address}")
            return

        await asyncio.sleep(SELL_AFTER_SECONDS)

        should_exit = await evaluate_early_exit(token_address, self.wallet, config)
        if should_exit:
            await self._exit_token(token_address, early=True)
        else:
            await self._exit_token(token_address)

        if frenzy_stats["rugs"] >= RUG_LIMIT:
            await send_telegram_message("❌ Too many rugs detected. Stopping Feeding Frenzy.")
            await self._end_frenzy()

    async def _exit_token(self, token_address: str, early=False):
        result = await self.executor.sell_token(token_address, wallet=self.wallet)
        if not result:
            frenzy_stats["rugs"] += 1
            tag_token_result(token_address, "rug")
            add_to_blacklist(token_address)
            record_result(token_address, "rug", 0, token_type="frenzy", scanner="frenzy")
            return

        confirmed = await confirm_token_sell(self.wallet.rpc, result)
        if confirmed:
            frenzy_stats["wins"] += 1
            tag_token_result(token_address, "win")
            record_result(token_address, "win", 100, token_type="frenzy", scanner="frenzy")
        else:
            frenzy_stats["losses"] += 1
            tag_token_result(token_address, "loss")
            record_result(token_address, "loss", 5, token_type="frenzy", scanner="frenzy")

    async def _end_frenzy(self):
        global frenzy_active, frenzy_cooldown_until, frenzy_task
        frenzy_active = False
        frenzy_cooldown_until = datetime.utcnow() + timedelta(hours=1)
        frenzy_task = None

        summary = (
            f"📊 Feeding Frenzy ended.\n"
            f"🏆 Wins: {frenzy_stats['wins']}\n"
            f"💀 Rugs: {frenzy_stats['rugs']}\n"
            f"📉 Losses: {frenzy_stats['losses']}"
        )
        logging.info(summary)
        await send_telegram_message(summary)

    async def run(self):
        # NOTE: We never trigger frenzy directly from run()
        # Let AI or maybe_trigger_frenzy handle it safely
        pass


# === Global Helpers ===
def is_frenzy_active() -> bool:
    return frenzy_active

def toggle_auto_frenzy() -> bool:
    global auto_frenzy_enabled
    auto_frenzy_enabled = not auto_frenzy_enabled
    return auto_frenzy_enabled

def is_frenzy_ready() -> bool:
    now = datetime.utcnow()
    if frenzy_active:
        return False
    if frenzy_cooldown_until and now < frenzy_cooldown_until:
        return False
    return True

async def start_feeding_frenzy():
    global frenzy_active, frenzy_start_time
    if frenzy_active:
        return
    frenzy_start_time = datetime.utcnow()
    frenzy_active = True
