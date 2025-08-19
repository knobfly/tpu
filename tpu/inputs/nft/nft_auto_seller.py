import asyncio
import logging
from datetime import datetime

from core.magic_eden_connector import list_nft_me
from cortex.meta_cortex import score_token
from cortex.wallet_cortex import estimate_confidence
from inputs.meta_data.token_metadata_fetcher import fetch_token_metadata
from librarian.data_librarian import librarian
from special.insight_logger import log_scanner_insight
from strategy.strategy_memory import record_result
from utils.logger import log_event
from utils.service_status import update_status
from utils.telegram_bridge import send_telegram_message

LISTING_CONFIDENCE_THRESHOLD = 0.65
PRICE_MULTIPLIER = 1.15  # List 15% above floor by default

async def maybe_list_nft_for_sale(mint: str, wallet=None, source="nft_auto_seller"):
    update_status("nft_auto_seller")

    try:
        meta = await fetch_token_metadata(mint)
        if not meta:
            log_event(f"[NFTAutoSell] ⚠️ No metadata for {mint}")
            return

        meta["type"] = "nft"
        meta["source"] = source

        confidence = estimate_confidence(mint, meta)
        score = await score_token(mint, meta)
        floor_price = meta.get("floor_price", 0.0)
        list_price = round(floor_price * PRICE_MULTIPLIER, 3)

        if confidence < LISTING_CONFIDENCE_THRESHOLD:
            log_event(f"[NFTAutoSell] ⏸️ Holding {mint} | Confidence too high: {confidence:.2f}")
            return

        success = await list_nft_me(mint=mint, price_sol=list_price, wallet=wallet)
        if not success:
            log_event(f"[NFTAutoSell] ❌ Magic Eden listing failed for {mint}")
            return

        await send_telegram_message(
            f"🎨 *NFT Listed on Magic Eden*\n"
            f"🏷 Name: `{meta.get('name', 'Unknown')}`\n"
            f"💸 Listed At: `{list_price} SOL`\n"
            f"⚖️ Confidence: `{confidence:.2f}`\n"
            f"🔗 Mint: `{mint}`"
        )

        record_result({
            "type": "nft_sale",
            "mint": mint,
            "price_sol": list_price,
            "confidence": confidence,
            "source": source,
            "tags": ["listed_for_sale"],
            "timestamp": datetime.utcnow().isoformat()
        })

        log_scanner_insight(source, {
            "mint": mint,
            "name": meta.get("name"),
            "confidence": confidence,
            "score": score,
            "list_price": list_price,
            "floor": floor_price,
            "timestamp": datetime.utcnow().isoformat()
        })

        log_event(f"[NFTAutoSell] ✅ Listed {mint} on ME at {list_price} SOL")

    except Exception as e:
        logging.warning(f"[NFTAutoSell] Error for {mint}: {e}")
