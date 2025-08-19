import asyncio
import logging
from datetime import datetime

from cortex.txn_cortex import register_buy
from defense.honeypot_scanner import is_honeypot
from defense.liquidity_monitor import LiquidityMonitor
from defense.rug_wave_defender import evaluate_token_for_rug
from inputs.meta_data.token_metadata import is_dust_token, parse_token_metadata
from inputs.social.group_reputation import get_group_score, update_group_score
from memory.token_memory_index import token_memory_index
from scoring.snipe_score_engine import evaluate_snipe
from special.insight_logger import log_ai_insight
from strategy.strategy_memory import get_meta_keywords, is_blacklisted_token, tag_token_result
from utils.logger import log_event
from utils.telegram_utils import send_telegram_message
from utils.wallet_tracker import get_wallet_token_activity

# one shared executor is fine if you already instantiate it in main and inject; if not, create lightweight here:

FAST_PROBE_SOL = 0.02     # small probe for borderline cases
FULL_SIZE_SOL = 0.08      # bump as you like
HARD_SCORE = 0.88         # instant full send
SOFT_SCORE = 0.78         # probe first

async def classify_stream_event(event: dict):
    from exec.trade_executor import TradeExecutor
    token_list = event.get("tokens", [])
    wallet_list = event.get("wallets", [])
    logs = event.get("logs", [])

    triggered = []

    for token in token_list:
        if not token or token in ["So11111111111111111111111111111111111111112"]:
            continue

        try:
            if is_blacklisted_token(token):
                tag_token_result(token, "blacklisted")
                token_memory_index.record(token, "risk_flag", "blacklisted")

            if evaluate_token_for_rug(token):
                tag_token_result(token, "rug_pattern")
                token_memory_index.record(token, "risk_flag", "rug")

            if is_honeypot(token):
                tag_token_result(token, "honeypot")
                token_memory_index.record(token, "risk_flag", "honeypot")

            if LiquidityMonitor.check_lp_status(token):
                token_memory_index.record(token, "lp_status", "unlocked")
                tag_token_result(token, "unlocked_lp")
        except Exception as e:
            log_event(f"[StreamFilter] Risk filter failed for {token}: {e}")

        if is_dust_token(token):
            triggered.append(f"🪙 Dust token: {token}")
            continue

        try:
            keywords = get_meta_keywords(token)
            if keywords:
                token_memory_index.record(token, "keyword_overlap", keywords)

            group_score = get_group_score(token)
            if group_score:
                token_memory_index.record(token, "group_reputation", group_score)
                if group_score > 0.75:
                    log_ai_insight(token, "group_alpha", "Highly active token in group radar", meta={"group_score": group_score})
        except Exception as e:
            log_event(f"[StreamSocial] Social overlap failed for {token}: {e}")

        try:
            meta = await parse_token_metadata(token)
            if meta:
                tag_token_result(token, meta)
                token_memory_index.record(token, "meta_tags", meta)
                log_ai_insight(token, "stream_event", "Observed in raw stream", meta=meta)
                if meta and meta.get("new_launch"):
                    asyncio.create_task(fast_snipe_router(token, source="stream", logs=logs))
        except Exception as e:
            log_event(f"[Metadata] Failed to parse metadata for {token}: {e}")

        try:
            snapshot = {
                "token": token,
                "time": datetime.utcnow().isoformat(),
                "stream_source": "solana-mainnet",
                "tagged_meta": token_memory_index.get(token, "meta_tags", []),
                "tagged_risk": token_memory_index.get(token, "risk_flag", None),
                "group_links": token_memory_index.get(token, "group_links", []),
            }

            log_event(f"[StreamCatalog] 📦 Snapshot: {snapshot}")

            if snapshot["tagged_meta"] and "alpha" in snapshot["tagged_meta"]:
                await send_telegram_message(f"🚀 Alpha Signal: {token}\nTags: {snapshot['tagged_meta']}")

            if snapshot["tagged_risk"] == "rug":
                await send_telegram_message(f"⚠️ Rug detected in stream: {token}")

            if "alpha" in snapshot["tagged_meta"] and snapshot["tagged_risk"] is None:
                try:
                    tx_hash = await TradeExecutor.buy_token(token)
                    if tx_hash:
                        await register_buy(token, tx_hash)
                except Exception as e:
                    log_event(f"[AutoSnipe-Alpha] Buy failed for {token}: {e}")

        except Exception as e:
            log_event(f"[StreamLogger] Snapshot failed for {token}: {e}")

    for wallet in wallet_list:
        try:
            summary = await get_wallet_token_activity(wallet)
            if summary.get("wallet_score", 0) > 0.9:
                triggered.append(f"🐋 Whale active: {wallet}")
                token_memory_index.record(wallet, "wallet_rank", "whale")

            tokens_touched = summary.get("recent_tokens", [])
            for touched_token in tokens_touched:
                if is_blacklisted_token(touched_token):
                    continue
                score_result = await evaluate_snipe(touched_token)
                if score_result.get("final_score", 0) >= 0.88:
                    buy_txn = await TradeExecutor.buy_token(touched_token)
                    if buy_txn:
                        await register_buy(touched_token, buy_txn, score_result, source="stream")
                        triggered.append(f"💥 Sniped {touched_token} from whale trigger!")
        except Exception as e:
            log_event(f"[AutoSnipeWallet] Eval error for {wallet}: {e}")

    for line in logs:
        if "mint" in line.lower():
            triggered.append("🧬 Mint detected")
        if "initialize" in line.lower():
            triggered.append("🛠️ Init event")
        if "buy" in line.lower():
            triggered.append("🟢 Possible buy")

    for note in triggered:
        log_ai_insight("stream", note)
        log_event(f"[StreamClassifier] {note}")

async def fast_snipe_router(token: str, *, source: str, logs: list[str]|None=None):
    from exec.trade_executor import TradeExecutor
    _trade_exec = TradeExecutor()
    """Ultra-low-latency path for new mints/pools/liquidity events."""
    # cheap parallel gates
    ok, flags = await _quick_risk(token)
    if not ok:
        log_event(f"[FastSnipe] Blocked {token}: {flags}")
        return None

    # single score call
    try:
        score_res = await evaluate_snipe(token)
        score = float(score_res.get("final_score", 0))
    except Exception as e:
        log_event(f"[FastSnipe] score failed for {token}: {e}")
        return None

    # decide size
    amt = FULL_SIZE_SOL if score >= HARD_SCORE else (FAST_PROBE_SOL if score >= SOFT_SCORE else 0.0)
    if amt <= 0:
        log_event(f"[FastSnipe] score too low {score:.2f} for {token}")
        return None

    try:
        tx = await _trade_exec.buy_token(token, amt, override_filters=False, scanner_source=source)
        if tx:
            log_event(f"[FastSnipe] ✅ bought {token} @ {amt} SOL (score={score:.2f}) tx={tx}")
            return tx
        else:
            log_event(f"[FastSnipe] ❌ buy attempt failed for {token} (score={score:.2f})")
            return None
    except Exception as e:
        log_event(f"[FastSnipe] buy exception for {token}: {e}")
        return None

async def _quick_risk(token: str):
    # run fast gates concurrently
    async def _bh(): return is_blacklisted_token(token)
    async def _hp(): return is_honeypot(token)
    async def _lp(): return check_lp_status(token)  # your fn returns True if unlocked; adapt if needed

    blk, honeypot, lp_unlocked = await asyncio.gather(_bh(), _hp(), _lp(), return_exceptions=True)
    flags = []
    if blk is True: flags.append("blacklisted")
    if honeypot is True: flags.append("honeypot")
    if lp_unlocked is True: flags.append("lp_unlocked")
    ok = len(flags) == 0
    return ok, flags
