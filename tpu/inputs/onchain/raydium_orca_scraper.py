import asyncio
import logging
from typing import List, Optional

import aiohttp
from bs4 import BeautifulSoup
from cortex.txn_cortex import register_buy
from cortex.meta_cortex import assess_confidence
from librarian.data_librarian import librarian
from scoring.trade_score_engine import evaluate_trade
from utils.logger import log_event
from utils.rpc_loader import get_active_rpc
from utils.service_status import update_status
from inputs.meta_data.token_metadata import parse_token_metadata
from utils.tx_utils import build_swap_instruction
from utils.tx_utils import sign_and_send_tx

RAYDIUM_URL = "https://raydium.io/pools"
ORCA_URL = "https://www.orca.so/pools"
CHECK_INTERVAL = 60  # seconds

class RaydiumOrcaScraper:
    def __init__(self):
        self.seen_tokens = set()

    async def run(self):
        log_event("🌊 Raydium/Orca scraper started.")
        while True:
            try:
                await self.scrape_all()
            except Exception as e:
                logging.warning(f"[Scraper] ❌ Unexpected error: {e}")
            await asyncio.sleep(CHECK_INTERVAL)

    async def scrape_all(self):
        update_status("raydium_orca_scraper")
        tokens = []

        raydium_tokens = await self.scrape_raydium()
        orca_tokens = await self.scrape_orca()

        logging.info(f"[Raydium] ✅ Found {len(raydium_tokens)} tokens")
        logging.info(f"[Orca] ✅ Found {len(orca_tokens)} tokens")

        tokens.extend(raydium_tokens)
        tokens.extend(orca_tokens)
        for token in tokens:
            if token in self.seen_tokens:
                continue
            self.seen_tokens.add(token)
            await self.handle_token(token)

    async def scrape_raydium(self) -> List[str]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(RAYDIUM_URL, timeout=10) as resp:
                    html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")
                    links = soup.find_all("a")
                    return [link.get("href").split("/")[-1] for link in links if "/token/" in str(link)]
        except Exception as e:
            logging.warning(f"[Raydium] ⚠️ Scrape failed: {e}")
            return []

    async def scrape_orca(self) -> List[str]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(ORCA_URL, timeout=10) as resp:
                    html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")
                    links = soup.find_all("a")
                    return [link.get("href").split("/")[-1] for link in links if "/token/" in str(link)]
        except Exception as e:
            logging.warning(f"[Orca] ⚠️ Scrape failed: {e}")
            return []

    async def handle_token(self, token_address: str):
        try:
            metadata = await parse_token_metadata(token_address)
            if not metadata:
                return

            score = await evaluate_trade(token_address, metadata)
            confidence = assess_confidence(token_address, metadata, score)

            if confidence >= 7.0:
                librarian.record_scanner_source(token_address, "raydium_orca")
                await register_buy(
                    mint=token_address,
                    metadata=metadata,
                    ai_score=score,
                    source="raydium_orca"
                )
                log_event(f"🌊 Auto-buy triggered via Raydium/Orca for {token_address} → Score: {score:.1f}, Confidence: {confidence:.1f}")
            else:
                log_event(f"🌊 Token {token_address} ignored — low confidence {confidence:.1f}")
        except Exception as e:
            logging.warning(f"[Scraper] ⚠️ Failed to process {token_address}: {e}")


# === Direct AMM Snipe Functions ===

async def build_direct_swap_tx(token_address: str, amount_sol: float, wallet) -> Optional[dict]:
    try:
        rpc = get_active_rpc()
        swap_ix = await build_swap_instruction(wallet, token_address, amount_sol, rpc)
        if not swap_ix:
            logging.warning(f"[AMM Snipe] No swap instruction for {token_address}")
            return None

        tx_data = {
            "instructions": swap_ix,
            "signer": wallet,
            "raw_tx": await wallet.sign_transaction(swap_ix)
        }
        logging.info(f"[AMM Snipe] Built direct swap TX for {token_address} amount {amount_sol} SOL")
        return tx_data
    except Exception as e:
        logging.warning(f"[AMM Snipe] Failed to build TX: {e}")
        return None


async def send_direct_swap_tx(tx_data: dict) -> Optional[str]:
    try:
        sig = await sign_and_send_tx(tx_data["raw_tx"])
        if sig:
            log_event(f"[AMM Snipe] TX sent: {sig}")
        return sig
    except Exception as e:
        logging.warning(f"[AMM Snipe] Send failed: {e}")
        return None
