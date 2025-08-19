import asyncio
import logging
import time
from collections import defaultdict
from typing import Optional

from core.live_config import config
from cortex.txn_cortex import evaluate_junk_confidence
from exec.trade_executor import TradeExecutor
from inputs.wallet.multi_wallet_manager import MultiWalletManager
from librarian.data_librarian import librarian
from scoring.scoring_engine import score_token
from special.insight_logger import log_scanner_insight
from utils.logger import log_event
from utils.service_status import update_status
from utils.token_utils import get_token_balance, get_token_metadata

# === Cleanup Settings ===
MIN_HOLD_SECONDS = 600             # Minimum 10 min hold before cleaning
JUNK_HOLDER_THRESHOLD = 7          # If holders < this, it's likely junk
JUNK_BALANCE_THRESHOLD = 0.001     # If value is below this, it's clutter
AUTOSELL_CONFIDENCE_MAX = 3.5      # Sell if AI confidence never goes above this
SEEN_TOKENS = {}                   # token → first seen timestamp

# === Initialize Executor ===
trade_executor: TradeExecutor | None = None
multi_wallet_manager = MultiWalletManager()

async def run_junk_token_cleaner():
    global trade_executor
    if trade_executor is None:
        trade_executor = TradeExecutor()  # now created with a running loop
    log_event("🧹 Junk Token Cleaner started.")
    while True:
        try:
            update_status("junk_token_cleaner")
            await clean_wallet_junk()
        except Exception as e:
            logging.warning(f"[JunkCleaner] Error: {e}")
        await asyncio.sleep(120)

async def clean_wallet_junk():
    try:
        wallet_address = config.get("wallet_address")
        wallet = multi_wallet_manager.get_wallet_by_address(wallet_address)
        tokens = librarian.get_tokens_in_wallet(wallet_address)

        for token in tokens:
            now_ts = time.time()

            if token not in SEEN_TOKENS:
                SEEN_TOKENS[token] = now_ts
                continue

            if now_ts - SEEN_TOKENS[token] < MIN_HOLD_SECONDS:
                continue

            balance = await get_token_balance(wallet_address, token)
            if not balance or balance < JUNK_BALANCE_THRESHOLD:
                continue

            metadata = await get_token_metadata(token) or {}
            holders = int(metadata.get("holders", 0) or 0)

            # score_token expects a ctx dict and returns sync dict with final_score (0..100)
            try:
                ctx = {"token_address": token, "mode": "trade", "scanner_source": "junk_cleaner"}
                score_result = score_token(ctx) or {}
                confidence = float(score_result.get("final_score", 0.0))
                raw_score = confidence
            except Exception:
                confidence = 0.0
                raw_score = 0.0

            if holders < JUNK_HOLDER_THRESHOLD or confidence < AUTOSELL_CONFIDENCE_MAX:
                log_event(f"🗑 JunkCleaner: Selling {token} | Holders={holders} | Conf={confidence:.2f}")
                log_scanner_insight(
                    token=token,
                    source="junk_cleaner",
                    sentiment=confidence,
                    volume=holders,
                    result="junk_flagged",
                    tags=[f"balance:{balance}"]
                )

                librarian.record_trade_feedback(token, "junk_sell", score=raw_score, source="junk_cleaner")

                # use the lazily-created executor
                global trade_executor
                if trade_executor is None:
                    trade_executor = TradeExecutor()
                await trade_executor.sell_token(token, amount_tokens=balance, wallet=wallet)

    except Exception as e:
        logging.warning(f"[JunkCleaner] Wallet loop error: {e}")
