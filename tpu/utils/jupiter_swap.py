import base64
import logging

import aiohttp
from solana.rpc.async_api import AsyncClient
from solana.transaction import Transaction
from solders.signature import Signature
from utils.rpc_loader import get_active_rpc, report_rpc_failure


class JupiterSwap:
    def __init__(self, session: aiohttp.ClientSession, wallet, logger=None):
        self.session = session
        self.wallet = wallet
        self.logger = logger

    async def fetch_swap_tx(self, input_mint: str, output_mint: str, amount: int, user_pubkey: str, slippage: float = 1.0) -> str:
        url = "https://quote-api.jup.ag/v6/swap"
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount,
            "slippageBps": int(slippage * 100),
            "userPublicKey": user_pubkey,
            "wrapUnwrapSOL": True,
            "feeBps": 0
        }
        async with self.session.get(url, params=params) as resp:
            if resp.status != 200:
                raise Exception(f"❌ Jupiter quote error: {await resp.text()}")
            data = await resp.json()
            return data["swapTransaction"]

    async def execute_swap(self, swap_tx_b64: str) -> str:
        swap_tx_bytes = base64.b64decode(swap_tx_b64)
        tx = Transaction.deserialize(swap_tx_bytes)

        try:
            tx.sign(self.wallet)
        except Exception as e:
            logging.error(f"❌ Failed to sign transaction: {e}")
            return None

        rpc_url = get_active_rpc()
        try:
            async with AsyncClient(rpc_url) as client:
                response = await client.send_transaction(tx, self.wallet)
                sig = response.value
                if self.logger:
                    self.logger.info(f"✅ Swap sent: https://solscan.io/tx/{sig}")
                return sig
        except Exception as e:
            report_rpc_failure(rpc_url)
            if self.logger:
                self.logger.error(f"❌ Swap failed on active RPC: {e}")
            return None

    async def buy_token(self, token_mint: str, amount: float) -> str:
        sol_mint = "So11111111111111111111111111111111111111112"
        lamports = int(amount * 1_000_000_000)
        tx_b64 = await self.fetch_swap_tx(sol_mint, token_mint, lamports, str(self.wallet.public_key))
        return await self.execute_swap(tx_b64)

    async def sell_token(self, token_mint: str, token_amount: float) -> str:
        sol_mint = "So11111111111111111111111111111111111111112"
        lamports = int(token_amount)
        tx_b64 = await self.fetch_swap_tx(token_mint, sol_mint, lamports, str(self.wallet.public_key))
        return await self.execute_swap(tx_b64)

    async def close(self):
        try:
            await self.session.close()
        except Exception as e:
            logging.warning(f"[JupiterSwap] session close failed: {e}")
