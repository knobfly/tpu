import asyncio
import logging
from datetime import datetime

from cortex.meta_cortex import MetaCortex
from cortex.txn_cortex import TxnCortex
from exec.magic_eden_connector import buy_nft_me, list_nft_me
from inputs.meta_data.token_metadata_fetcher import fetch_token_metadata
from inputs.nft.nft_meta_enricher import enrich_nft_metadata
from inputs.nft.opensea_metadata import fetch_opensea_metadata
from inputs.wallet.wallet_core import WalletManager
from librarian.data_librarian import librarian
from special.insight_logger import log_scanner_insight
from utils.logger import log_event
from utils.service_status import update_status

SEEN_MINTS = set()

# Thresholds
NFT_AUTO_BUY_CONFIDENCE = 0.75
NFT_MIN_SCORE = 7.0

meta_cortex = MetaCortex()
txn_cortex = TxnCortex()

async def maybe_auto_buy_nft(mint: str, wallet: WalletManager = None, source: str = "nft_auto_buyer"):
    update_status("nft_auto_buyer")
    if mint in SEEN_MINTS:
        return
    SEEN_MINTS.add(mint)

    try:
        nft_meta = await enrich_nft_metadata(mint)
        if not nft_meta:
            enriched = await fetch_opensea_metadata(mint)
            if not enriched:
                enriched = await fetch_token_metadata(mint)
            if not enriched:
                log_event(f"[NFTAutoBuy] ⚠️ No metadata found for {mint}")
                return
        else:
            enriched = nft_meta

        enriched["type"] = "nft"
        enriched["source"] = source

        score = await meta_cortex.score_token(enriched)
        confidence = score / 10

        enriched["score"] = round(score, 3)
        enriched["confidence"] = round(confidence, 3)

        collection = enriched.get("collection", "").lower().replace(" ", "_")
        tags = [f"collection::{collection}"] if collection else []

        log_scanner_insight(source, mint, {
            "score": score,
            "confidence": confidence,
            "collection": collection,
            "timestamp": datetime.utcnow().isoformat()
        })

        if confidence < NFT_AUTO_BUY_CONFIDENCE:
            log_event(f"[NFTAutoBuy] ⛔ Skipped {mint} | Confidence: {confidence}")
            return

        if score < NFT_MIN_SCORE:
            log_event(f"[NFTAutoBuy] ❌ Skipped {mint} | Score too low: {score}")
            return

        log_event(f"[NFTAutoBuy] ✅ Buying NFT {mint} | Score: {score} | Confidence: {confidence}")

        # === Execute Buy
        if wallet:
            success = await buy_nft_me(wallet, mint)
            if not success:
                log_event(f"[NFTAutoBuy] ❌ Buy failed for {mint}")
                return
        else:
            log_event(f"[NFTAutoBuy] 🛑 No wallet provided for buy")
            return

        # === Auto-list NFT (if traits allow)
        price_floor = nft_meta.get("floorPrice", 0.1)
        list_price = round(price_floor * 1.5, 3)
        listed = await list_nft_me(wallet, mint, list_price)

        if not listed:
            log_event(f"⚠️ Auto-list failed for {mint} at {list_price} SOL")

        # === Record & Learn
        await librarian.record({
            "type": "nft",
            "mint": mint,
            "name": enriched.get("name"),
            "symbol": enriched.get("symbol", ""),
            "price_sol": enriched.get("price_sol", 0),
            "score": score,
            "confidence": confidence,
            "source": source,
            "tags": tags,
            "timestamp": datetime.utcnow().isoformat()
        })

        # === Summary Message (Optional)
        summary = (
            f"🎨 *NFT Auto-Buy Triggered!*\n"
            f"🏷 Name: `{enriched.get('name')}`\n"
            f"🧠 Score: `{score}`\n"
            f"⚖️ Confidence: `{confidence}`\n"
            f"💸 Price: `{enriched.get('price_sol', 0)} SOL`\n"
            f"📦 Collection: `{collection}`\n"
            f"🔗 Mint: `{mint}`"
        )
        await txn_cortex.send_summary(summary)

    except Exception as e:
        logging.warning(f"[NFTAutoBuy] Error evaluating {mint}: {e}")
