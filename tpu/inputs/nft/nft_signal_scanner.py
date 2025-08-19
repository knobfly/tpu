import asyncio
import logging
from datetime import datetime

from inputs.meta_data.token_metadata_fetcher import fetch_token_metadata
from inputs.nft.opensea_metadata import fetch_opensea_metadata
from librarian.data_librarian import librarian
from special.insight_logger import log_scanner_insight
from utils.logger import log_event
from utils.service_status import update_status

NFT_KEYWORDS = {"nft", "mint", "collection", "art", "1/1", "creator", "pfp"}
SEEN_NFTS = set()
SCAN_INTERVAL = 10  # just to keep task alive


async def nft_event_handler(event: dict):
    """
    Handle NFT mints coming from the Firehose router.
    """
    try:
        etype = event.get("event_type", "")
        desc = (event.get("description") or "").lower()

        if etype != "nft_mint" and not any(k in desc for k in NFT_KEYWORDS):
            return

        token = event.get("token_address") or event.get("mint") or event.get("token")
        if not token or token in SEEN_NFTS:
            return
        SEEN_NFTS.add(token)

        log_event(f"🎨 Firehose NFT Event: {token} — {desc}")
        now = datetime.utcnow().isoformat()

        # === Fetch full metadata
        metadata = await fetch_token_metadata(token) or {}
        enriched = await fetch_opensea_metadata(token) or {}
        metadata.update(enriched)

        if enriched.get("verified"):
            try:
                librarian.tag(token, "verified_opensea")
            except Exception as e:
                logging.debug(f"[NFTScanner] tag_token failed: {e}")

        # === Score token using librarian
        try:
            score = await librarian.score_token({
                "token": token,
                "type": "nft",
                "metadata": metadata,
                "timestamp": now
            })
        except Exception as e:
            logging.warning(f"[NFTScanner] scoring failed: {e}")
            score = 0

        log_scanner_insight("nft_signal", {
            "token": token,
            "name": metadata.get("name", "Unknown"),
            "symbol": metadata.get("symbol", ""),
            "score": score,
            "timestamp": now
        })

        log_event(f"🖼 NFT Detected: {metadata.get('name', token)} | Score: {score:.2f}")

        # === Auto-buy logic
        try:
            if score >= 0.8 and getattr(ai_brain, "should_auto_buy_nft", lambda *_: False)(token):
                from inputs.wallet.wallet_core import get_best_wallet
                wallet = await get_best_wallet()
                if wallet:
                    await wallet.maybe_auto_buy_nft({
                        "mint": token,
                        "name": metadata.get("name", "Unknown NFT"),
                        "price_sol": metadata.get("price", 0),
                        "ai_score": score
                    })
        except Exception as e:
            logging.debug(f"[NFTScanner] Auto-buy failed: {e}")

    except Exception as e:
        logging.warning(f"[NFTScanner] Error handling NFT event: {e}")


async def run_nft_signal_scanner():
    log_event("🎨 NFT Signal Scanner (Firehose) started.")
    update_status("nft_signal_scanner")
    while True:
        await asyncio.sleep(SCAN_INTERVAL)
