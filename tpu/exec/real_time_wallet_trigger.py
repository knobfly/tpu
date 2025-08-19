import asyncio
import logging
import time
from datetime import datetime

from inputs.meta_data.token_metadata_fetcher import fetch_token_metadata
from inputs.wallet.wallet_core import WalletManager
from special.insight_logger import log_scanner_insight
from utils.logger import log_event
from utils.service_status import update_status
from utils.wallet_helpers import get_recent_buys
from utils.wallet_tracker import get_wallet_tracker_score

# === Config ===
TRUST_SCORE_THRESHOLD = 60
SNIPE_CONFIDENCE_THRESHOLD = 6.8  # we map 0..100 score -> 0..10 confidence
TRIGGER_COOLDOWN_SEC = 60         # per token
SEEN_TRIGGERS: set[str] = set()
_token_cooldowns: dict[str, float] = {}


def _now() -> float:
    return time.time()


async def _maybe_await(val):
    """Await if coroutine/awaitable; otherwise return the value as-is."""
    if asyncio.iscoroutine(val):
        return await val
    return val


# === Real-time streaming trigger ===
async def handle_wallet_trade_event(event: dict, wallet: WalletManager):
    """
    Reacts instantly when a known wallet buys or sells in live webhook or listener mode.
    """
    from scoring.snipe_score_engine import evaluate_snipe

    update_status("real_time_wallet_trigger")

    try:
        token = event.get("token")
        action = event.get("action")  # 'buy' or 'sell'
        wallet_address = event.get("wallet")
        tx_sig = event.get("signature", "")
        amount = event.get("amount", 0)

        if not token:
            return

        # basic per-token debounce so we don't spam on rapid events
        last = _token_cooldowns.get(token, 0.0)
        if _now() - last < TRIGGER_COOLDOWN_SEC:
            return
        _token_cooldowns[token] = _now()

        metadata = await fetch_token_metadata(token)
        name = metadata.get("name", "Unknown")

        log_event(
            f"🚨 Wallet {str(action).upper()}: {str(wallet_address)[:6]}... → {token} ({name}) | Amount: {amount}"
        )
        log_scanner_insight(
            token=token,
            source="wallet_trigger",
            sentiment=0,
            volume=amount,
            result="wallet_event",
            tags=[f"wallet:{wallet_address}", f"tx:{tx_sig}"],
        )

        # scoring expects a context dict; derive a 0..10 “confidence” from final_score/10
        ctx = {"token_address": token, "scanner_source": "wallet_trigger"}
        snipe_result = await evaluate_snipe(ctx)
        final_score = float(snipe_result.get("final_score", snipe_result.get("score", 0.0)))
        confidence = final_score / 10.0  # keep your 0..10 threshold semantics

        if confidence >= SNIPE_CONFIDENCE_THRESHOLD:
            log_event(f"🤖 Auto-buy triggered: {token} | Score: {final_score:.2f}")
            # WalletManager.maybe_buy is async in your codebase
            await wallet.maybe_buy(token, reason="wallet_trigger", confidence=confidence)

    except Exception as e:
        logging.warning(f"[WalletTrigger] Error handling event: {e}")


# === Loop scanner for recent snipes ===
async def scan_realtime_buys():
    """
    Scans the last few seconds for new buys by known trusted wallets and evaluates trigger worthiness.
    """
    from scoring.snipe_score_engine import evaluate_snipe
    from exec.trade_executor import TradeExecutor

    global _token_cooldowns
    update_status("real_time_wallet_trigger")

    try:
        # get_recent_buys may be sync (list) or async; support both
        recent = await _maybe_await(get_recent_buys(since_seconds=15))
        now_ts = _now()

        # expected shape: iterable of (wallet, token)
        for wallet_addr, token in (recent or []):
            trust = float(get_wallet_tracker_score(wallet_addr))
            if trust < TRUST_SCORE_THRESHOLD:
                continue
            if token in _token_cooldowns and now_ts - _token_cooldowns[token] < TRIGGER_COOLDOWN_SEC:
                continue

            ctx = {"token_address": token, "scanner_source": "realtime_wallet"}
            snipe_result = await evaluate_snipe(ctx)
            final_score = float(snipe_result.get("final_score", snipe_result.get("score", 0.0)))
            confidence = final_score / 10.0  # 0..10 scale

            log_event(
                f"⚡ Trusted wallet {str(wallet_addr)[:6]}... sniped {token} "
                f"| Trust={trust:.0f} | AI={confidence:.2f} (score={final_score:.1f})"
            )
            log_scanner_insight(
                token=token,
                source="realtime_wallet",
                sentiment=confidence,
                volume=0,
                result="realtime_wallet_trigger",
                tags=[
                    f"wallet:{wallet_addr}",
                    f"trust:{trust:.0f}",
                    f"score:{final_score:.2f}",
                    f"time:{datetime.utcnow().isoformat()}",
                ],
            )

            if confidence >= SNIPE_CONFIDENCE_THRESHOLD:
                # TradeExecutor.buy_token appears to be sync in your snippet; keep as-is
                TradeExecutor.buy_token(
                    token=token,
                    score=final_score,
                    source="realtime_wallet",
                    metadata={"wallet": wallet_addr},
                )
                _token_cooldowns[token] = now_ts

    except Exception as e:
        logging.warning(f"[RealTimeWalletTrigger] Scan error: {e}")


# === Main runtime loop ===
async def run_real_time_wallet_trigger():
    log_event("⚡ Real-Time Wallet Trigger loop started.")
    while True:
        await scan_realtime_buys()
        await asyncio.sleep(5)


# alias retained for other modules
detect_sniper_behavior = handle_wallet_trade_event
