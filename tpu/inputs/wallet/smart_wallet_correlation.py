import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from core.live_config import config
from cortex.wallet_data import get_tracked_wallets
from librarian.data_librarian import librarian
from scoring.scoring_engine import score_token
from special.insight_logger import log_scanner_insight
from utils.logger import log_event
from utils.service_status import update_status
from utils.wallet_tracker import get_wallet_performance_score, get_wallet_trade_history

CHECK_INTERVAL = 120  # every 2 minutes
MIN_WALLET_REP = 60
CORRELATION_BOOST = 0.7
HISTORY_WINDOW_MINUTES = 60

# In-memory cache to avoid re-processing same patterns
_last_seen = defaultdict(lambda: 0)

async def process_wallet_correlations():
    now = datetime.utcnow()
    correlated_tokens = defaultdict(list)

    all_wallets = get_tracked_wallets()  # Smart wallets only
    for wallet in all_wallets:
        if get_wallet_performance_score(wallet) < MIN_WALLET_REP:
            continue
        trades = get_wallet_trade_history(wallet, window_minutes=HISTORY_WINDOW_MINUTES)
        seen_tokens = set()
        for tx in trades:
            token = tx.get("token")
            if not token or token in seen_tokens:
                continue
            seen_tokens.add(token)
            correlated_tokens[token].append(wallet)

    for token, wallets in correlated_tokens.items():
        if len(wallets) < 2:
            continue
        if _last_seen[token] and (now.timestamp() - _last_seen[token]) < CHECK_INTERVAL:
            continue

        _last_seen[token] = now.timestamp()

        metadata = {
            "wallets": wallets,
            "correlated_wallets": len(wallets),
            "source": "wallet_correlation"
        }

        try:
            score = await score_token(token, metadata)
        except Exception as e:
            logging.warning(f"[WalletCorrelation] ⚠️ Score error: {e}")
            continue

        score += CORRELATION_BOOST
        confidence = librarian.estimate_confidence(token, metadata, score)

        log_event(f"🔗 Wallet Correlation: {token} seen by {len(wallets)} smart wallets → Score: {score:.2f} | AI: {confidence:.2f}")
        log_scanner_insight(
            token=token,
            source="wallet_correlation",
            sentiment=metadata.get("sentiment", 0),
            volume=metadata.get("volume", 0),
            result="wallet_overlap"
        )

        if confidence >= config.get("wallet_correlation_threshold", 6.5):
            librarian.mark_source(token, "wallet_correlation")
            await librarian.queue_autobuy(token, metadata, score, source="wallet_correlation")

async def run_smart_wallet_correlation():
    log_event("🔗 Smart Wallet Correlation Scanner started.")
    update_status("smart_wallet_correlation")

    while True:
        try:
            await process_wallet_correlations()
        except Exception as e:
            logging.warning(f"[WalletCorrelation] Error: {e}")
        await asyncio.sleep(CHECK_INTERVAL)
